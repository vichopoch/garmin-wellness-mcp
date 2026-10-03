import asyncio
from copy import deepcopy
from datetime import date, timedelta

import pytest
from sqlalchemy import select, func

from garmin_mcp.historical_sync import sync_history_batch
from garmin_mcp.storage import Store, migrate, activities_records


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('GARMIN_SYNC_DAY_PAUSE_SECONDS', '0')
    url = 'sqlite:///' + str(tmp_path / 'history.db')
    migrate(url)
    result = Store(url)
    yield result
    result.engine.dispose()


class Service:
    profile_id = 'test-history-owner'

    def __init__(self, store, today='2026-01-03', activities=0):
        self.store, self.end = store, today
        self.activities = [{'activityId': index + 1, 'distance': 1000,
                            'activityName': 'PRIVATE', 'startLatitude': 0,
                            'startTimeLocal': '2018-03-02 10:00:00' if index == 0 else '2026-01-02 12:00:00'}
                           for index in range(activities)]
        self.days_called = []
        self.pages_called = []
        self.errors = {}
        self.activity_error_offset = None
        self.gaps = set()
        self.slow = False

    async def today(self):
        return self.end

    async def daily(self, day, refresh=False):
        if self.slow:
            await asyncio.sleep(.1)
        cached = self.store.get_day(self.profile_id, day)
        if cached and not refresh:
            return cached
        self.days_called.append(day)
        result = {'date': day, 'health': {'available': day not in self.gaps},
                  'source_errors': self.errors.get(day, {})}
        if day not in self.gaps:
            result['health']['steps'] = 123
        self.store.put_day(self.profile_id, day, result)
        return result

    async def call(self, method, offset, limit):
        assert method == 'get_activities'
        assert limit == 100
        self.pages_called.append(offset)
        if offset == self.activity_error_offset:
            return None, 'rate_limited'
        return self.activities[offset:offset + limit], None


def count_activities(store, profile='test-history-owner'):
    with store.engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(activities_records).where(activities_records.c.profile_id == profile)).scalar()


@pytest.mark.asyncio
async def test_resume_days_after_store_restart_and_idempotency(store):
    service = Service(store)
    first = await sync_history_batch(service, store, '2026-01-01', max_days=1)
    assert first['status'] == 'pending'
    assert first['next_date'] == '2026-01-02'
    assert first['days_processed'] == 1
    restarted = Store(str(store.engine.url))
    try:
        new_service = Service(restarted)
        second = await sync_history_batch(new_service, restarted, '2026-01-01', max_days=5)
        assert second['status'] == 'completed'
        assert second['days_processed'] == 3
        assert new_service.days_called == ['2026-01-02', '2026-01-03']
        calls = list(new_service.days_called), list(new_service.pages_called)
        again = await sync_history_batch(new_service, restarted, '2026-01-01')
        assert again == second
        assert calls == (new_service.days_called, new_service.pages_called)
        assert second['first_observed'] == '2026-01-01'
        assert second['last_observed'] == '2026-01-03'
    finally:
        restarted.engine.dispose()


@pytest.mark.asyncio
async def test_source_error_does_not_advance_and_retries_cached_failure(store):
    service = Service(store)
    service.errors['2026-01-02'] = {'hrv': 'rate_limited'}
    result = await sync_history_batch(service, store, '2026-01-01')
    assert result['status'] == 'error'
    assert result['days_processed'] == 1
    assert result['next_date'] == '2026-01-02'
    assert result['error']['sources'] == {'hrv': 'rate_limited'}
    service.errors.clear()
    result = await sync_history_batch(service, store, '2026-01-01')
    assert result['status'] == 'completed'
    assert result['days_processed'] == 3
    assert service.days_called.count('2026-01-01') == 1
    assert service.days_called.count('2026-01-02') == 2


@pytest.mark.asyncio
async def test_missing_without_errors_counts_gap_and_not_failure(store):
    service = Service(store)
    service.gaps = {'2026-01-01', '2026-01-03'}
    result = await sync_history_batch(service, store, '2026-01-01')
    assert result['status'] == 'completed'
    assert result['days_processed'] == 3
    assert result['days_with_data'] == 1
    assert result['missing_days_count'] == 2
    assert result['first_observed'] == result['last_observed'] == '2026-01-02'


@pytest.mark.asyncio
async def test_total_range_can_exceed_365_days(store):
    service = Service(store, today='2026-01-03')
    result = await sync_history_batch(service, store, '2010-01-01', max_days=2)
    assert result['next_date'] == '2010-01-03'
    assert result['target_date'] == '2026-01-03'
    assert result['status'] == 'pending'
    assert result['days_processed'] == 2


@pytest.mark.asyncio
async def test_activity_pagination_resumes_preserves_offset_on_rate_limit(store):
    service = Service(store, activities=250)
    result = await sync_history_batch(service, store, '2026-01-03', max_pages=1)
    assert result['activities_offset'] == 100
    assert result['status'] == 'pending'
    assert count_activities(store) == 100
    service.activity_error_offset = 100
    result = await sync_history_batch(service, store, '2026-01-03')
    assert result['status'] == 'error'
    assert result['activities_offset'] == 100
    assert count_activities(store) == 100
    service.activity_error_offset = None
    result = await sync_history_batch(service, store, '2026-01-03')
    assert result['status'] == 'completed'
    assert result['activities_offset'] == 250
    assert result['activities_seen'] == 250
    assert result['activities_pages'] == 3
    assert result['first_activity_date'] == '2018-03-02'
    assert result['last_activity_date'] == '2026-01-02'
    assert service.pages_called == [0, 100, 100, 200]
    assert count_activities(store) == 250
    with store.engine.connect() as conn:
        saved = conn.execute(select(activities_records.c.data)).scalars().all()
    assert all('activityName' not in row and 'startLatitude' not in row for row in saved)


@pytest.mark.asyncio
async def test_exact_full_activity_page_requires_empty_tail(store):
    service = Service(store, activities=100)
    result = await sync_history_batch(service, store, '2026-01-03')
    assert service.pages_called == [0, 100]
    assert result['activities_complete'] is True
    assert count_activities(store) == 100


def test_page_upsert_and_cursor_are_atomic(store, monkeypatch):
    initial = {'activities_offset': 0}
    store.put_sync_job('test-history-owner', initial)
    original = store._upsert
    def broken(conn, table, values, keys):
        if table is activities_records:
            raise RuntimeError('simulated database failure')
        return original(conn, table, values, keys)
    monkeypatch.setattr(store, '_upsert', broken)
    with pytest.raises(RuntimeError):
        store.commit_history_page('test-history-owner', [{'activity_id': 1}], {'activities_offset': 100})
    assert store.get_sync_job('test-history-owner') == initial
    assert count_activities(store) == 0


@pytest.mark.asyncio
async def test_time_budget_retains_day_cursor(store):
    service = Service(store)
    service.slow = True
    result = await sync_history_batch(service, store, '2026-01-01', max_seconds=.02)
    assert result['status'] == 'pending'
    assert result['stop_reason'] == 'time_budget'
    assert result['days_processed'] == 0
    assert result['next_date'] == '2026-01-01'


@pytest.mark.asyncio
async def test_locked_batch_does_not_call_service(store):
    service = Service(store)
    with store.sync_lock() as acquired:
        assert acquired
        result = await sync_history_batch(service, store, '2026-01-01')
    assert result['status'] == 'skipped'
    assert service.days_called == service.pages_called == []


@pytest.mark.asyncio
async def test_completed_job_extends_next_day_and_deduplicates_activities(store):
    service = Service(store, activities=2)
    first = await sync_history_batch(service, store, '2026-01-03')
    assert first['status'] == 'completed'
    service.end = '2026-01-04'
    second = await sync_history_batch(service, store, '2026-01-03')
    assert second['status'] == 'completed'
    assert second['days_processed'] == 2
    assert count_activities(store) == 2
    assert service.days_called == ['2026-01-03', '2026-01-04']


@pytest.mark.asyncio
async def test_existing_cache_reused_when_start_date_changes(store):
    service = Service(store)
    await sync_history_batch(service, store, '2026-01-02')
    assert service.days_called == ['2026-01-02', '2026-01-03']
    result = await sync_history_batch(service, store, '2026-01-01')
    assert result['days_processed'] == 3
    assert service.days_called == ['2026-01-02', '2026-01-03', '2026-01-01']


@pytest.mark.asyncio
async def test_bad_activity_id_prevents_false_completion(store):
    service = Service(store)
    service.activities = [{'activityName': 'private'}]
    result = await sync_history_batch(service, store, '2026-01-03')
    assert result['status'] == 'error'
    assert result['activities_offset'] == 0
    assert not result['activities_complete']


@pytest.mark.asyncio
async def test_profile_jobs_are_isolated(store):
    first = Service(store)
    second = Service(store)
    second.profile_id = 'another-owner'
    await sync_history_batch(first, store, '2026-01-01', max_days=1)
    await sync_history_batch(second, store, '2026-01-03')
    assert store.get_sync_job(first.profile_id)['days_processed'] == 1
    assert store.get_sync_job(second.profile_id)['days_processed'] == 1
    assert store.get_sync_job(second.profile_id)['start_date'] == '2026-01-03'


@pytest.mark.asyncio
async def test_repeated_activity_page_stops_without_false_coverage(store):
    service = Service(store, activities=100)
    async def broken_pagination(method, offset, limit):
        return service.activities, None
    service.call = broken_pagination
    result = await sync_history_batch(service, store, '2026-01-03')
    assert result['status'] == 'error'
    assert result['error']['reason'] == 'pagination_not_advancing'
    assert result['activities_offset'] == 100
    assert result['days_processed'] == 0
    assert count_activities(store) == 100


@pytest.mark.asyncio
async def test_auth_failure_preserves_page_and_daily_cursor(store):
    service = Service(store)
    async def expired(method, offset, limit):
        return None, 'authentication_required'
    service.call = expired
    result = await sync_history_batch(service, store, '2026-01-01')
    assert result['status'] == 'error'
    assert result['activities_offset'] == 0
    assert result['next_date'] == '2026-01-01'
    assert result['error']['reason'] == 'authentication_required'
    assert service.days_called == []


@pytest.mark.asyncio
async def test_exception_messages_never_enter_job_state(store):
    service = Service(store)
    async def failing_daily(day, refresh=False):
        raise RuntimeError('Authorization Bearer SECRET user@example.com')
    service.daily = failing_daily
    result = await sync_history_batch(service, store, '2026-01-01')
    assert result['status'] == 'error'
    assert result['next_date'] == '2026-01-01'
    assert result['error']['reason'] == 'batch_failure'
    assert 'SECRET' not in str(store.get_sync_job(service.profile_id))


@pytest.mark.asyncio
async def test_crash_after_day_write_replays_cache_before_advancing(store):
    service = Service(store)
    await service.daily('2026-01-01')
    assert store.get_sync_job(service.profile_id) is None
    result = await sync_history_batch(service, store, '2026-01-01', max_days=1)
    assert result['days_processed'] == 1
    assert result['next_date'] == '2026-01-02'
    assert service.days_called == ['2026-01-01']


@pytest.mark.asyncio
async def test_completed_old_normalization_version_is_revisited(store):
    service = Service(store)
    result = await sync_history_batch(service, store, '2026-01-01')
    assert result['status'] == 'completed'
    result['normalization_version'] = 1
    store.put_sync_job(service.profile_id, result)
    result = await sync_history_batch(service, store, '2026-01-01', max_days=1)
    assert result['normalization_version'] == 2
    assert result['status'] == 'pending'
    assert result['days_processed'] == 1
    assert result['next_date'] == '2026-01-02'
