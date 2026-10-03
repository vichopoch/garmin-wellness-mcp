"""Loopback-only integration harness with synthetic Garmin data and ephemeral JWTs.

This module is never imported by the production runtime. The generated RSA key
and its token exist only for this local process; the service still verifies JWTs.
"""
from contextlib import contextmanager
import os
import socket
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa

from garmin_mcp.runtime import build_app
from garmin_mcp.security import JWTVerifier, OAuthSettings


class FakeGarmin:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        responses = {
            "get_user_profile": {"timeZone": "America/Santiago"},
            "get_stats": {"totalSteps": 4321, "restingHeartRate": 52, "bodyBatteryHighestValue": 89,
                          "bodyBatteryLowestValue": 42, "averageStressLevel": 24},
            "get_sleep_data": {"dailySleepDTO": {"sleepTimeSeconds": 27000, "deepSleepSeconds": 5400,
                                "sleepScores": {"overall": {"value": 85}}, "napTimeSeconds": 0},
                               "avgOvernightHrv": 55},
            "get_hrv_data": {"hrvSummary": {"lastNightAvg": 55, "weeklyAvg": 53,
                             "baseline": {"balancedLow": 44, "balancedUpper": 62}, "status": "BALANCED"}},
            "get_body_battery": [{"bodyBatteryMostRecentValue": 77, "chargedValue": 62, "drainedValue": 13}],
            "get_stress_data": {"avgStressLevel": 24, "maxStressLevel": 55},
            "get_training_readiness": [{"score": 79, "recoveryTime": 120}],
            "get_training_status": {"mostRecentTrainingStatus": {"latestTrainingStatusData": {
                "fixture": {"trainingStatus": 2, "acuteTrainingLoadDTO": {"dailyTrainingLoadAcute": 430}}}}},
            "get_max_metrics": [{"generic": {"vo2MaxPreciseValue": 48.5}}],
            "get_spo2_data": {"averageSpO2": 97},
            "get_respiration_data": {"avgWakingRespirationValue": 14},
            "get_activities": [{"activityId": 123, "activityType": {"typeKey": "running"},
                                "distance": 5000, "duration": 1800, "averageHR": 140}],
            "get_activity": {"activityId": 123, "activityType": {"typeKey": "running"},
                             "summaryDTO": {"distance": 5000, "duration": 1800}},
        }
        if name not in responses:
            raise AttributeError(name)
        def call(*args, **kwargs):
            self.calls.append((name, args))
            return responses[name]
        return call


@contextmanager
def running_runtime():
    """Start the real production app on an OS-assigned loopback port."""
    settings = OAuthSettings.from_env({
        "AUTH0_DOMAIN": "integration.auth0.com", "AUTH0_ALLOWED_SUBJECT": "auth0|integration-owner",
        "AUTH0_AUDIENCE": "https://integration.example/mcp", "MCP_RESOURCE_URL": "https://integration.example/mcp",
    })
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({"iss": settings.issuer, "aud": settings.audience, "sub": settings.allowed_subject,
                        "scope": "garmin:read", "exp": int(time.time()) + 900}, key,
                       algorithm="RS256", headers={"kid": "ephemeral-local-test-key"})
    class LocalJWKS:
        def get_signing_key_from_jwt(self, _token):
            return SimpleNamespace(key=key.public_key())
    fake = FakeGarmin()
    with patch.dict(os.environ, {"GARMIN_READ_ONLY": "true", "AUTH_MODE": "oauth",
                                "CHATGPT_TOOLSET": "wellness", "GARMIN_SYNC_ENABLED": "false",
                                "GARMIN_ENABLED_TOOLS": "", "GARMIN_DISABLED_TOOLS": ""}):
        app, mcp, service = build_app(client=fake, settings=settings, identifier="garmin-profile-integration-fixture")
    app.verifier = JWTVerifier(settings, LocalJWKS())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical",
                                          access_log=False, log_config=None))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Local integration server did not start")
            time.sleep(0.01)
        yield SimpleNamespace(url=f"http://127.0.0.1:{port}/mcp", token=token, fake=fake,
                              app=app, mcp=mcp, service=service)
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        if thread.is_alive():
            raise RuntimeError("Local integration server did not stop")
