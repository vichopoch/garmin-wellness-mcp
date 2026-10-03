"""Lazy token-only Garmin client with bounded, serialized retries and persistence."""
import os
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from garminconnect import Garmin, GarminConnectAuthenticationError, GarminConnectTooManyRequestsError, GarminConnectConnectionError
from garmin_mcp.token_utils import secure_token_dir

# Deliberate narrower boundary than the upstream registry; no raw HTTP escape hatch.
WELLNESS_READ_METHODS = frozenset({'get_stats','get_sleep_data','get_hrv_data','get_body_battery','get_stress_data','get_training_readiness','get_training_status','get_max_metrics','get_spo2_data','get_respiration_data','get_user_profile','get_activities','get_activity','get_endurance_score','get_hill_score','get_lactate_threshold','get_fitnessage_data'})

class GarminUnavailable(RuntimeError):
    pass

class GarminAuthenticationRequired(GarminUnavailable):
    pass

class GarminTooManyRequests(GarminUnavailable):
    pass


def error_response(exc):
    """SDK may preserve HTTP response on the causal exception rather than wrapper."""
    for _ in range(5):
        response = getattr(exc, "response", None)
        if response is not None:
            return response
        exc = getattr(exc, "__cause__", None)
        if exc is None:
            break
    return None


class TokenClient:
    def __init__(self, token_path=None, factory=Garmin):
        self.token_path = token_path or os.getenv('GARMINTOKENS', '/data/garmin')
        self._factory = factory
        self._api = None
        self._lock = threading.RLock()
        self._cooldown = 0.0
        self.last_success = None

    def ready(self):
        return (Path(self.token_path)/'garmin_tokens.json').is_file()

    def _login(self):
        if not self.ready():
            raise GarminAuthenticationRequired('Garmin tokens missing. Run garmin-mcp-auth in the private Railway SSH terminal.')
        secure_token_dir(self.token_path)
        api = self._factory(retry_attempts=0, is_cn=os.getenv('GARMIN_IS_CN','false') == 'true')
        try:
            api.login(self.token_path)
        except Exception:
            raise GarminAuthenticationRequired('Garmin token login failed. Run garmin-mcp-auth --verify; reauthenticate if expired.') from None
        self._api = api

    def __getattr__(self, name):
        if name not in WELLNESS_READ_METHODS:
            raise AttributeError('Method is not in the reviewed read-only Garmin boundary')
        from garmin_mcp.read_only import validate_read_method
        validate_read_method(name)
        def call(*args, **kwargs):
            with self._lock:
                if time.monotonic() < self._cooldown:
                    raise GarminTooManyRequests('Garmin is cooling down after rate limiting; try later.')
                if self._api is None:
                    self._login()
                for attempt in range(3):
                    try:
                        result = getattr(self._api, name)(*args, **kwargs)
                        self._api.client.dump(self.token_path)
                        secure_token_dir(self.token_path)
                        self.last_success = datetime.now(timezone.utc).isoformat()
                        return result
                    except GarminConnectAuthenticationError:
                        self._api = None
                        raise GarminAuthenticationRequired('Garmin tokens expired. Reauthenticate using the private terminal.') from None
                    except Exception as exc:
                        response = error_response(exc)
                        status = getattr(response, 'status_code', None)
                        if status in (401,403):
                            self._api = None
                            raise GarminAuthenticationRequired('Garmin rejected the session. Reauthenticate using the private terminal.') from None
                        rate_limited = status == 429 or isinstance(exc, GarminConnectTooManyRequestsError)
                        transient = rate_limited or (status is not None and status >= 500) or (status is None and isinstance(exc, (GarminConnectConnectionError, TimeoutError, ConnectionError)))
                        delay = 2 ** attempt
                        if rate_limited:
                            value = getattr(response,'headers',{}).get('Retry-After', '60')
                            try:
                                delay = max(delay,float(value))
                            except ValueError:
                                try: delay = max(delay,(parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds())
                                except (ValueError,TypeError): delay = 60
                            self._cooldown = time.monotonic() + delay
                            # Long Retry-After is honored by refusing calls, not holding MCP open.
                            if delay > 10:
                                raise GarminTooManyRequests('Garmin rate limited requests. Respecting Retry-After; retry later.') from None
                        if rate_limited and attempt == 2:
                            raise GarminTooManyRequests('Garmin rate limited requests. Respecting Retry-After; retry later.') from None
                        if not transient or attempt == 2:
                            raise GarminUnavailable('Garmin metric unavailable; retry later or verify account/device support.') from None
                        time.sleep(delay)
        return call
