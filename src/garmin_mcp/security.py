"""OAuth resource server: Auth0 owns authorization-code/PKCE and token issuance.

Never use Garmin session tokens as MCP credentials. This is a single-owner
service: a correctly signed token from another tenant user is still forbidden.
"""
from __future__ import annotations

import asyncio
import hmac
import os
import threading
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

import jwt
from garmin_mcp.observability import log_event
from starlette.responses import JSONResponse

REQUIRED_SCOPE = "garmin:read"
METADATA_PATH = "/.well-known/oauth-protected-resource"


def _https_url(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment
            or parsed.port not in (None, 443)
            or any(c.isspace() or c in '\\"<>' for c in value)):
        raise ValueError(f"{name} must be a canonical HTTPS URL")
    return value


@dataclass(frozen=True)
class OAuthSettings:
    issuer: str = ""
    audience: str = ""
    jwks_url: str = ""
    resource_url: str = ""
    allowed_subject: str = ""
    configuration_error: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "OAuthSettings":
        env = os.environ if env is None else env
        resource = env.get("MCP_RESOURCE_URL", "").strip()
        try:
            if resource:
                _https_url(resource, "MCP_RESOURCE_URL")
                if urlsplit(resource).path != "/mcp":
                    raise ValueError("MCP_RESOURCE_URL must end with /mcp")
            if env.get("AUTH_MODE", "oauth") != "oauth":
                raise ValueError("Remote MCP requires OAuth")
            domain = env.get("AUTH0_DOMAIN", "").strip()
            issuer = env.get("AUTH0_ISSUER", "").strip() or (f"https://{domain}/" if domain else "")
            audience = env.get("AUTH0_AUDIENCE", "").strip()
            jwks = env.get("AUTH0_JWKS_URL", "").strip() or (issuer + ".well-known/jwks.json" if issuer else "")
            subject = env.get("AUTH0_ALLOWED_SUBJECT", "").strip()
            if not all((issuer, audience, jwks, resource, subject)):
                return cls(resource_url=resource, configuration_error=True)
            _https_url(issuer, "AUTH0_ISSUER")
            _https_url(jwks, "AUTH0_JWKS_URL")
            if urlsplit(issuer).path != "/":
                raise ValueError("Auth0 issuer must include its trailing slash")
            if domain and domain != urlsplit(issuer).netloc:
                raise ValueError("Auth0 domain and issuer must agree")
            if urlsplit(issuer).netloc != urlsplit(jwks).netloc:
                raise ValueError("JWKS must belong to the configured issuer")
            if audience != resource:
                raise ValueError("AUTH0_AUDIENCE must equal MCP_RESOURCE_URL")
            return cls(issuer, audience, jwks, resource, subject)
        except ValueError:
            # Do not reflect invalid environment values into headers or logs.
            return cls(configuration_error=True)

    @property
    def ready(self) -> bool:
        return not self.configuration_error and all((self.issuer, self.audience, self.jwks_url, self.resource_url, self.allowed_subject))

    @property
    def metadata_url(self) -> str:
        if not self.resource_url:
            return METADATA_PATH
        parsed = urlsplit(self.resource_url)
        return f"{parsed.scheme}://{parsed.netloc}{METADATA_PATH}"

    def metadata(self) -> dict:
        return {
            "resource": self.resource_url,
            "authorization_servers": [self.issuer],
            "scopes_supported": [REQUIRED_SCOPE],
            "bearer_methods_supported": ["header"],
            "resource_name": "Garmin Wellness",
        }


class AuthorizationFailure(Exception):
    """Deliberately carries no token, claims or upstream error message."""
    def __init__(self, status: int = 401, error: str = "invalid_token"):
        self.status = status
        self.error = error
        super().__init__(error)


class _RateLimitedJWKClient(jwt.PyJWKClient):
    """Prevent random key IDs from causing an outbound request on every call."""
    def __init__(self, uri: str):
        super().__init__(uri, timeout=5, lifespan=300)
        self._last_fetch = float("-inf")

    def fetch_data(self):
        now = time.monotonic()
        if now - self._last_fetch < 30:
            raise jwt.PyJWKClientError("JWKS refresh cooldown")
        self._last_fetch = now
        return super().fetch_data()


class JWTVerifier:
    def __init__(self, settings: OAuthSettings, jwks_client=None):
        self.settings = settings
        self.jwks_client = jwks_client or _RateLimitedJWKClient(settings.jwks_url)
        self._lock = threading.Lock()

    def verify(self, token: str) -> dict:
        if not self.settings.ready or not token or len(token) > 16384:
            raise AuthorizationFailure()
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise AuthorizationFailure()
            # Synchronize cache refreshes; token-supplied jku/x5u are never used.
            with self._lock:
                key = self.jwks_client.get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token, key, algorithms=["RS256"],
                issuer=self.settings.issuer, audience=self.settings.audience,
                options={"require": ["exp", "iss", "aud", "sub"], "verify_nbf": True},
                leeway=0,
            )
        except AuthorizationFailure:
            raise
        except (jwt.PyJWTError, ValueError, TypeError, KeyError, OverflowError):
            raise AuthorizationFailure() from None
        subject = claims.get("sub")
        if not isinstance(subject, str) or not hmac.compare_digest(subject.encode(), self.settings.allowed_subject.encode()):
            raise AuthorizationFailure(403, "access_denied")
        scope = claims.get("scope", "")
        if not isinstance(scope, str) or REQUIRED_SCOPE not in scope.split():
            raise AuthorizationFailure(403, "insufficient_scope")
        return claims


class OAuthMiddleware:
    """Wrap the entire HTTP app, including redirects and all MCP methods.

    Only health and discovery are public. Incomplete bootstrap configuration
    serves health normally, discovery as 503, and all other routes as 401.
    """
    def __init__(self, app, settings: OAuthSettings | None = None, verifier=None):
        self.app = app
        self.settings = settings or OAuthSettings.from_env()
        self.verifier = verifier or (JWTVerifier(self.settings) if self.settings.ready else None)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == "/healthz" and scope["method"] in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        if path in (METADATA_PATH, METADATA_PATH + "/mcp") and scope["method"] in {"GET", "HEAD"}:
            response = JSONResponse(self.settings.metadata() if self.settings.ready else {"error": "temporarily_unavailable"},
                                    status_code=200 if self.settings.ready else 503,
                                    headers={"Cache-Control": "no-store"})
            await response(scope, receive, send)
            return
        headers = [v for k, v in scope.get("headers", []) if k.lower() == b"authorization"]
        error = AuthorizationFailure()
        if self.verifier and len(headers) == 1:
            try:
                parts = headers[0].decode("ascii").split()
                if len(parts) != 2 or parts[0].lower() != "bearer":
                    raise AuthorizationFailure()
                # Network-bound JWKS lookup cannot block the ASGI event loop.
                claims = await asyncio.to_thread(self.verifier.verify, parts[1])
                scope.setdefault("state", {})["oauth_subject"] = claims["sub"]
            except (AuthorizationFailure, UnicodeDecodeError) as exc:
                error = exc if isinstance(exc, AuthorizationFailure) else error
            else:
                log_event("auth", status="success")
                await self.app(scope, receive, send)
                return
        log_event("auth", status="denied", error_type=error.error)
        challenge = (f'Bearer resource_metadata="{self.settings.metadata_url}", '
                     f'scope="{REQUIRED_SCOPE}", error="{error.error}"')
        await JSONResponse({"error": error.error}, status_code=error.status,
                           headers={"WWW-Authenticate": challenge, "Cache-Control": "no-store"})(scope, receive, send)
