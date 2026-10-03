"""Read-only cached history coverage and bounded monthly summaries.

No Garmin requests, sync triggering, credentials, or raw health payloads leave
this module. Queries always bind the personal profile and optional date bounds.
"""
from __future__ import annotations

import calendar
import math
import re
import statistics
from datetime import date
from typing import Any

from sqlalchemy import func, select

from .storage import SOURCE_FIELDS, tables

MAX_MONTHS = 400


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Use ISO dates YYYY-MM-DD')
    return date.fromisoformat(value)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _progress(job: Any) -> dict:
    """Project operational progress only; never copy arbitrary checkpoint payloads."""
    data = _mapping(job)
    result = {}
    for key in ('status', 'phase', 'stop_reason'):
        value = data.get(key)
        if isinstance(value, str) and re.fullmatch(r'[a-zA-Z_]{1,48}', value):
            result[key] = value
    for key in ('start_date', 'end_date', 'next_date', 'first_date', 'last_date',
                'earliest_date', 'discovered_start', 'cursor_date', 'target_date', 'first_observed', 'last_observed',
                'first_activity_date', 'last_activity_date'):
        value = data.get(key)
        try:
            parsed = _date(value)
        except (ValueError, TypeError):
            continue
        if parsed:
            result[key] = parsed.isoformat()
    for key in ('days_requested', 'days_processed', 'days_successful', 'days_missing',
                'error_count', 'activities_imported', 'empty_streak', 'consecutive_empty_days',
                'days_with_data', 'missing_days_count', 'activities_offset', 'activities_pages', 'activities_seen'):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    if isinstance(data.get('activities_complete'), bool):
        result['activities_complete'] = data['activities_complete']
    if data.get('error'):
        result['has_error'] = True
        error = _mapping(data['error'])
        safe_error = {}
        if error.get('stage') in {'daily', 'activities', 'batch'}:
            safe_error['stage'] = error['stage']
        reasons = {'source_errors', 'authentication_required', 'rate_limited', 'timeout', 'upstream_error',
                   'unsupported_method', 'invalid_page', 'invalid_activity_id', 'pagination_not_advancing', 'batch_failure'}
        if error.get('reason') in reasons:
            safe_error['reason'] = error['reason']
        try:
            error_date = _date(error.get('date'))
        except (ValueError, TypeError):
            error_date = None
        if error_date:
            safe_error['date'] = error_date.isoformat()
        if isinstance(error.get('offset'), int) and not isinstance(error['offset'], bool) and error['offset'] >= 0:
            safe_error['offset'] = error['offset']
        sources = {key: value for key, value in _mapping(error.get('sources')).items()
                   if key in SOURCE_FIELDS and value in reasons}
        if sources:
            safe_error['sources'] = sources
        result['error'] = safe_error
    return result


def history_overview(store: Any, profile_id: str, metric_definitions: dict,
                     metrics: list[str] | None = None, start_date: str | None = None,
                     end_date: str | None = None) -> dict[str, Any]:
    """Stream cached rows into at most 400 monthly buckets, bound to one profile."""
    from .wellness import NORMALIZATION_VERSION

    selected = ['sleep_score', 'hrv', 'resting_hr'] if metrics is None else metrics
    if not 1 <= len(selected) <= 13 or len(set(selected)) != len(selected):
        raise ValueError('Provide 1–13 distinct metrics')
    if any(metric not in metric_definitions for metric in selected):
        raise ValueError('Unknown metric; use one of: ' + ', '.join(metric_definitions))
    start, end = _date(start_date), _date(end_date)
    if start is not None and end is not None and start > end:
        raise ValueError('start_date must not be after end_date')
    if start and end and (end.year - start.year) * 12 + end.month - start.month + 1 > MAX_MONTHS:
        raise ValueError('Cached history summaries support at most 400 calendar months; request a smaller date range')
    if store is None:
        return {'available': False, 'reason': 'Historical cache is not configured',
                'source': 'normalized_cache_only', 'sync_progress': {}}
    table = tables['daily_health']
    conditions = [table.c.profile_id == profile_id]
    if start:
        conditions.append(table.c.date >= start)
    if end:
        conditions.append(table.c.date <= end)
    progress_reader = getattr(store, 'get_sync_job', None)
    progress = _progress(progress_reader(profile_id, 'history')) if callable(progress_reader) else {}
    with store.engine.connect() as conn:
        first, last, row_count = conn.execute(select(func.min(table.c.date), func.max(table.c.date),
                                                   func.count()).where(*conditions)).one()
        if first is None:
            return {'available': False, 'reason': 'No cached daily history in the requested range',
                    'source': 'normalized_cache_only', 'sync_progress': progress,
                    'coverage': {'first_cached_date': None, 'last_cached_date': None, 'cached_days': 0},
                    'monthly': [], 'metrics': {}}
        # Without explicit dates the covered cache interval defines expected days.
        effective_start, effective_end = start or first, end or last
        months_count = (effective_end.year - effective_start.year) * 12 + effective_end.month - effective_start.month + 1
        if months_count > MAX_MONTHS:
            raise ValueError('Cached history summaries support at most 400 calendar months; request a smaller date range')
        buckets = {}
        year, month = effective_start.year, effective_start.month
        for _ in range(months_count):
            month_start = max(effective_start, date(year, month, 1))
            month_end = min(effective_end, date(year, month, calendar.monthrange(year, month)[1]))
            buckets[f'{year:04d}-{month:02d}'] = {
                'calendar_days': (month_end - month_start).days + 1,
                'cached_days': 0, 'source_error_days': 0, 'legacy_days_excluded': 0,
                'metrics': {metric: {'values': [], 'stale_days': 0} for metric in selected},
            }
            month += 1
            if month == 13:
                year, month = year + 1, 1
        coverage = {metric: {'first_observed_date': None, 'last_observed_date': None,
                             'sample_count': 0, 'stale_days_excluded': 0,
                             'unit': metric_definitions[metric][2]} for metric in selected}
        source_error_days = 0
        legacy_days_excluded = 0
        query = select(table.c.date, table.c.data).where(*conditions).order_by(table.c.date)
        for day, payload in conn.execution_options(stream_results=True).execute(query).yield_per(128):
            data = _mapping(payload)
            bucket = buckets[day.strftime('%Y-%m')]
            bucket['cached_days'] += 1
            if data.get('normalization_version') != NORMALIZATION_VERSION:
                bucket['legacy_days_excluded'] += 1
                legacy_days_excluded += 1
                continue
            errors = _mapping(data.get('source_errors'))
            if errors:
                bucket['source_error_days'] += 1
                source_error_days += 1
            for metric in selected:
                group, field, _ = metric_definitions[metric]
                group_stale = _mapping(_mapping(data.get('stale_groups')).get(group))
                stale = any(field in _mapping(metadata).get('fields', []) for metadata in group_stale.values())
                value = _mapping(data.get(group)).get(field)
                # For old cache versions lacking stale_groups, a failed owning
                # source is conservatively excluded as well.
                failed_owner = any(field in SOURCE_FIELDS.get(source, {}).get(group, ()) for source in errors)
                if (stale or failed_owner) and _number(value):
                    bucket['metrics'][metric]['stale_days'] += 1
                    coverage[metric]['stale_days_excluded'] += 1
                    continue
                if not _number(value):
                    continue
                bucket['metrics'][metric]['values'].append(value)
                metric_coverage = coverage[metric]
                metric_coverage['sample_count'] += 1
                metric_coverage['first_observed_date'] = metric_coverage['first_observed_date'] or day.isoformat()
                metric_coverage['last_observed_date'] = day.isoformat()
    total_days = (effective_end - effective_start).days + 1
    monthly = []
    for month, bucket in buckets.items():
        aggregated = {}
        for metric, item in bucket['metrics'].items():
            values = item['values']
            aggregated[metric] = {
                'mean': statistics.mean(values) if values else None,
                'median': statistics.median(values) if values else None,
                'sample_count': len(values), 'missing_days': bucket['calendar_days'] - len(values),
                'stale_days_excluded': item['stale_days'],
            }
        monthly.append({'month': month, 'calendar_days': bucket['calendar_days'],
                        'cached_days': bucket['cached_days'], 'source_error_days': bucket['source_error_days'],
                        'legacy_days_excluded': bucket['legacy_days_excluded'],
                        'metrics': aggregated})
    for item in coverage.values():
        item['missing_days'] = total_days - item['sample_count']
    return {'available': True, 'source': 'normalized_cache_only', 'normalization_version': NORMALIZATION_VERSION,
            'sync_progress': progress,
            'coverage': {'start_date': effective_start.isoformat(), 'end_date': effective_end.isoformat(),
                         'first_cached_date': first.isoformat(), 'last_cached_date': last.isoformat(),
                         'calendar_days': total_days, 'cached_days': row_count,
                         'uncached_days': total_days - row_count, 'source_error_days': source_error_days,
                         'legacy_days_excluded': legacy_days_excluded,
                         'usable_cached_days': row_count - legacy_days_excluded},
            'metrics': coverage, 'monthly': monthly,
            'interpretation': 'Observed cached history only; cache bounds do not establish first Garmin use or completed import. Missing and stale values are excluded, never zero-filled.'}
