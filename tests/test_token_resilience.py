from unittest.mock import Mock
import pytest
import requests
from garminconnect import GarminConnectConnectionError,GarminConnectTooManyRequestsError
from garmin_mcp.garmin_client import TokenClient,GarminUnavailable,GarminTooManyRequests,error_response

def make_client(tmp_path):
    (tmp_path/'garmin_tokens.json').write_text('{}')
    api=Mock()
    return TokenClient(str(tmp_path),factory=Mock(return_value=api)),api

def test_terminal_rate_limit_cools_down_and_follows_cause(tmp_path,monkeypatch):
    c,api=make_client(tmp_path)
    monkeypatch.setattr('garmin_mcp.garmin_client.time.sleep',lambda _:None)
    response=requests.Response();response.status_code=429;response.headers['Retry-After']='3'
    cause=requests.HTTPError(response=response)
    err=GarminConnectTooManyRequestsError('private');err.__cause__=cause
    api.get_stats.side_effect=err
    with pytest.raises(GarminTooManyRequests):c.get_stats('2025-01-01')
    assert api.get_stats.call_count==3
    with pytest.raises(GarminTooManyRequests):c.get_stats('2025-01-02')
    assert api.get_stats.call_count==3
    assert error_response(err) is response

def test_404_never_retried(tmp_path):
    c,api=make_client(tmp_path)
    response=requests.Response();response.status_code=404
    error=GarminConnectConnectionError('private');error.response=response
    api.get_stats.side_effect=error
    with pytest.raises(GarminUnavailable):c.get_stats('2025-01-01')
    assert api.get_stats.call_count==1

def test_network_failure_three_attempts_no_sensitive_error(tmp_path,monkeypatch):
    c,api=make_client(tmp_path)
    monkeypatch.setattr('garmin_mcp.garmin_client.time.sleep',lambda _:None)
    api.get_stats.side_effect=GarminConnectConnectionError('password=secret')
    with pytest.raises(GarminUnavailable) as exc:c.get_stats('2025-01-01')
    assert 'secret' not in str(exc.value)
    assert api.get_stats.call_count==3
