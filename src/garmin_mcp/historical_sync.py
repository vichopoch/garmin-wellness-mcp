"""Durable, bounded historical import, with no 365-day total-range restriction.

Daily normalized data may commit just before the cursor. After a crash that day
is replayed from cache. Activity rows and their page cursor commit atomically.
Offset pagination is Garmin's API contract; concurrent upstream additions may
repeat rows, which are idempotent by activity ID. No raw response is persisted.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import math
import os
import time

from garmin_mcp.observability import log_event

_GROUPS = ('health', 'sleep', 'hrv', 'body_battery', 'stress', 'training', 'naps')
_SAFE_ERRORS = {'authentication_required', 'rate_limited', 'timeout', 'upstream_error', 'unsupported_method'}


def _new_state(start: str, end: str) -> dict:
    from garmin_mcp.wellness import NORMALIZATION_VERSION
    return {'normalization_version': NORMALIZATION_VERSION, 'job_key': 'history', 'start_date': start, 'target_date': end, 'next_date': start,
            'days_processed': 0, 'days_with_data': 0, 'missing_days_count': 0,
            'first_observed': None, 'last_observed': None,
            'activities_offset': 0, 'activities_pages': 0, 'activities_seen': 0,
            'activities_complete': False, 'activities_last_page': None,
            'first_activity_date': None, 'last_activity_date': None,
            'status': 'pending', 'error': None}


def _error(stage: str, reason: str, **location) -> dict:
    return {'stage': stage, 'reason': reason, **location}


async def sync_history_batch(service, store, start_date: str, max_days: int = 30, *,
                             max_seconds: float = 300, max_pages: int = 5) -> dict:
    """Resume profile's `history` job, including every activity page (100/page).

    Limits apply per invocation, never to total history. Source failures preserve
    the current day/page. Empty metric responses without errors are valid gaps.
    A changed start date starts a new cursor, reusing already-persisted days.
    Completed jobs extend to newer account dates and rescan activities from zero.
    """
    start = date.fromisoformat(start_date)
    if start.isoformat() != start_date:
        raise ValueError('start_date must be YYYY-MM-DD')
    if isinstance(max_days, bool) or not isinstance(max_days, int) or max_days < 1:
        raise ValueError('max_days must be a positive integer')
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or max_pages < 1:
        raise ValueError('max_pages must be a positive integer')
    if not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError('max_seconds must be positive and finite')
    pause = float(os.getenv('GARMIN_SYNC_DAY_PAUSE_SECONDS', '1'))
    if not math.isfinite(pause) or pause < 0:
        raise ValueError('GARMIN_SYNC_DAY_PAUSE_SECONDS must be nonnegative and finite')
    deadline = time.monotonic() + max_seconds
    with store.sync_lock() as acquired:
        if not acquired:
            return {'status': 'skipped', 'reason': 'Another sync holds the lock'}
        today = await service.today()
        end = date.fromisoformat(today)
        if start > end:
            raise ValueError('start_date must not be after the account date')
        state = await asyncio.to_thread(store.get_sync_job, service.profile_id)
        from garmin_mcp.wellness import NORMALIZATION_VERSION
        if (state is None or state['start_date'] != start_date
                or state.get('normalization_version') != NORMALIZATION_VERSION):
            state = _new_state(start_date, today)
        else:
            state = deepcopy(state)
            if today > state['target_date']:
                if state['activities_complete']:
                    state['activities_offset'] = 0
                    state['activities_complete'] = False
                    state['activities_last_page'] = None
                state['target_date'] = today
        if state['status'] == 'completed' and state['next_date'] > state['target_date']:
            return state
        state['status'], state['error'] = 'running', None
        state.pop('stop_reason', None)
        await asyncio.to_thread(store.put_sync_job, service.profile_id, state)

        async def save():
            await asyncio.to_thread(store.put_sync_job, service.profile_id, deepcopy(state))

        async def bounded(awaitable):
            return await asyncio.wait_for(awaitable, timeout=max(0.001, deadline - time.monotonic()))

        days = 0
        try:
            pages = 0
            while not state['activities_complete'] and pages < max_pages and time.monotonic() < deadline:
                offset = state['activities_offset']
                raw, error = await bounded(service.call('get_activities', offset, 100))
                if error:
                    state['status'] = 'error'
                    state['error'] = _error('activities', error if error in _SAFE_ERRORS else 'upstream_error', offset=offset)
                    await save()
                    return state
                if not isinstance(raw, list) or len(raw) > 100:
                    state['status'] = 'error'
                    state['error'] = _error('activities', 'invalid_page', offset=offset)
                    await save()
                    return state
                from garmin_mcp.wellness import normalize_activity
                rows = [normalize_activity(item) for item in raw]
                ids = [str(row.get('activity_id', '')) for row in rows]
                if any(not value.isdigit() or not 0 < len(value) <= 20 for value in ids):
                    state['status'] = 'error'
                    state['error'] = _error('activities', 'invalid_activity_id', offset=offset)
                    await save()
                    return state
                digest = hashlib.sha256(','.join(ids).encode()).hexdigest()
                if rows and digest == state['activities_last_page']:
                    state['status'] = 'error'
                    state['error'] = _error('activities', 'pagination_not_advancing', offset=offset)
                    await save()
                    return state
                next_state = deepcopy(state)
                next_state['activities_offset'] += len(raw)
                next_state['activities_pages'] += 1
                next_state['activities_seen'] += len(raw)
                next_state['activities_complete'] = len(raw) < 100
                next_state['activities_last_page'] = digest
                for row in rows:
                    candidate = str(row.get('start_time', ''))[:10]
                    try:
                        activity_day = date.fromisoformat(candidate).isoformat()
                    except ValueError:
                        continue
                    earliest = next_state.get('first_activity_date')
                    latest = next_state.get('last_activity_date')
                    next_state['first_activity_date'] = min(earliest, activity_day) if earliest else activity_day
                    next_state['last_activity_date'] = max(latest, activity_day) if latest else activity_day
                await asyncio.to_thread(store.commit_history_page, service.profile_id, rows, next_state)
                state = next_state
                pages += 1
                if pause:
                    await asyncio.sleep(min(pause, max(0, deadline - time.monotonic())))
            while state['next_date'] <= state['target_date'] and days < max_days:
                if time.monotonic() >= deadline:
                    break
                day = state['next_date']
                cached = await asyncio.to_thread(store.get_day, service.profile_id, day)
                result = await bounded(service.daily(day, refresh=bool(cached and cached.get('source_errors'))))
                errors = result.get('source_errors') or {}
                if errors:
                    state['status'] = 'error'
                    # Only known source labels/reasons, never exception text/payload.
                    from garmin_mcp.wellness import SOURCE_METHODS
                    safe = {key: reason if reason in _SAFE_ERRORS else 'upstream_error'
                            for key, reason in errors.items() if key in SOURCE_METHODS}
                    state['error'] = _error('daily', 'source_errors', date=day, sources=safe)
                    await save()
                    return state
                # Guarantee persistence even for service implementations without an
                # attached store. Replayed writes are idempotent on profile/date.
                await asyncio.to_thread(store.put_day, service.profile_id, day, result)
                observed = any(isinstance(result.get(group), dict) and result[group].get('available') for group in _GROUPS)
                state['days_processed'] += 1
                state['days_with_data'] += int(observed)
                state['missing_days_count'] += int(not observed)
                if observed:
                    state['first_observed'] = state['first_observed'] or day
                    state['last_observed'] = day
                state['next_date'] = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                days += 1
                await save()
                if pause:
                    await asyncio.sleep(min(pause, max(0, deadline - time.monotonic())))

        except asyncio.TimeoutError:
            state['stop_reason'] = 'time_budget'
        except Exception as exc:
            # Database/API exceptions may include credentials and payloads.
            state['status'] = 'error'
            state['error'] = _error('batch', 'batch_failure')
            await save()
            log_event('sync', status='error', error_type=type(exc).__name__)
            return state
        state['status'] = 'completed' if state['next_date'] > state['target_date'] and state['activities_complete'] else 'pending'
        if state['status'] == 'pending' and 'stop_reason' not in state:
            state['stop_reason'] = 'time_budget' if time.monotonic() >= deadline else 'batch_limit'
        await save()
        log_event('sync', status='success' if state['status'] == 'completed' else 'started')
        return state
