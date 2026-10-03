import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from garmin_mcp import sync, historical_sync


@pytest.mark.asyncio
@pytest.mark.parametrize('status,expected_delay', [('pending', 10), ('error', 900), ('completed', 3600)])
async def test_history_background_pacing_and_recent_refresh(monkeypatch, status, expected_delay):
    monkeypatch.setenv('GARMIN_HISTORY_START_DATE', '2017-01-01')
    monkeypatch.setenv('GARMIN_SYNC_INTERVAL_SECONDS', '3600')
    monkeypatch.setenv('GARMIN_HISTORY_RETRY_SECONDS', '900')
    monkeypatch.setenv('GARMIN_HISTORY_BATCH_PAUSE_SECONDS', '10')
    monkeypatch.setattr(sync.time, 'monotonic', lambda: 100)
    recent = AsyncMock(return_value={'range_completed': True})
    historical = AsyncMock(return_value={'status': status})
    monkeypatch.setattr(sync, 'sync_once', recent)
    monkeypatch.setattr(historical_sync, 'sync_history_batch', historical)
    delays = []

    async def sleep(delay):
        delays.append(delay)
        if len(delays) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(sync.asyncio, 'sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        await sync.background_sync(object(), object(), Mock(ready=lambda: True))
    assert delays == [expected_delay, expected_delay]
    assert recent.await_count == 1
    assert historical.await_count == 2

