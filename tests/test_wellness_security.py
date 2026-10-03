import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from garmin_mcp.security import OAuthSettings, OAuthMiddleware, JWTVerifier, AuthorizationFailure
from garmin_mcp.observability import PrivateJSONFormatter


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def env():
    return {"AUTH0_DOMAIN": "tenant.auth0.com", "AUTH0_AUDIENCE": "https://wellness.example/mcp",
            "MCP_RESOURCE_URL": "https://wellness.example/mcp", "AUTH0_ALLOWED_SUBJECT": "auth0|owner"}


@pytest.fixture
def settings(env):
    return OAuthSettings.from_env(env)


@pytest.fixture
def claims(settings):
    return {"iss": settings.issuer, "aud": settings.audience, "exp": int(time.time()) + 300,
            "nbf": int(time.time()) - 5, "sub": settings.allowed_subject, "scope": "garmin:read"}


def token(key, claims):
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


def verifier(settings, key):
    client = Mock()
    client.get_signing_key_from_jwt.return_value = SimpleNamespace(key=key.public_key())
    return JWTVerifier(settings, client)


def app(settings, verifier=None):
    async def endpoint(request):
        return JSONResponse({"status": "ok"})
    return TestClient(OAuthMiddleware(Starlette(routes=[
        Route("/healthz", endpoint), Route("/mcp", endpoint, methods=["GET", "POST", "DELETE"]),
    ]), settings=settings, verifier=verifier))


def test_settings(settings):
    assert settings.ready
    assert settings.issuer == "https://tenant.auth0.com/"
    assert settings.jwks_url == "https://tenant.auth0.com/.well-known/jwks.json"
    assert settings.metadata_url == "https://wellness.example/.well-known/oauth-protected-resource"


@pytest.mark.parametrize("field,value", [
    ("AUTH_MODE", "none"), ("AUTH0_ALLOWED_SUBJECT", ""), ("AUTH0_AUDIENCE", "wrong"),
    ("MCP_RESOURCE_URL", "http://example/mcp"), ("MCP_RESOURCE_URL", "https://example/"),
    ("AUTH0_ISSUER", "https://other.auth0.com/"), ("AUTH0_JWKS_URL", "https://evil.example/keys"),
    ("AUTH0_JWKS_URL", "http://tenant.auth0.com/keys"),
    ("MCP_RESOURCE_URL", 'https://wellness.example/mcp\"\r\nHeader: bad'),
])
def test_invalid_config_locks_service(env, field, value):
    env[field] = value
    config = OAuthSettings.from_env(env)
    assert not config.ready
    client = app(config)
    assert client.get("/healthz").status_code == 200
    assert client.post("/mcp").status_code == 401


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "PUT", "OPTIONS", "HEAD", "PATCH"])
@pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/anything", "/sse", "/messages/"])
def test_every_transport_route_requires_auth(settings, method, path):
    response = app(settings).request(method, path, follow_redirects=False)
    assert response.status_code == 401
    assert 'resource_metadata="https://wellness.example/.well-known/oauth-protected-resource"' in response.headers["www-authenticate"]
    assert response.headers["cache-control"] == "no-store"


def test_locked_bootstrap():
    client = app(OAuthSettings.from_env({}))
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.post("/mcp").status_code == 401
    assert client.get("/.well-known/oauth-protected-resource").status_code == 503


@pytest.mark.parametrize("path", ["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"])
def test_discovery(settings, path):
    response = app(settings).get(path)
    assert response.status_code == 200
    assert response.json()["resource"] == settings.audience
    assert response.json()["authorization_servers"] == [settings.issuer]
    assert response.json()["scopes_supported"] == ["garmin:read"]


def test_valid_token_reaches_app(key, settings, claims):
    response = app(settings, verifier(settings, key)).post("/mcp", headers={"Authorization": "Bearer " + token(key, claims)})
    assert response.status_code == 200


@pytest.mark.parametrize("change,status", [
    ({"exp": 1}, 401), ({"nbf": 9999999999}, 401), ({"iss": "https://evil.example/"}, 401),
    ({"aud": "wrong"}, 401), ({"scope": "openid"}, 403), ({"scope": ["garmin:read"]}, 403),
    ({"sub": "auth0|someone-else"}, 403), ({"scope": "garmin:read:write"}, 403),
])
def test_invalid_claims_rejected(key, settings, claims, change, status):
    claims.update(change)
    response = app(settings, verifier(settings, key)).post("/mcp", headers={"Authorization": "Bearer " + token(key, claims)})
    assert response.status_code == status
    assert "www-authenticate" in response.headers


@pytest.mark.parametrize("claim", ["exp", "iss", "aud", "sub"])
def test_required_claims(key, settings, claims, claim):
    del claims[claim]
    with pytest.raises(AuthorizationFailure):
        verifier(settings, key).verify(token(key, claims))


def test_wrong_signature(key, settings, claims):
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(AuthorizationFailure):
        verifier(settings, key).verify(token(other_key, claims))


@pytest.mark.parametrize("authorization", ["Bearer rubbish", "Basic aGVsbG8=", "Bearer", "Bearer one two"])
def test_bad_headers(key, settings, authorization):
    assert app(settings, verifier(settings, key)).get("/mcp", headers={"Authorization": authorization}).status_code == 401


def test_duplicate_authorization(key, settings, claims):
    auth = "Bearer " + token(key, claims)
    assert app(settings, verifier(settings, key)).post("/mcp", headers=[("Authorization", auth), ("Authorization", auth)]).status_code == 401


@pytest.mark.parametrize("algorithm", ["none", "HS256"])
def test_algorithm_confusion(key, settings, claims, algorithm):
    bad = jwt.encode(claims, "" if algorithm == "none" else "a" * 32, algorithm=algorithm, headers={"kid": "test-key"})
    with pytest.raises(AuthorizationFailure):
        verifier(settings, key).verify(bad)


def test_jwks_unavailable(key, settings, claims):
    check = verifier(settings, key)
    check.jwks_client.get_signing_key_from_jwt.side_effect = jwt.PyJWKClientConnectionError("contains secrets")
    with pytest.raises(AuthorizationFailure) as exc:
        check.verify(token(key, claims))
    assert str(exc.value) == "invalid_token"


def test_private_logging_discards_payloads_and_exceptions():
    record = logging.LogRecord("garminconnect", logging.ERROR, "", 1,
                               "Authorization: Bearer SECRET email@example.com password MFA HRV=51", (), None)
    record.safe_event = "mcp_tool_call"
    record.safe_fields = {"tool": "get_sleep", "duration_ms": 13, "status": "success",
                          "Authorization": "SECRET", "cookies": "SECRET", "tokens": "SECRET",
                          "email": "email@example.com", "password": "SECRET", "MFA": "123456",
                          "payload": {"HRV": 51}, "result": "sensitive", "request_id": "a" * 32}
    output = PrivateJSONFormatter().format(record)
    assert json.loads(output) == {"event": "mcp_tool_call", "level": "ERROR", "tool": "get_sleep",
                                  "duration_ms": 13, "status": "success", "request_id": "a" * 32}
    assert "SECRET" not in output and "HRV" not in output and "email" not in output


def test_jwks_refresh_is_rate_limited(settings, monkeypatch):
    from garmin_mcp.security import _RateLimitedJWKClient
    fetch = Mock(return_value={"keys": []})
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", fetch)
    client = _RateLimitedJWKClient(settings.jwks_url)
    client.fetch_data()
    with pytest.raises(jwt.PyJWKClientError):
        client.fetch_data()
    assert fetch.call_count == 1
