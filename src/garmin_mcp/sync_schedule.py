"""Wall-clock sync slots, with durable deduplication and explicit time zone."""
import asyncio
from datetime import datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo


class DailySchedule:
    def __init__(self, times: str, timezone_name: str):
        values = [value.strip() for value in times.split(',')]
        if not values or any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value) for value in values):
            raise ValueError('GARMIN_SYNC_TIMES must contain comma-separated HH:MM times')
        if len(values) != len(set(values)):
            raise ValueError('Sync times must be distinct')
        self.times = sorted(values)
        self.zone = ZoneInfo(timezone_name)
        self.signature = f'{self.zone.key}:{",".join(self.times)}'

    def slots(self, now: datetime):
        if now.tzinfo is None:
            raise ValueError('Schedule clock must be timezone-aware')
        today = now.astimezone(self.zone).date()
        for offset in range(-2, 3):
            for value in self.times:
                slot = datetime.combine(today + timedelta(days=offset), time.fromisoformat(value), self.zone)
                # Skip nonexistent spring-forward times. Fall-back wall times
                # run once, using their first occurrence (fold=0).
                if slot.astimezone(timezone.utc).astimezone(self.zone).replace(tzinfo=None) == slot.replace(tzinfo=None):
                    yield slot

    def bounds(self, now: datetime):
        candidates = sorted(self.slots(now), key=lambda slot: slot.timestamp())
        previous = max((slot for slot in candidates if slot.timestamp() <= now.timestamp()), key=lambda slot: slot.timestamp())
        following = min((slot for slot in candidates if slot.timestamp() > now.timestamp()), key=lambda slot: slot.timestamp())
        return previous, following

    def delay(self, now: datetime) -> float:
        return max(0, self.bounds(now)[1].timestamp() - now.timestamp())


async def scheduled_refresh(service, store, schedule: DailySchedule, refresh, now: datetime):
    """Run each due slot once across restarts; first activation waits for next slot.

    A restart after a missed slot catches up once, rather than replaying every
    missed occurrence. Failed attempts are recorded and the next fixed slot
    retries the recent lookback range. No startup-only extra refresh is added.
    """
    due, following = schedule.bounds(now)
    key = 'recent_schedule'
    state = await asyncio.to_thread(store.get_sync_job, service.profile_id, key)
    if not state or state.get('schedule') != schedule.signature:
        state = {'schedule': schedule.signature, 'timezone': schedule.zone.key,
                 'times': schedule.times, 'last_slot': due.isoformat(),
                 'next_run': following.isoformat(), 'status': 'waiting'}
        await asyncio.to_thread(store.put_sync_job, service.profile_id, state, key)
        return state
    if datetime.fromisoformat(state['last_slot']).timestamp() >= due.timestamp():
        return state
    state = {'schedule': schedule.signature, 'timezone': schedule.zone.key,
             'times': schedule.times, 'last_slot': due.isoformat(),
             'next_run': following.isoformat(), 'status': 'running'}
    # Persist the attempted slot before starting, so a process restart cannot
    # run it again. The regular sync's advisory lock protects actual writers.
    await asyncio.to_thread(store.put_sync_job, service.profile_id, state, key)
    try:
        report = await refresh(service, store)
        state['status'] = ('success' if report.get('range_completed') and not report.get('errors')
                           and report.get('activities', {}).get('status') != 'unavailable' else 'error')
    except Exception as exc:
        state['status'] = 'error'
        state['error_type'] = type(exc).__name__
    await asyncio.to_thread(store.put_sync_job, service.profile_id, state, key)
    return state
