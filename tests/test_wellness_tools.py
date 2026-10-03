"""Wellness contract tests use SDK-shaped payloads; no Garmin credentials needed."""
import asyncio
import threading
import time
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp.wellness import (
    METRICS, SOURCE_METHODS, WellnessService, date_range, normalize,
    normalize_activity, register_tools, slope, summary,
)
from tests.fixtures.garmin_responses import (
    MOCK_BODY_BATTERY, MOCK_HRV_DATA, MOCK_SLEEP_DATA,
    MOCK_STRESS_DATA, MOCK_TRAINING_STATUS,
)


class FakeGarmin:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name not in {*SOURCE_METHODS.values(), 'get_user_profile', 'get_activities', 'get_activity'}:
            raise AttributeError(name)
        def read(*args):
            self.calls.append((name, args))
            return {
                'get_user_profile': {'userData': {'timeZone': 'Pacific/Auckland'}},
                'get_sleep_data': MOCK_SLEEP_DATA, 'get_hrv_data': MOCK_HRV_DATA,
                'get_body_battery': MOCK_BODY_BATTERY, 'get_stress_data': MOCK_STRESS_DATA,
                'get_training_status': MOCK_TRAINING_STATUS,
                'get_training_readiness': [{'score': 81, 'recoveryTime': 120}],
                'get_stats': {'totalSteps': 1000, 'restingHeartRate': 51,
                              'moderateIntensityMinutes': 10, 'vigorousIntensityMinutes': 3},
                'get_spo2_data': {'averageSpO2': 97},
                'get_respiration_data': {'avgWakingRespirationValue': 14},
                'get_max_metrics': [{'generic': {'vo2MaxPreciseValue': 50.321}}],
                'get_activities': [{'activityId': 123, 'activityName': 'private home', 'startLatitude': -33,
                                    'duration': 1800, 'activityTrainingLoad': 45}],
                'get_activity': {'activityId': 123, 'summaryDTO': {'duration': 1800, 'startLatitude': -33}},
            }.get(name, {})
        return read


class Registry:
    def __init__(self):
        self.tools = {}
        self.options = {}

    def tool(self, **options):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            self.options[fn.__name__] = options
            return fn
        return decorate


@pytest.fixture
def tools():
    client = FakeGarmin()
    service = WellnessService(client)
    registry = Registry()
    register_tools(registry, service)
    return registry, service, client


def test_normalization_sdk_fixtures_preserves_units_missing_and_precision():
    result = normalize('2026-01-01', {
        'sleep': MOCK_SLEEP_DATA, 'hrv': MOCK_HRV_DATA,
        'body_battery': MOCK_BODY_BATTERY, 'stress': MOCK_STRESS_DATA,
        'status': MOCK_TRAINING_STATUS,
        'readiness': [{'score': 80, 'recoveryTime': 90}],
    })
    assert result['sleep']['sleep_seconds'] == 28800
    assert result['sleep']['sleep_hours'] == 8
    assert result['naps']['total_seconds'] == 0
    assert result['hrv']['nightly_avg'] == 48
    assert result['hrv']['baseline_low'] == 40
    assert result['body_battery']['latest'] == 75
    assert result['body_battery']['high'] == 100
    assert result['training']['vo2max'] == 52.47
    assert result['training']['recovery_seconds'] == 5400
    assert result['training']['acute_load'] == 250
    assert result['health']['available'] is False
    assert 'userProfilePk' not in str(result)


@pytest.mark.parametrize('payload', [None, {}, [], {'dailySleepDTO': None}, {'dailySleepDTO': {'sleepScores': None}}])
def test_missing_sleep_is_not_zero(payload):
    result = normalize('2026-01-01', {'sleep': payload})
    assert result['sleep']['available'] is False
    assert 'sleep_seconds' not in result['sleep']


def test_sentinel_values_and_recovery_reached_zero():
    result = normalize('2026-01-01', {
        'stress': {'avgStressLevel': -1},
        'readiness': [{'score': 0, 'recoveryTime': 300, 'recoveryTimeChangePhrase': 'REACHED_ZERO'}],
        'sleep': {'dailySleepDTO': {'sleepTimeSeconds': 0, 'avgSkinTempDeviationC': -0.3}},
    })
    assert result['stress']['available'] is False
    assert result['sleep']['sleep_seconds'] == 0
    assert result['sleep']['skin_temperature_change'] == -0.3
    assert result['training']['readiness'] == 0
    assert result['training']['recovery_seconds'] == 0


def test_summary_missing_and_trend_keep_calendar_gaps():
    assert summary([1, None, 3, float('nan')]) == {
        'mean': 2, 'median': 2, 'min': 1, 'max': 3, 'sample_count': 2, 'missing_days': 2,
    }
    assert slope([1, None, 5]) == 2


@pytest.mark.parametrize('start,end', [('2026-01-01', '2025-01-01'), ('2025-01-01', '2026-01-01'),
                                      ('2026-1-01', '2026-01-01'), ('2026-02-30', '2026-02-30')])
def test_invalid_dates_and_unbounded_ranges_rejected(start, end):
    with pytest.raises(ValueError):
        date_range(start, end)


@pytest.mark.asyncio
async def test_all_registered_tools_are_readonly_with_output_schemas():
    app = FastMCP('wellness-test')
    register_tools(app, WellnessService(FakeGarmin()))
    definitions = await app.list_tools()
    assert len(definitions) == 23
    assert {tool.name for tool in definitions} == {
        'get_profile', 'get_history_overview', 'get_capabilities', 'get_wellness_today', 'get_daily_health', 'get_health_range',
        'get_sleep', 'get_sleep_analysis', 'get_naps', 'get_recovery_context', 'get_hrv', 'get_body_battery',
        'get_stress', 'get_training_overview', 'get_training_readiness', 'get_training_status', 'get_vo2max',
        'get_activities', 'get_activity', 'get_metric_trend', 'compare_periods', 'get_metric_timeseries', 'find_correlations',
    }
    for tool in definitions:
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.openWorldHint is False
        assert tool.outputSchema is not None
        assert tool.meta['securitySchemes'] == [{'type': 'oauth2', 'scopes': ['garmin:read']}]
    assert next(tool for tool in definitions if tool.name == 'get_profile').meta['openai/profile'] is True


@pytest.mark.asyncio
async def test_summary_partial_failure_timezone_no_gps_and_cache(tools):
    registry, service, client = tools
    client.get_spo2_data = Mock(side_effect=RuntimeError('secret-token-DO-NOT-RETURN'))
    result = await registry.tools['get_wellness_today']()
    assert result['timezone'] == 'Pacific/Auckland'
    assert result['sleep']['sleep_score'] == 85
    assert result['source_errors']['spo2'] == 'upstream_error'
    assert 'secret-token' not in str(result)
    assert 'private home' not in str(result)
    assert 'Latitude' not in str(result)
    calls = len(client.calls)
    await registry.tools['get_wellness_today']()
    assert len(client.calls) == calls


@pytest.mark.asyncio
async def test_single_metric_does_not_fetch_every_source(tools):
    registry, _, client = tools
    result = await registry.tools['get_hrv']('2026-01-01')
    assert result['nightly_avg'] == 48
    assert client.calls == [('get_hrv_data', ('2026-01-01',))]


@pytest.mark.asyncio
async def test_store_read_and_curated_write():
    store = Mock()
    store.get_day.return_value = None
    service = WellnessService(FakeGarmin(), store=store, profile_id='opaque')
    record = await service.daily('2026-01-01')
    store.put_day.assert_called_once_with('opaque', '2026-01-01', record)
    store.get_day.return_value = record
    service.client = None
    assert await service.daily('2026-01-01') == record
    assert 'sleepMovement' not in str(store.put_day.call_args)


@pytest.mark.asyncio
async def test_activity_summary_does_not_leak_gps_or_names(tools):
    registry, _, _ = tools
    result = await registry.tools['get_activity']('123')
    assert result['duration_seconds'] == 1800
    assert 'Latitude' not in str(result)
    assert normalize_activity({'activityName': 'secret', 'latitude': 1}) == {}


@pytest.mark.asyncio
async def test_timeseries_limits_before_network(tools):
    registry, _, client = tools
    with pytest.raises(ValueError, match='max_points'):
        await registry.tools['get_metric_timeseries']('hrv', '2026-01-01', '2026-01-04', 3)
    with pytest.raises(ValueError, match='Unknown metric'):
        await registry.tools['compare_periods'](['hrv', 'unsafe'], '2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04')
    assert not client.calls


@pytest.mark.asyncio
async def test_comparison_lag_and_missing_pairs(tools):
    registry, service, _ = tools
    async def series(metric, start, end):
        values = {'hrv': [1, 2, None, 4, 5], 'sleep_duration': [0, 2, 4, 6, 8]}[metric]
        return [{'date': day, 'value': values[index]} for index, day in enumerate(date_range(start, end))]
    service.series = series
    result = await registry.tools['find_correlations']('hrv', 'sleep_duration', '2026-01-01', '2026-01-05', 1)
    assert result['n'] == 3
    assert result['correlation'] == pytest.approx(1)
    assert result['missing_pairs'] == 1
    assert result['boundary_excluded_days'] == 1
    result = await registry.tools['compare_periods'](['hrv'], '2026-01-01', '2026-01-02', '2026-01-01', '2026-01-05')
    assert result['metrics']['hrv']['period_b']['missing_days'] == 1
    assert result['metrics']['hrv']['difference'] == 1.5
    assert result['metrics']['hrv']['percentage_change'] == 100


@pytest.mark.asyncio
async def test_constant_and_insufficient_correlation_is_unavailable(tools):
    registry, _, _ = tools
    result = await registry.tools['find_correlations']('hrv', 'hrv', '2026-01-01', '2026-01-04')
    assert result['correlation'] is None
    assert result['n'] == 4


@pytest.mark.asyncio
async def test_worker_concurrency_bounded_after_timeout():
    lock = threading.Lock()
    state = {'running': 0, 'max': 0}
    class Client:
        def get_stats(self, day):
            with lock:
                state['running'] += 1
                state['max'] = max(state['max'], state['running'])
            time.sleep(.08)
            with lock:
                state['running'] -= 1
            return {}
    service = WellnessService(Client(), call_timeout=.015)
    results = await asyncio.gather(*(service.call('get_stats', str(i)) for i in range(8)))
    assert all(error == 'timeout' for _, error in results)
    await asyncio.sleep(.1)
    assert state['max'] == 2
    assert state['running'] == 0


@pytest.mark.asyncio
async def test_all_tools_execute_with_fake_garmin(tools):
    registry, _, _ = tools
    date_args = {'start_date': '2026-01-01', 'end_date': '2026-01-03'}
    args = {
        'get_daily_health': {'date': '2026-01-01'}, 'get_health_range': date_args,
        'get_sleep_analysis': date_args, 'get_recovery_context': {'days': 2},
        'get_activity': {'activity_id': '123'},
        'get_metric_trend': {'metric': 'hrv', **date_args},
        'get_metric_timeseries': {'metric': 'hrv', **date_args},
        'find_correlations': {'metric_x': 'hrv', 'metric_y': 'stress', **date_args},
        'compare_periods': {'metrics': ['hrv'], 'period_a_start': '2026-01-01', 'period_a_end': '2026-01-02',
                            'period_b_start': '2026-01-03', 'period_b_end': '2026-01-04'},
    }
    for name, fn in registry.tools.items():
        result = await fn(**args.get(name, {}))
        assert isinstance(result, dict), name


@pytest.mark.asyncio
async def test_cold_analytics_budget_requires_sync(tools):
    registry, _, client = tools
    with pytest.raises(ValueError, match='Run historical sync first'):
        await registry.tools['get_metric_timeseries']('hrv', '2026-01-01', '2026-04-01')
    assert len(client.calls) == 60


@pytest.mark.asyncio
async def test_warm_analytics_uses_store_without_garmin():
    store = Mock()
    store.get_day.side_effect = lambda profile, date: normalize(date, {'hrv': MOCK_HRV_DATA})
    client = FakeGarmin()
    registry = Registry()
    register_tools(registry, WellnessService(client, store=store))
    result = await registry.tools['get_metric_timeseries']('hrv', '2026-01-01', '2026-04-01')
    assert result['sample_count'] == 91
    assert client.calls == []


@pytest.mark.asyncio
async def test_defense_in_depth_method_allowlist(tools):
    _, service, client = tools
    with pytest.raises(ValueError, match='read-only Wellness allowlist'):
        await service.call('delete_activity', '123')
    assert client.calls == []


def test_readiness_uses_latest_timestamp_without_local_field_and_sleep_need_minutes():
    result = normalize('2026-01-01', {
        'readiness': [
            {'timestamp': '2026-01-01T07:00:00Z', 'score': 60, 'recoveryTime': 120},
            {'timestamp': '2026-01-01T11:00:00Z', 'score': 80, 'recoveryTime': 60},
        ],
        'sleep': {'dailySleepDTO': {'sleepNeed': {'actual': 470}}},
        'body_battery': [{'charged': 45, 'drained': 20,
                          'bodyBatteryValuesArray': [[1000, 20], [2000, 40], [3000, -1], [4000, 35]]}],
    })
    assert result['training']['readiness'] == 80
    assert result['training']['recovery_seconds'] == 3600
    assert result['sleep']['sleep_need_seconds'] == 28200
    assert result['body_battery']['latest'] == 35
    assert result['body_battery']['high'] == 40
    assert result['body_battery']['charged'] == 45


@pytest.mark.asyncio
async def test_tool_audit_logs_only_safe_metadata(tools, caplog):
    import json
    import logging
    from uuid import UUID
    from garmin_mcp.observability import PrivateJSONFormatter
    registry, _, _ = tools
    caplog.set_level(logging.INFO, logger='garmin_mcp.audit')
    await registry.tools['get_hrv']('2026-01-01')
    with pytest.raises(ValueError):
        await registry.tools['get_activity']('secret-email@example.com')
    records = [record for record in caplog.records if getattr(record, 'safe_event', None) == 'mcp_tool_call']
    assert len(records) == 2
    entries = [json.loads(PrivateJSONFormatter().format(record)) for record in records]
    assert entries[0]['status'] == 'success'
    assert entries[0]['tool'] == 'get_hrv'
    assert entries[1]['status'] == 'error'
    assert entries[1]['error_type'] == 'ValueError'
    for entry in entries:
        assert entry['duration_ms'] >= 0
        assert UUID(entry['request_id'])
        assert set(entry) <= {'event', 'level', 'tool', 'status', 'duration_ms', 'request_id', 'error_type'}
    assert '2026-01-01' not in str(entries)
    assert 'secret-email' not in str(entries)
    assert 'nightly_avg' not in str(entries)


@pytest.mark.asyncio
async def test_stale_sections_preserve_metadata_and_analytics_exclude_retained_values():
    from datetime import datetime, timezone
    from garmin_mcp.storage import merge_refresh
    previous = normalize('2026-01-01', {
        'hrv': MOCK_HRV_DATA, 'status': MOCK_TRAINING_STATUS,
        'readiness': [{'score': 81, 'recoveryTime': 120}],
    })
    timestamp = '2026-01-01T12:00:00+00:00'
    previous['source_updated_at'] = {source: timestamp for source in SOURCE_METHODS}
    incoming = normalize('2026-01-01', {}, {'hrv': 'timeout', 'readiness': 'timeout', 'status': 'timeout', 'vo2max': 'timeout'})
    retained = merge_refresh(previous, incoming, datetime(2026, 1, 1, 13, tzinfo=timezone.utc))
    store = Mock()
    store.get_day.return_value = retained
    registry = Registry()
    register_tools(registry, WellnessService(FakeGarmin(), store=store))
    for name in ('get_hrv', 'get_training_overview', 'get_training_readiness', 'get_training_status', 'get_vo2max'):
        result = await registry.tools[name]('2026-01-01')
        group = 'hrv' if name == 'get_hrv' else 'training'
        assert result['stale_groups'][group]
        assert result['source_updated_at']
        assert result['source_errors']
    series = await registry.tools['get_metric_timeseries']('hrv', '2026-01-01', '2026-01-01')
    assert series['points'][0]['value'] is None
    assert series['points'][0]['reason'] == 'cached_value_stale'
    assert series['points'][0]['stale_sources']['hrv']['last_success_at'] == timestamp
    assert series['sample_count'] == 0
    assert series['missing_days'] == 1


def test_empty_default_summary_flags_do_not_fabricate_zero_measurements():
    defaults = {'includesWellnessData': False, 'includesActivityData': False,
                'includesCalorieConsumedData': False, 'totalSteps': 0,
                'totalDistanceMeters': 0, 'activeKilocalories': 0, 'restingHeartRate': 0,
                'bodyBatteryHighestValue': 0, 'averageStressLevel': 0,
                'moderateIntensityMinutes': 0, 'vigorousIntensityMinutes': 0}
    record = normalize('2010-01-01', {'health': defaults, 'hrv': MOCK_HRV_DATA})
    assert record['health']['available'] is False
    assert record['body_battery']['available'] is False
    assert record['stress']['available'] is False
    assert record['hrv']['nightly_avg'] == 48  # Independent source not suppressed.
    assert record['normalization_version'] == 2
    defaults['includesWellnessData'] = True
    record = normalize('2018-01-01', {'health': defaults})
    assert record['health']['steps'] == 0
    assert record['health']['intensity_minutes'] == 0


def test_future_training_snapshots_are_excluded_and_past_snapshot_selected():
    record = normalize('2020-01-01', {
        'readiness': [{'calendarDate': '2026-01-01', 'score': 90}],
        'status': {
            'mostRecentTrainingStatus': {'latestTrainingStatusData': {
                'future': {'calendarDate': '2026-01-01', 'trainingStatus': 'FUTURE'},
                'past': {'calendarDate': '2019-12-31', 'trainingStatus': 'PAST',
                         'acuteTrainingLoadDTO': {'calendarDate': '2026-01-01', 'dailyTrainingLoadAcute': 99}},
            }},
            'mostRecentVO2Max': {'generic': {'calendarDate': '2026-01-01', 'vo2MaxValue': 99}},
        },
        'vo2max': [{'calendarDate': '2026-01-01', 'generic': {'vo2MaxValue': 99}}],
    })
    assert record['training']['training_status'] == 'PAST'
    assert 'readiness' not in record['training']
    assert 'acute_load' not in record['training']
    assert 'vo2max' not in record['training']


@pytest.mark.asyncio
async def test_old_normalization_cache_is_refetched():
    store = Mock()
    store.get_day.return_value = {'date': '2010-01-01', 'health': {'steps': 0}, 'normalization_version': 1}
    client = FakeGarmin()
    result = await WellnessService(client, store).daily('2010-01-01')
    assert client.calls
    assert result['normalization_version'] == 2
    store.put_day.assert_called_once()


@pytest.mark.parametrize('activity,food', [(True, False), (False, True), (False, False)])
def test_nonwellness_days_keep_only_explicit_activity_totals(activity, food):
    health = {'includesWellnessData': False, 'includesActivityData': activity,
              'includesCalorieConsumedData': food, 'totalSteps': 0,
              'restingHeartRate': 0, 'bodyBatteryHighestValue': 0,
              'averageStressLevel': 0, 'moderateIntensityMinutes': 0,
              'vigorousIntensityMinutes': 0, 'totalDistanceMeters': 1000,
              'activeKilocalories': 100}
    record = normalize('2020-01-01', {'health': health})
    assert 'steps' not in record['health']
    assert 'resting_hr' not in record['health']
    assert 'intensity_minutes' not in record['health']
    assert record['stress']['available'] is False
    assert record['body_battery']['available'] is False
    if activity:
        assert record['health']['distance_meters'] == 1000
        assert record['health']['active_calories'] == 100
    else:
        assert record['health']['available'] is False
