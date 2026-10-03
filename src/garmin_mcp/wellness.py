"""Small read-only Wellness toolset, curated normalization and daily analytics.

Only allowlisted scalar fields leave this module: no GPS, names, email, device IDs,
raw responses or exception strings are persisted or returned. Daily metrics use the
account's IANA timezone when provided by Garmin, otherwise the configured timezone.
"""
from __future__ import annotations

import asyncio
import math
import os
import re
import statistics
import time
from contextvars import ContextVar
from datetime import date as Date, datetime, timedelta, timezone as UTC
from functools import wraps
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import uuid4

from mcp.types import ToolAnnotations

from .observability import log_event

MAX_DAYS = 365
MAX_POINTS = 2000
_CALL_BUDGET: ContextVar[list[int] | None] = ContextVar('wellness_call_budget', default=None)
SOURCE_METHODS = {
    'health': 'get_stats', 'sleep': 'get_sleep_data', 'hrv': 'get_hrv_data',
    'body_battery': 'get_body_battery', 'stress': 'get_stress_data',
    'readiness': 'get_training_readiness', 'status': 'get_training_status',
    'vo2max': 'get_max_metrics', 'spo2': 'get_spo2_data',
    'respiration': 'get_respiration_data',
}
METRICS = {
    'steps': ('health', 'steps', 'steps'),
    'resting_hr': ('health', 'resting_hr', 'bpm'),
    'sleep_duration': ('sleep', 'sleep_seconds', 'seconds'),
    'sleep_score': ('sleep', 'sleep_score', 'score'),
    'sleep_need': ('sleep', 'sleep_need_seconds', 'seconds'),
    'hrv': ('hrv', 'nightly_avg', 'ms'),
    'body_battery': ('body_battery', 'latest', 'score'),
    'stress': ('stress', 'average', 'score'),
    'training_readiness': ('training', 'readiness', 'score'),
    'training_load': ('training', 'acute_load', 'load'),
    'vo2max': ('training', 'vo2max', 'ml/kg/min'),
    'spo2': ('health', 'spo2_avg', 'percent'),
    'respiration': ('health', 'respiration_avg', 'breaths/min'),
}
GROUP_SOURCES = {
    'health': ('health', 'spo2', 'respiration'), 'sleep': ('sleep',),
    'naps': ('sleep',), 'hrv': ('hrv',), 'body_battery': ('body_battery', 'health'),
    'stress': ('stress', 'health'), 'training': ('readiness', 'status', 'vo2max'),
}


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def pick(data: Any, *keys: str) -> Any:
    for key in keys:
        value = as_dict(data).get(key)
        if isinstance(value, str):
            return value[:160]
        if number(value) and value >= 0:
            return value
    return None


def compact(**values: Any) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


def measured(values: dict, errors: list[str] | None = None) -> dict[str, Any]:
    if values:
        return {'available': True, **values}
    return {'available': False, 'reason': errors[0] if errors else
            'Metric not provided by this Garmin account/device for this date'}


def first_record(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        records = [value for value in payload if isinstance(value, dict)]
        return max(records, key=lambda value: str(value.get('timestampLocal') or value.get('timestamp') or value.get('calendarDate') or '')) if records else {}
    return as_dict(payload)


def iso_timestamp(milliseconds: Any) -> str | None:
    if not number(milliseconds):
        return None
    try:
        return datetime.fromtimestamp(milliseconds / 1000, UTC.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def normalize(day: str, payloads: dict, errors: dict | None = None) -> dict[str, Any]:
    """Normalize supported Garmin response variants without synthesizing measurements."""
    errors = errors or {}
    h = as_dict(payloads.get('health'))
    raw_sleep = as_dict(payloads.get('sleep'))
    s = as_dict(raw_sleep.get('dailySleepDTO'))
    score = as_dict(as_dict(s.get('sleepScores')).get('overall'))
    need = as_dict(s.get('sleepNeed'))
    sleep = compact(sleep_seconds=pick(s, 'sleepTimeSeconds'), sleep_score=pick(score, 'value'),
                    deep_seconds=pick(s, 'deepSleepSeconds'), light_seconds=pick(s, 'lightSleepSeconds'),
                    rem_seconds=pick(s, 'remSleepSeconds'), awake_seconds=pick(s, 'awakeSleepSeconds'),
                    sleep_need_seconds=pick(s, 'sleepNeedSeconds'),
                    bedtime=iso_timestamp(s.get('sleepStartTimestampGMT')),
                    wake_time=iso_timestamp(s.get('sleepEndTimestampGMT')),
                    hrv_avg=pick(raw_sleep, 'avgOvernightHrv'),
                    skin_temperature_change=pick(s, 'avgSkinTempDeviationC'))
    # Temperature deviations may legitimately be negative.
    if number(s.get('avgSkinTempDeviationC')):
        sleep['skin_temperature_change'] = s['avgSkinTempDeviationC']
    if 'sleep_need_seconds' not in sleep and number(need.get('actual')):
        sleep['sleep_need_seconds'] = need['actual'] * 60
    if 'sleep_seconds' in sleep:
        sleep['sleep_hours'] = round(sleep['sleep_seconds'] / 3600, 3)
    hrv = as_dict(as_dict(payloads.get('hrv')).get('hrvSummary'))
    baseline = as_dict(hrv.get('baseline'))
    hrv_values = compact(nightly_avg=pick(hrv, 'lastNightAvg'), weekly_avg=pick(hrv, 'weeklyAvg'),
                         baseline_low=pick(baseline, 'balancedLow'), baseline_high=pick(baseline, 'balancedUpper'),
                         status=pick(hrv, 'status'))
    bb = first_record(payloads.get('body_battery'))
    samples = []
    for row in bb.get('bodyBatteryValuesArray') or bb.get('bodyBatteryValuesList') or []:
        value = row[-1] if isinstance(row, list) and len(row) > 1 else as_dict(row).get('value')
        if number(value) and 0 <= value <= 100:
            samples.append(value)
    body = compact(latest=pick(bb, 'bodyBatteryMostRecentValue'), charged=pick(bb, 'charged', 'chargedValue'),
                   drained=pick(bb, 'drained', 'drainedValue'), high=pick(h, 'bodyBatteryHighestValue'),
                   low=pick(h, 'bodyBatteryLowestValue'))
    if samples:
        body.setdefault('latest', samples[-1])
        body.setdefault('high', max(samples))
        body.setdefault('low', min(samples))
    stress = as_dict(payloads.get('stress'))
    stress_values = compact(average=pick(stress, 'avgStressLevel'), maximum=pick(stress, 'maxStressLevel'),
                            low_seconds=pick(h, 'lowStressDuration'), medium_seconds=pick(h, 'mediumStressDuration'),
                            high_seconds=pick(h, 'highStressDuration'))
    if 'average' not in stress_values and pick(h, 'averageStressLevel') is not None:
        stress_values['average'] = pick(h, 'averageStressLevel')
    ready = first_record(payloads.get('readiness'))
    status = as_dict(payloads.get('status'))
    devices = as_dict(as_dict(status.get('mostRecentTrainingStatus')).get('latestTrainingStatusData'))
    device = first_record(list(devices.values()))
    load = as_dict(device.get('acuteTrainingLoadDTO'))
    vo2 = first_record(payloads.get('vo2max'))
    generic = as_dict(vo2.get('generic')) or as_dict(as_dict(status.get('mostRecentVO2Max')).get('generic'))
    cycling = as_dict(vo2.get('cycling')) or as_dict(as_dict(status.get('mostRecentVO2Max')).get('cycling'))
    training = compact(readiness=pick(ready, 'score', 'readinessScore'), training_status=pick(device, 'trainingStatus'),
                        acute_load=pick(load, 'dailyTrainingLoadAcute'), chronic_load=pick(load, 'dailyTrainingLoadChronic'),
                        load_ratio=pick(load, 'dailyAcuteChronicWorkloadRatio'),
                        vo2max=pick(generic, 'vo2MaxPreciseValue', 'vo2MaxValue'),
                        cycling_vo2max=pick(cycling, 'vo2MaxPreciseValue', 'vo2MaxValue'))
    if number(ready.get('recoveryTime')) and ready['recoveryTime'] >= 0:
        training['recovery_seconds'] = 0 if ready.get('recoveryTimeChangePhrase') == 'REACHED_ZERO' else ready['recoveryTime'] * 60
    spo2 = as_dict(payloads.get('spo2'))
    resp = as_dict(payloads.get('respiration'))
    health = compact(steps=pick(h, 'totalSteps'), distance_meters=pick(h, 'totalDistanceMeters'),
                     active_calories=pick(h, 'activeKilocalories'), resting_hr=pick(h, 'restingHeartRate'),
                     min_hr=pick(h, 'minHeartRate'), max_hr=pick(h, 'maxHeartRate'),
                     intensity_minutes=pick(h, 'intensityMinutes'),
                     spo2_avg=pick(spo2, 'averageSpO2', 'averageSpo2'),
                     respiration_avg=pick(resp, 'avgWakingRespirationValue', 'avgRespirationRate'))
    if 'intensity_minutes' not in health and number(h.get('moderateIntensityMinutes')) and number(h.get('vigorousIntensityMinutes')):
        health['intensity_minutes'] = h['moderateIntensityMinutes'] + 2 * h['vigorousIntensityMinutes']
    naps = compact(total_seconds=pick(s, 'napTimeSeconds'))
    result = {'date': day}
    for name, values in [('health', health), ('sleep', sleep), ('hrv', hrv_values), ('body_battery', body),
                         ('stress', stress_values), ('training', training), ('naps', naps)]:
        result[name] = measured(values, [errors[source] for source in GROUP_SOURCES[name] if source in errors])
    result['missing_metrics'] = [name for name in GROUP_SOURCES if not result[name]['available']]
    result['source_errors'] = errors
    return result


def normalize_activity(raw: Any) -> dict[str, Any]:
    data = as_dict(raw)
    summary = as_dict(data.get('summaryDTO')) or data
    return compact(activity_id=pick(data, 'activityId'), start_time=pick(summary, 'startTimeLocal', 'startTimeGMT'),
                   activity_type=pick(as_dict(data.get('activityType') or data.get('activityTypeDTO')), 'typeKey'),
                   duration_seconds=pick(summary, 'duration'), distance_meters=pick(summary, 'distance'),
                   calories=pick(summary, 'calories'), avg_hr=pick(summary, 'averageHR'), max_hr=pick(summary, 'maxHR'),
                   training_effect=pick(summary, 'aerobicTrainingEffect', 'trainingEffect'),
                   training_load=pick(summary, 'activityTrainingLoad'))


def date_range(start: str, end: str) -> list[str]:
    if not all(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) for value in (start, end)):
        raise ValueError('Use ISO dates YYYY-MM-DD')
    first, last = Date.fromisoformat(start), Date.fromisoformat(end)
    count = (last - first).days + 1
    if not 1 <= count <= MAX_DAYS:
        raise ValueError('Date range must contain between 1 and 365 inclusive days')
    return [(first + timedelta(days=index)).isoformat() for index in range(count)]


def summary(values: list[Any]) -> dict[str, Any]:
    present = [value for value in values if number(value)]
    return {'mean': statistics.mean(present) if present else None,
            'median': statistics.median(present) if present else None,
            'min': min(present) if present else None, 'max': max(present) if present else None,
            'sample_count': len(present), 'missing_days': len(values) - len(present)}


def slope(values: list[Any]) -> float | None:
    pairs = [(index, value) for index, value in enumerate(values) if number(value)]
    if len(pairs) < 2:
        return None
    mx, my = statistics.mean(x for x, _ in pairs), statistics.mean(y for _, y in pairs)
    return sum((x - mx) * (y - my) for x, y in pairs) / sum((x - mx) ** 2 for x, _ in pairs)


class WellnessService:
    def __init__(self, client: Any, store: Any = None, timezone: str = 'America/Santiago',
                 profile_id: str = 'garmin-profile-personal', call_timeout: float = 90,
                 cache_ttl: int | None = None):
        self.client, self.store, self.profile_id = client, store, profile_id
        self.timezone = ZoneInfo(timezone)
        self.call_timeout = call_timeout
        self.cache_ttl = cache_ttl if cache_ttl is not None else int(os.getenv('GARMIN_CACHE_TTL_SECONDS', '900'))
        self._semaphore = asyncio.Semaphore(2)
        self._cache: dict[tuple, tuple[float, Any, str | None]] = {}
        self._timezone_checked = False

    async def call(self, method: str, *args: Any) -> tuple[Any, str | None]:
        if method not in {*SOURCE_METHODS.values(), 'get_user_profile', 'get_activities', 'get_activity'}:
            raise ValueError('Garmin method is not in the read-only Wellness allowlist')
        key = (method, *args)
        cached = self._cache.get(key)
        if cached and time.monotonic() - cached[0] < self.cache_ttl:
            return cached[1], cached[2]
        budget = _CALL_BUDGET.get()
        if budget is not None:
            if budget[0] <= 0:
                raise ValueError('Cold query exceeded 60 Garmin requests. Run historical sync first or request a smaller range.')
            budget[0] -= 1
        fn = getattr(self.client, method, None)
        if not callable(fn):
            return None, 'unsupported_method'
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self.call_timeout)
        except TimeoutError:
            return None, 'timeout'
        task = asyncio.create_task(asyncio.to_thread(fn, *args))
        # A timeout never releases the permit while a synchronous request still runs.
        def finished(future: asyncio.Task) -> None:
            self._semaphore.release()
            if not future.cancelled():
                future.exception()  # Retrieve exceptions after a caller timed out.
        task.add_done_callback(finished)
        try:
            data = await asyncio.wait_for(asyncio.shield(task), timeout=self.call_timeout)
            self._cache[key] = (time.monotonic(), data, None)
            # Bounded process cache; persistent normalized store holds the history.
            if len(self._cache) > 4096:
                self._cache.pop(next(iter(self._cache)))
            return data, None
        except TimeoutError:
            return None, 'timeout'
        except Exception as error:
            # No upstream exception text: it can contain URLs, identifiers or secrets.
            code = getattr(getattr(error, 'response', None), 'status_code', None)
            label = type(error).__name__.lower()
            reason = ('authentication_required' if code in (401, 403) or 'authentication' in label
                      else 'rate_limited' if code == 429 or 'toomanyrequests' in label
                      else 'upstream_error')
            return None, reason

    async def resolve_timezone(self) -> None:
        if self._timezone_checked:
            return
        self._timezone_checked = True
        profile, _ = await self.call('get_user_profile')
        for record in (as_dict(profile), as_dict(as_dict(profile).get('userData')),
                       as_dict(as_dict(profile).get('userSleep'))):
            candidate = record.get('timeZone') or record.get('timeZoneId') or record.get('timezone')
            candidate = as_dict(candidate).get('timeZoneId') if isinstance(candidate, dict) else candidate
            if isinstance(candidate, str):
                try:
                    self.timezone = ZoneInfo(candidate)
                    return
                except (ZoneInfoNotFoundError, ValueError):
                    pass

    async def today(self) -> str:
        await self.resolve_timezone()
        return datetime.now(self.timezone).date().isoformat()

    async def daily(self, date: str, refresh: bool = False, groups: tuple[str, ...] | None = None) -> dict[str, Any]:
        date_range(date, date)
        if not refresh and self.store is not None:
            cached = await asyncio.to_thread(self.store.get_day, self.profile_id, date)
            if cached is not None:
                return cached
        sources = set(SOURCE_METHODS) if groups is None else {source for group in groups for source in GROUP_SOURCES[group]}
        if refresh:
            for source in sources:
                self._cache.pop((SOURCE_METHODS[source], date), None)
        names = sorted(sources)
        results = await asyncio.gather(*(self.call(SOURCE_METHODS[source], date) for source in names))
        payloads = {source: result[0] for source, result in zip(names, results)}
        errors = {source: result[1] for source, result in zip(names, results) if result[1]}
        result = normalize(date, payloads, errors)
        result['timezone'] = str(self.timezone)
        if groups is None and self.store is not None:
            await asyncio.to_thread(self.store.put_day, self.profile_id, date, result)
        return result

    async def series(self, metric: str, start: str, end: str) -> list[dict]:
        if metric not in METRICS:
            raise ValueError('Unknown metric; use one of: ' + ', '.join(METRICS))
        group, field, _ = METRICS[metric]
        rows = []
        # Serial dates, at most two concurrent source calls per date; no request bursts.
        for day in date_range(start, end):
            record = await self.daily(day, groups=(group,))
            value = record[group].get(field)
            stale = as_dict(as_dict(record.get('stale_groups')).get(group))
            field_stale = {source: metadata for source, metadata in stale.items()
                           if field in as_dict(metadata).get('fields', [])}
            row = {'date': day, 'value': value if number(value) and not field_stale else None}
            if field_stale:
                row['reason'] = 'cached_value_stale'
                row['stale_sources'] = field_stale
            elif not number(value):
                row['reason'] = next(iter(record.get('source_errors', {}).values()), 'metric_not_provided')
            rows.append(row)
        return rows


def register_tools(app: Any, service: WellnessService) -> Any:
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    meta = {'securitySchemes': [{'type': 'oauth2', 'scopes': ['garmin:read']}]}

    def tool(name: str | None = None, profile: bool = False):
        def decorate(fn):
            @wraps(fn)
            async def bounded(*args, **kwargs):
                token = _CALL_BUDGET.set([60])
                started = time.monotonic()
                request_id = str(uuid4())
                status, error_type = 'success', None
                try:
                    result = await fn(*args, **kwargs)
                    if isinstance(result, dict) and (result.get('source_errors') or result.get('available') is False):
                        status = 'unavailable'
                    return result
                except BaseException as error:
                    status, error_type = 'error', type(error).__name__
                    raise
                finally:
                    _CALL_BUDGET.reset(token)
                    log_event('mcp_tool_call', tool=fn.__name__, status=status,
                              duration_ms=(time.monotonic() - started) * 1000,
                              request_id=request_id, error_type=error_type)
            return app.tool(name=name, annotations=annotations,
                            meta={**meta, **({'openai/profile': True} if profile else {})},
                            structured_output=True)(bounded)
        return decorate

    @tool(profile=True)
    async def get_profile() -> dict[str, Any]:
        """Return the authenticated personal Garmin profile's stable opaque ID. Contains no email."""
        return {'id': service.profile_id, 'name': 'Garmin Wellness'}

    @tool()
    async def get_capabilities() -> dict[str, Any]:
        """Report metrics observed today or yesterday. False means not observed, not proof a device lacks support."""
        today = await service.today()
        yesterday = (Date.fromisoformat(today) - timedelta(days=1)).isoformat()
        records = [await service.daily(today), await service.daily(yesterday)]
        result = {metric: any(number(row[group].get(field)) for row in records)
                  for metric, (group, field, _) in METRICS.items()}
        result['sleep'] = result['sleep_duration']
        result['skin_temperature'] = any(number(row['sleep'].get('skin_temperature_change')) for row in records)
        return {'metrics': result, 'observation_dates': [yesterday, today],
                'interpretation': 'False means not observed during these dates; support is unknown when data is absent.',
                'source_errors': {row['date']: row['source_errors'] for row in records if row['source_errors']}}

    @tool()
    async def get_wellness_today() -> dict[str, Any]:
        """Answer how the user is doing today in one compact read-only call: sleep, HRV, recovery, stress, training and latest activity. Not medical advice."""
        day = await service.today()
        result = dict(await service.daily(day))
        activities, error = await service.call('get_activities', 0, 1)
        result['latest_activity'] = measured(normalize_activity(first_record(activities)), [error] if error else None)
        return result

    @tool()
    async def get_daily_health(date: str) -> dict[str, Any]:
        """Return compact daily health, sleep and recovery measurements for an ISO date, with explicit missing data and source errors. Read-only."""
        return await service.daily(date)

    @tool()
    async def get_health_range(start_date: str, end_date: str) -> dict[str, Any]:
        """Return normalized daily summaries for an inclusive range up to 31 days; use metric timeseries for longer compact ranges. Read-only."""
        days = date_range(start_date, end_date)
        if len(days) > 31:
            raise ValueError('Full health summaries are limited to 31 days; use get_metric_timeseries for up to 365 days')
        return {'days': [await service.daily(day) for day in days]}

    async def section(date: str, group: str) -> dict[str, Any]:
        day = date or await service.today()
        record = await service.daily(day, groups=(group,))
        freshness = {}
        timestamps = as_dict(record.get('source_updated_at'))
        if timestamps:
            freshness['source_updated_at'] = {source: timestamps[source] for source in GROUP_SOURCES[group] if source in timestamps}
        stale = as_dict(record.get('stale_groups'))
        if group in stale:
            freshness['stale_groups'] = {group: stale[group]}
        return {'date': day, **record[group], 'source_errors': record['source_errors'], **freshness}

    def training_projection(data: dict, fields: tuple[str, ...]) -> dict[str, Any]:
        freshness = {key: data[key] for key in ('source_updated_at', 'stale_groups') if key in data}
        return {'date': data['date'], **measured({key: data[key] for key in fields if key in data}),
                'source_errors': data['source_errors'], **freshness}

    @tool()
    async def get_sleep(date: str = '') -> dict[str, Any]:
        """Read sleep duration, phases, score, need and timing for the wake date YYYY-MM-DD, default today. Missing measurements remain unavailable."""
        return await section(date, 'sleep')

    @tool()
    async def get_sleep_analysis(start_date: str, end_date: str) -> dict[str, Any]:
        """Analyze sleep duration and score over up to 365 days with mean, median and missing-day counts. Not medical advice."""
        return {metric: summary([row['value'] for row in await service.series(metric, start_date, end_date)])
                for metric in ('sleep_duration', 'sleep_score', 'sleep_need')}

    @tool()
    async def get_naps(date: str = '') -> dict[str, Any]:
        """Read total nap duration in seconds reported with Garmin sleep data for an ISO date. Zero is distinct from unavailable."""
        return await section(date, 'naps')

    @tool()
    async def get_recovery_context(days: int = 30) -> dict[str, Any]:
        """Compare today's recovery with the preceding 2–90 days: median baseline, difference, mean and linear slope/day. Not medical advice."""
        if not 2 <= days <= 90:
            raise ValueError('days must be between 2 and 90')
        today = await service.today()
        start = (Date.fromisoformat(today) - timedelta(days=days)).isoformat()
        result = {}
        for metric in ('sleep_score', 'sleep_duration', 'hrv', 'resting_hr', 'body_battery', 'stress', 'training_readiness'):
            values = [row['value'] for row in await service.series(metric, start, today)]
            baseline, current = summary(values[:-1]), values[-1]
            result[metric] = {'today': current, 'baseline': baseline,
                              'change': current - baseline['median'] if number(current) and baseline['median'] is not None else None,
                              'trend_per_day': slope(values), 'unit': METRICS[metric][2]}
        return {'date': today, 'baseline_days': days, 'baseline_statistic': 'median (today excluded)', 'metrics': result}

    @tool()
    async def get_hrv(date: str = '') -> dict[str, Any]:
        """Read nightly and weekly HRV in ms, personal baseline and status for an ISO date; use for recovery and fatigue context, never diagnosis."""
        return await section(date, 'hrv')

    @tool()
    async def get_body_battery(date: str = '') -> dict[str, Any]:
        """Read latest, high, low, charged and drained Body Battery for an ISO date. Compact daily summary, no unbounded intraday payload."""
        return await section(date, 'body_battery')

    @tool()
    async def get_stress(date: str = '') -> dict[str, Any]:
        """Read daily average/max Garmin stress and low/medium/high duration seconds for an ISO date. Not medical advice."""
        return await section(date, 'stress')

    @tool()
    async def get_training_overview(date: str = '') -> dict[str, Any]:
        """Read training readiness, status, acute/chronic load, recovery time and VO2 max in one compact dated summary."""
        return await section(date, 'training')

    @tool()
    async def get_training_readiness(date: str = '') -> dict[str, Any]:
        """Read latest dated training readiness score and recovery seconds when supported. Missing device metrics degrade gracefully."""
        data = await section(date, 'training')
        return training_projection(data, ('readiness', 'recovery_seconds'))

    @tool()
    async def get_training_status(date: str = '') -> dict[str, Any]:
        """Read Garmin training status and acute/chronic load for an ISO date to contextualize training and recovery."""
        data = await section(date, 'training')
        return training_projection(data, ('training_status', 'acute_load', 'chronic_load', 'load_ratio'))

    @tool()
    async def get_vo2max(date: str = '') -> dict[str, Any]:
        """Read running and cycling VO2 max estimates in ml/kg/min for an ISO date, retaining Garmin's available precision."""
        data = await section(date, 'training')
        return training_projection(data, ('vo2max', 'cycling_vo2max'))

    @tool()
    async def get_activities(limit: int = 10, offset: int = 0) -> dict[str, Any]:
        """Read recent activity summaries with duration, distance, heart rate, training effect and load. Maximum 50; no GPS tracks or personal titles."""
        if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
            raise ValueError('limit must be 1–50 and offset 0–10000')
        data, error = await service.call('get_activities', offset, limit)
        rows = data if isinstance(data, list) else []
        return {'available': error is None, 'activities': [normalize_activity(row) for row in rows[:limit]],
                **({'reason': error} if error else {})}

    @tool()
    async def get_activity(activity_id: str) -> dict[str, Any]:
        """Read a single Garmin activity summary by numeric ID. No modification, GPS coordinates, tracks, names or free-text descriptions."""
        if not re.fullmatch(r'[1-9]\d{0,19}', activity_id):
            raise ValueError('activity_id must be a positive numeric ID')
        data, error = await service.call('get_activity', activity_id)
        return measured(normalize_activity(data), [error] if error else None)

    @tool()
    async def get_metric_timeseries(metric: str, start_date: str, end_date: str, max_points: int = 365) -> dict[str, Any]:
        """Read compact daily timeseries up to 365 days/2000 points. Missing values stay null; reduce date range if max_points would be exceeded. Use get_capabilities for observed metrics."""
        if not 1 <= max_points <= MAX_POINTS:
            raise ValueError('max_points must be 1–2000')
        if len(date_range(start_date, end_date)) > max_points:
            raise ValueError('Requested range exceeds max_points; reduce the range or increase max_points')
        rows = await service.series(metric, start_date, end_date)
        return {'metric': metric, 'unit': METRICS[metric][2], 'points': rows,
                **summary([row['value'] for row in rows])}

    @tool()
    async def get_metric_trend(metric: str, start_date: str, end_date: str, rolling_window: int = 7) -> dict[str, Any]:
        """Read daily metric values, trailing available-sample rolling mean and linear slope/day, up to 365 days. Includes missing counts; not a clinical prediction."""
        if not 1 <= rolling_window <= 90:
            raise ValueError('rolling_window must be 1–90')
        rows = await service.series(metric, start_date, end_date)
        values = [row['value'] for row in rows]
        for index, row in enumerate(rows):
            window = summary(values[max(0, index - rolling_window + 1):index + 1])
            row['rolling_mean'], row['rolling_sample_count'] = window['mean'], window['sample_count']
        return {'metric': metric, 'unit': METRICS[metric][2], 'rolling_window': rolling_window,
                'trend_per_day': slope(values), 'points': rows, **summary(values)}

    @tool()
    async def compare_periods(metrics: list[str], period_a_start: str, period_a_end: str,
                              period_b_start: str, period_b_end: str) -> dict[str, Any]:
        """Compare up to 10 daily metrics across two inclusive periods (365 days each). Returns mean, median, min/max, n, missing days, B−A mean and percent change."""
        if not 1 <= len(metrics) <= 10:
            raise ValueError('Provide 1–10 metrics')
        # Validate every input before initiating network requests.
        date_range(period_a_start, period_a_end)
        date_range(period_b_start, period_b_end)
        if any(metric not in METRICS for metric in metrics):
            raise ValueError('Unknown metric; use one of: ' + ', '.join(METRICS))
        result = {}
        for metric in metrics:
            a = summary([row['value'] for row in await service.series(metric, period_a_start, period_a_end)])
            b = summary([row['value'] for row in await service.series(metric, period_b_start, period_b_end)])
            difference = b['mean'] - a['mean'] if a['mean'] is not None and b['mean'] is not None else None
            result[metric] = {'period_a': a, 'period_b': b, 'difference': difference,
                              'percentage_change': difference / a['mean'] * 100 if difference is not None and a['mean'] != 0 else None,
                              'unit': METRICS[metric][2]}
        return {'comparison': 'B minus A, based on available daily means', 'metrics': result}

    @tool()
    async def find_correlations(metric_x: str, metric_y: str, start_date: str, end_date: str, lag_days: int = 0) -> dict[str, Any]:
        """Explore Pearson correlation between daily metrics; positive lag pairs X(day) with Y(day+lag), within the requested range. Correlation does not imply causation. Not medical advice."""
        if not -30 <= lag_days <= 30:
            raise ValueError('lag_days must be between -30 and 30')
        if metric_x not in METRICS or metric_y not in METRICS:
            raise ValueError('Unknown metric; use one of: ' + ', '.join(METRICS))
        x = await service.series(metric_x, start_date, end_date)
        y = {row['date']: row['value'] for row in await service.series(metric_y, start_date, end_date)}
        candidates, pairs = 0, []
        for row in x:
            target = (Date.fromisoformat(row['date']) + timedelta(days=lag_days)).isoformat()
            if target not in y:
                continue
            candidates += 1
            if number(row['value']) and number(y[target]):
                pairs.append((row['value'], y[target]))
        correlation = None
        if len(pairs) >= 3:
            xs, ys = [pair[0] for pair in pairs], [pair[1] for pair in pairs]
            mx, my = statistics.mean(xs), statistics.mean(ys)
            denominator = math.sqrt(sum((v - mx) ** 2 for v in xs) * sum((v - my) ** 2 for v in ys))
            if denominator:
                correlation = max(-1., min(1., sum((a - mx) * (b - my) for a, b in pairs) / denominator))
        return {'metric_x': metric_x, 'metric_y': metric_y, 'lag': lag_days, 'n': len(pairs),
                'correlation': correlation, 'missing_pairs': candidates - len(pairs),
                'boundary_excluded_days': len(x) - candidates,
                'reason': None if correlation is not None else 'At least 3 paired observations with nonzero variance required',
                'interpretation': 'Exploratory association only. Correlation does not imply causation. Not medical advice.'}

    return app
