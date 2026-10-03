from datetime import datetime, timezone
from unittest.mock import AsyncMock
from unittest.mock import Mock
import asyncio
from zoneinfo import ZoneInfo

import pytest

from garmin_mcp.storage import Store, migrate
from garmin_mcp.sync_schedule import DailySchedule, scheduled_refresh


ZONE = ZoneInfo('America/Santiago')


@pytest.fixture
def store(tmp_path):
    url = 'sqlite:///' + str(tmp_path / 'schedule.db')
    migrate(url)
    result = Store(url)
    yield result
    result.engine.dispose()


def test_fixed_times_use_santiago_not_utc_and_roll_over_date():
    schedule = DailySchedule('07:00,15:00,23:00', 'America/Santiago')
    now = datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc)
    previous, following = schedule.bounds(now)
    assert previous.isoformat() == '2026-10-02T23:00:00-03:00'
    assert following.isoformat() == '2026-10-03T07:00:00-03:00'
    assert schedule.delay(now) == 7.5 * 3600


@pytest.mark.parametrize('month,day', [(4, 5), (9, 6)])
def test_dst_keeps_wall_clock_hours_and_uses_elapsed_seconds(month, day):
    schedule = DailySchedule('07:00,15:00,23:00', 'America/Santiago')
    now = datetime(2026, month, day, 2, tzinfo=ZONE)
    _, following = schedule.bounds(now)
    assert following.hour == 7
    assert schedule.delay(now) == following.timestamp() - now.timestamp()


@pytest.mark.parametrize('value', ['', '25:00', '07:61', '7:00', '07:00,07:00'])
def test_invalid_schedule_rejected(value):
    with pytest.raises(ValueError):
        DailySchedule(value, 'America/Santiago')


@pytest.mark.asyncio
async def test_three_slots_and_no_extra_startup_or_restart_refresh(store):
    class Service:
        profile_id = 'owner'
    service = Service()
    schedule = DailySchedule('07:00,15:00,23:00', 'America/Santiago')
    refresh = AsyncMock(return_value={'range_completed': True, 'errors': []})
    at = lambda hour, minute=0: datetime(2026, 10, 3, hour, minute, tzinfo=ZONE)
    state = await scheduled_refresh(service, store, schedule, refresh, at(6, 59))
    assert state['status'] == 'waiting'
    refresh.assert_not_awaited()
    for hour in (7, 15, 23):
        state = await scheduled_refresh(service, store, schedule, refresh, at(hour))
        assert state['status'] == 'success'
        # A fresh connection to persisted state models process restart.
        restarted = Store(str(store.engine.url))
        try:
            await scheduled_refresh(service, restarted, schedule, refresh, at(hour, 1))
        finally:
            restarted.engine.dispose()
    assert refresh.await_count == 3


@pytest.mark.asyncio
async def test_missed_slots_catch_up_once_and_failure_waits_next_slot(store):
    class Service:
        profile_id = 'owner'
    schedule = DailySchedule('07:00,15:00,23:00', 'America/Santiago')
    refresh = AsyncMock(side_effect=RuntimeError('private payload must not persist'))
    await scheduled_refresh(Service(), store, schedule, refresh, datetime(2026, 10, 2, 6, tzinfo=ZONE))
    now = datetime(2026, 10, 3, 16, tzinfo=ZONE)
    result = await scheduled_refresh(Service(), store, schedule, refresh, now)
    assert result['status'] == 'error'
    assert result['error_type'] == 'RuntimeError'
    assert 'private' not in str(result)
    await scheduled_refresh(Service(), store, schedule, refresh, now)
    assert refresh.await_count == 1


@pytest.mark.asyncio
async def test_fixed_background_waits_and_does_not_restart_completed_history(store, monkeypatch):
    from garmin_mcp import sync, historical_sync
    class Service:
        profile_id = 'owner'
    class Clock:
        @staticmethod
        def now(zone):
            return datetime(2026, 10, 3, 9, tzinfo=ZONE).astimezone(zone)
    monkeypatch.setenv('GARMIN_SYNC_TIMES', '07:00,15:00,23:00')
    monkeypatch.setenv('GARMIN_SYNC_TIMEZONE', 'America/Santiago')
    monkeypatch.setenv('GARMIN_HISTORY_START_DATE', '2017-01-01')
    monkeypatch.setattr(sync, 'datetime', Clock)
    store.put_sync_job('owner', {'status': 'completed', 'start_date': '2017-01-01'})
    recent, history = AsyncMock(), AsyncMock()
    monkeypatch.setattr(sync, 'sync_once', recent)
    monkeypatch.setattr(historical_sync, 'sync_history_batch', history)
    async def stop(delay):
        assert delay == 60
        raise asyncio.CancelledError
    monkeypatch.setattr(sync.asyncio, 'sleep', stop)
    with pytest.raises(asyncio.CancelledError):
        await sync.background_sync(Service(), store, Mock(ready=lambda: True))
    recent.assert_not_awaited()
    history.assert_not_awaited()
    assert store.get_sync_job('owner', 'recent_schedule')['next_run'] == '2026-10-03T15:00:00-03:00'
