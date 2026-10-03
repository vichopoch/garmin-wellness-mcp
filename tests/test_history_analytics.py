"""Cached multi-year analytics: real SQL queries, no Garmin or credentials."""
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from garmin_mcp.history_analytics import history_overview
from garmin_mcp.storage import Store, metadata, profiles, tables
from garmin_mcp.wellness import METRICS, WellnessService, normalize, register_tools
from tests.test_wellness_tools import Registry


@pytest.fixture
def store(tmp_path):
    store = Store(f'sqlite:///{tmp_path / "history.sqlite"}')
    metadata.create_all(store.engine)
    store.get_sync_job = Mock(return_value={
        'status': 'running', 'start_date': '2020-01-01', 'target_date': '2026-01-01',
        'next_date': '2024-01-01', 'days_processed': 1461, 'days_with_data': 1400,
        'activities_seen': 3000, 'activities_complete': False,
        'password': 'DO-NOT-LEAK', 'error': {'reason': 'private-token'},
    })
    return store


def put(store, profile, day, hrv=None, sleep=None, errors=None, stale=None):
    row = normalize(day, {
        'hrv': {'hrvSummary': {'lastNightAvg': hrv}},
        'sleep': {'dailySleepDTO': {'sleepScores': {'overall': {'value': sleep}}}},
    }, errors)
    if stale:
        row['stale_groups'] = stale
    # Direct fixture inserts preserve deliberately stale values and source failures.
    with store.engine.begin() as conn:
        if not conn.execute(profiles.select().where(profiles.c.id == profile)).first():
            conn.execute(profiles.insert().values(id=profile, timezone='America/Santiago'))
        conn.execute(tables['daily_health'].insert().values(
            profile_id=profile, date=datetime.strptime(day, '%Y-%m-%d').date(),
            data=row, updated_at=datetime.now(timezone.utc)))


def test_monthly_mean_median_coverage_missing_and_profile_isolation(store):
    put(store, 'owner', '2020-01-01', hrv=40, sleep=80)
    put(store, 'owner', '2020-01-03', hrv=60, sleep=90)
    put(store, 'owner', '2021-02-01', hrv=30)
    put(store, 'other', '2010-01-01', hrv=999)
    put(store, 'other', '2020-01-02', hrv=999)
    result = history_overview(store, 'owner', METRICS, ['hrv', 'sleep_score'])
    assert result['coverage']['first_cached_date'] == '2020-01-01'
    assert result['coverage']['last_cached_date'] == '2021-02-01'
    assert result['coverage']['cached_days'] == 3
    assert len(result['monthly']) == 14
    january = result['monthly'][0]['metrics']
    assert january['hrv']['mean'] == 50
    assert january['hrv']['median'] == 50
    assert january['hrv']['sample_count'] == 2
    assert january['hrv']['missing_days'] == 29
    assert result['monthly'][1]['metrics']['hrv']['mean'] is None
    assert result['monthly'][1]['metrics']['hrv']['missing_days'] == 29  # Leap February.
    assert result['metrics']['sleep_score']['last_observed_date'] == '2020-01-03'
    assert result['sync_progress']['activities_seen'] == 3000
    assert result['sync_progress']['activities_complete'] is False
    assert 'private-token' not in str(result)
    assert 'DO-NOT-LEAK' not in str(result)
    assert '999' not in str(result)
    store.get_sync_job.assert_called_once_with('owner', 'history')


def test_stale_and_failed_source_values_excluded_without_zero_fill(store):
    put(store, 'owner', '2020-01-01', hrv=40, sleep=80)
    put(store, 'owner', '2020-01-02', hrv=60, sleep=90,
        errors={'hrv': 'timeout'}, stale={'hrv': {'hrv': {'fields': ['nightly_avg'], 'last_success_at': None}}})
    put(store, 'owner', '2020-01-03', hrv=80, errors={'hrv': 'upstream_error'})
    result = history_overview(store, 'owner', METRICS, ['hrv', 'sleep_score'])
    assert result['metrics']['hrv']['sample_count'] == 1
    assert result['metrics']['hrv']['stale_days_excluded'] == 2
    assert result['monthly'][0]['metrics']['hrv']['mean'] == 40
    assert result['monthly'][0]['metrics']['sleep_score']['mean'] == 85
    assert result['coverage']['source_error_days'] == 2


def test_explicit_range_is_inclusive_and_partial_month_missing_correct(store):
    put(store, 'owner', '2020-01-01', hrv=40)
    put(store, 'owner', '2020-01-03', hrv=60)
    result = history_overview(store, 'owner', METRICS, ['hrv'], '2020-01-02', '2020-01-04')
    assert result['coverage']['calendar_days'] == 3
    assert result['coverage']['cached_days'] == 1
    assert result['coverage']['uncached_days'] == 2
    assert result['monthly'][0]['metrics']['hrv']['mean'] == 60
    assert result['monthly'][0]['metrics']['hrv']['missing_days'] == 2


@pytest.mark.parametrize('kwargs', [
    {'metrics': []}, {'metrics': ['hrv', 'hrv']}, {'metrics': ['unknown']},
    {'start_date': '2020-1-01'}, {'start_date': '2020-02-30'},
    {'start_date': '2021-01-01', 'end_date': '2020-01-01'},
    {'start_date': '1990-01-01', 'end_date': '2026-01-01'},
])
def test_validation_before_storage_queries(kwargs):
    store = Mock()
    with pytest.raises(ValueError):
        history_overview(store, 'owner', METRICS, **kwargs)
    assert not store.engine.mock_calls
    assert not store.get_sync_job.mock_calls


def test_empty_cache_and_missing_database_clear_progress(store):
    result = history_overview(store, 'owner', METRICS)
    assert result['available'] is False
    assert result['sync_progress']['status'] == 'running'
    assert history_overview(None, 'owner', METRICS)['reason'] == 'Historical cache is not configured'


def test_maximum_400_months_enforced_when_dates_inferred(store):
    put(store, 'owner', '1980-01-01', hrv=40)
    put(store, 'owner', '2026-01-01', hrv=50)
    with pytest.raises(ValueError, match='400'):
        history_overview(store, 'owner', METRICS)
    result = history_overview(store, 'owner', METRICS, ['hrv'], '2000-01-01', '2026-01-01')
    assert len(result['monthly']) == 313


@pytest.mark.asyncio
async def test_history_tool_never_calls_garmin(store):
    put(store, 'owner', '2020-01-01', hrv=40)
    client = Mock()
    registry = Registry()
    register_tools(registry, WellnessService(client, store, profile_id='owner'))
    result = await registry.tools['get_history_overview'](['hrv'])
    assert result['available'] is True
    assert not client.mock_calls


def test_legacy_cache_excluded_until_renormalized(store):
    put(store, 'owner', '2020-01-01', hrv=40)
    put(store, 'owner', '2020-01-02', hrv=0)
    with store.engine.begin() as conn:
        conn.execute(tables['daily_health'].update().where(
            tables['daily_health'].c.profile_id == 'owner',
            tables['daily_health'].c.date == datetime(2020, 1, 2).date()).values(
                data={'normalization_version': 1, 'hrv': {'nightly_avg': 0}}))
    result = history_overview(store, 'owner', METRICS, ['hrv'])
    assert result['coverage']['legacy_days_excluded'] == 1
    assert result['coverage']['usable_cached_days'] == 1
    assert result['monthly'][0]['metrics']['hrv']['mean'] == 40
    assert result['metrics']['hrv']['sample_count'] == 1


def test_checkpoint_error_details_are_safely_projected(store):
    store.get_sync_job.return_value = {'status': 'error', 'error': {
        'stage': 'daily', 'reason': 'source_errors', 'date': '2020-01-01',
        'sources': {'hrv': 'timeout', 'password': 'private-secret'},
        'payload': 'private-secret',
    }}
    result = history_overview(store, 'owner', METRICS)
    assert result['sync_progress']['error'] == {
        'stage': 'daily', 'reason': 'source_errors', 'date': '2020-01-01', 'sources': {'hrv': 'timeout'},
    }
    assert 'private-secret' not in str(result)
