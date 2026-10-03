import asyncio
import threading
from unittest.mock import Mock

from starlette.testclient import TestClient

from garmin_mcp.runtime import build_app
from garmin_mcp.security import OAuthSettings


def test_sync_lives_with_http_server_not_stateless_mcp_request(monkeypatch):
    from garmin_mcp import sync
    monkeypatch.setenv('GARMIN_SYNC_ENABLED', 'true')
    started, stopped = threading.Event(), threading.Event()
    starts = []

    async def worker(*args):
        starts.append(True)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    monkeypatch.setattr(sync, 'background_sync', worker)
    settings = OAuthSettings('https://tenant.example/', 'https://testserver/mcp',
                             'https://tenant.example/.well-known/jwks.json',
                             'https://testserver/mcp', 'owner')
    http, _, _ = build_app(client=Mock(), store=Mock(), settings=settings,
                          identifier='opaque-test')
    http.verifier = Mock(verify=lambda token: {'sub': 'owner'})
    with TestClient(http) as client:
        assert started.wait(2), 'Sync must start without any MCP request'
        assert not stopped.is_set()
        for request_id in (1, 2):
            response = client.post('/mcp', headers={
                'Authorization': 'Bearer fixture',
                'Accept': 'application/json, text/event-stream',
                'MCP-Protocol-Version': '2025-11-25'}, json={
                    'jsonrpc': '2.0', 'id': request_id, 'method': 'tools/list'})
            assert response.status_code == 200
            assert len(response.json()['result']['tools']) == 23
            assert not stopped.is_set(), 'Finishing MCP request must not stop sync'
        assert len(starts) == 1
    assert stopped.wait(2), 'Server shutdown must cancel sync'

