"""Real HTTP/JSON-RPC checks against production composition with fake Garmin only."""
import json

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.runtime_harness import running_runtime

EXPECTED_TOOLS = {
    "get_profile", "get_capabilities", "get_wellness_today", "get_daily_health", "get_health_range",
    "get_sleep", "get_sleep_analysis", "get_naps", "get_recovery_context", "get_hrv",
    "get_body_battery", "get_stress", "get_training_overview", "get_training_readiness",
    "get_training_status", "get_vo2max", "get_activities", "get_activity", "get_metric_trend",
    "compare_periods", "get_metric_timeseries", "find_correlations",
}


@pytest.fixture(scope="module")
def runtime():
    with running_runtime() as instance:
        yield instance


def rpc(runtime, method, params=None):
    response = httpx.post(runtime.url, headers={"Authorization": "Bearer " + runtime.token,
                          "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"},
                          json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, timeout=15)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "error" not in payload, payload
    return payload["result"]


def call(runtime, name, **arguments):
    result = rpc(runtime, "tools/call", {"name": name, "arguments": arguments})
    assert not result.get("isError"), result
    assert isinstance(result.get("structuredContent"), dict)
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"]


def test_initialize_and_schema_contract(runtime):
    result = rpc(runtime, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                          "clientInfo": {"name": "wellness-test", "version": "1"}})
    assert result["protocolVersion"] == "2025-11-25"
    assert result["serverInfo"]["name"] == "Garmin Wellness"
    tools = rpc(runtime, "tools/list")["tools"]
    assert {tool["name"] for tool in tools} == EXPECTED_TOOLS
    assert len(tools) == 22
    for tool in tools:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["annotations"]["openWorldHint"] is False
        assert tool["outputSchema"]["type"] == "object"
        assert tool["_meta"]["securitySchemes"] == [{"type": "oauth2", "scopes": ["garmin:read"]}]
    profile = next(tool for tool in tools if tool["name"] == "get_profile")
    assert profile["_meta"]["openai/profile"] is True
    assert profile["inputSchema"].get("properties", {}) == {}
    identity = call(runtime, "get_profile")
    assert identity == {"id": "garmin-profile-integration-fixture", "name": "Garmin Wellness"}
    assert call(runtime, "get_profile") == identity


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE", "HEAD", "OPTIONS", "PATCH", "PUT"])
@pytest.mark.parametrize("suffix", ["", "/"])
def test_http_unauthenticated_methods_rejected(runtime, method, suffix):
    result = httpx.request(method, runtime.url + suffix, follow_redirects=False)
    assert result.status_code == 401
    assert 'scope="garmin:read"' in result.headers["www-authenticate"]


def test_health_and_discovery(runtime):
    base = runtime.url.removesuffix("/mcp")
    assert httpx.get(base + "/healthz").json() == {"status": "ok"}
    assert httpx.get(base + "/.well-known/oauth-protected-resource").json()["scopes_supported"] == ["garmin:read"]


@pytest.mark.parametrize("name,field,expected", [
    ("get_sleep", "sleep_score", 85), ("get_hrv", "nightly_avg", 55),
    ("get_body_battery", "latest", 77), ("get_stress", "average", 24),
    ("get_training_overview", "readiness", 79), ("get_vo2max", "vo2max", 48.5),
])
def test_representative_garmin_tools(runtime, name, field, expected):
    result = call(runtime, name, date="2026-09-30")
    assert result["available"] is True
    assert result[field] == expected


def test_today_aggregation_activity_and_missing_metric(runtime):
    result = call(runtime, "get_wellness_today")
    assert result["health"]["steps"] == 4321
    assert result["sleep"]["sleep_hours"] == 7.5
    assert result["hrv"]["nightly_avg"] == 55
    assert result["latest_activity"]["distance_meters"] == 5000
    assert "gps" not in json.dumps(result).lower()
    result = call(runtime, "get_metric_timeseries", metric="sleep_need", start_date="2026-09-29", end_date="2026-09-30")
    assert result["sample_count"] == 0


def test_tool_argument_errors_remain_protocol_errors(runtime):
    result = rpc(runtime, "tools/call", {"name": "get_sleep", "arguments": {"date": "bad-date"}})
    assert result["isError"] is True
    result = rpc(runtime, "tools/call", {"name": "delete_activity", "arguments": {"activity_id": "123"}})
    assert result["isError"] is True


@pytest.mark.asyncio
async def test_real_sdk_streamable_http_session(runtime):
    async with streamablehttp_client(runtime.url, headers={"Authorization": "Bearer " + runtime.token}) as (read, write, _):
        async with ClientSession(read, write) as session:
            result = await session.initialize()
            assert result.serverInfo.name == "Garmin Wellness"
            assert len((await session.list_tools()).tools) == 22
            called = await session.call_tool("get_daily_health", {"date": "2026-09-30"})
            assert not called.isError
            assert called.structuredContent["sleep"]["sleep_score"] == 85


@pytest.mark.parametrize("extra_headers,status", [({"Host": "attacker.example"}, 421),
                                                   ({"Origin": "https://attacker.example"}, 403)])
def test_valid_auth_does_not_bypass_transport_origin_guards(runtime, extra_headers, status):
    response = httpx.post(runtime.url, headers={"Authorization": "Bearer " + runtime.token,
                          "Accept": "application/json, text/event-stream", **extra_headers},
                          json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    assert response.status_code == status


def test_invalid_bearer_never_reaches_garmin(runtime):
    calls_before = len(runtime.fake.calls)
    response = httpx.post(runtime.url, headers={"Authorization": "Bearer invalid",
                          "Accept": "application/json, text/event-stream"},
                          json={"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                                "params": {"name": "get_wellness_today", "arguments": {}}})
    assert response.status_code == 401
    assert len(runtime.fake.calls) == calls_before
