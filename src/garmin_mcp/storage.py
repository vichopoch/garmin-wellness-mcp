"""Versioned, minimized wellness cache. No raw Garmin payloads or GPS tracks."""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import threading
from copy import deepcopy
from sqlalchemy import MetaData, Table, Column, String, Date, DateTime, JSON, ForeignKey, create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

metadata = MetaData()
profiles = Table('profiles', metadata, Column('id', String(80), primary_key=True), Column('timezone', String(80), nullable=False))
TABLE_NAMES = ('daily_health', 'sleep', 'hrv', 'body_battery', 'training', 'activities', 'sync_state')
tables = {name: Table(name, metadata,
    Column('profile_id', String(80), ForeignKey('profiles.id'), primary_key=True),
    Column('date', Date, primary_key=True), Column('data', JSON, nullable=False),
    Column('updated_at', DateTime(timezone=True), nullable=False)) for name in TABLE_NAMES}
_local_sync_lock = threading.Lock()
activities_records = Table('activities_records', metadata,
    Column('profile_id', String(80), ForeignKey('profiles.id'), primary_key=True),
    Column('activity_id', String(32), primary_key=True),
    Column('data', JSON, nullable=False), Column('updated_at', DateTime(timezone=True), nullable=False))

# Each failed source retains only the fields it owns, never replacing good data
# with an unavailable response. Source timestamps describe measurement freshness.
SOURCE_FIELDS = {
    'health': {'health': ('steps','distance_meters','active_calories','resting_hr','min_hr','max_hr','intensity_minutes'), 'body_battery': ('high','low'), 'stress': ('low_seconds','medium_seconds','high_seconds')},
    'sleep': {'sleep': ('sleep_seconds','sleep_hours','sleep_score','deep_seconds','light_seconds','rem_seconds','awake_seconds','sleep_need_seconds','bedtime','wake_time','hrv_avg','skin_temperature_change'), 'naps': ('total_seconds',)},
    'hrv': {'hrv': ('nightly_avg','weekly_avg','baseline_low','baseline_high','status')},
    'body_battery': {'body_battery': ('latest','charged','drained','high','low')},
    'stress': {'stress': ('average','maximum')},
    'readiness': {'training': ('readiness','recovery_seconds')},
    'status': {'training': ('training_status','acute_load','chronic_load','load_ratio')},
    'vo2max': {'training': ('vo2max','cycling_vo2max')},
    'spo2': {'health': ('spo2_avg',)}, 'respiration': {'health': ('respiration_avg',)},
}

def merge_refresh(previous, incoming, now):
    result = deepcopy(incoming)
    errors = result.get('source_errors') or {}
    timestamps = dict(previous.get('source_updated_at') or {})
    stale = {}
    for source, groups in SOURCE_FIELDS.items():
        if source not in errors:
            timestamps[source] = now.isoformat()
            continue
        for group, fields in groups.items():
            old = previous.get(group) or {}
            current = result.setdefault(group, {})
            retained = []
            for field in fields:
                if field in old and field not in current:
                    current[field] = old[field]
                    retained.append(field)
            if retained:
                current['available'] = True
                current.pop('reason', None)
                stale.setdefault(group, {})[source] = {'fields': retained, 'last_success_at': timestamps.get(source)}
    result['source_updated_at'] = timestamps
    if stale:
        result['stale_groups'] = stale
    result['missing_metrics'] = [name for name in ('health','sleep','hrv','body_battery','stress','training','naps') if isinstance(result.get(name), dict) and not result[name].get('available')]
    return result


def database_url(url):
    if url.startswith(('postgres://', 'postgresql://')):
        return 'postgresql+psycopg://' + url.split('://', 1)[1]
    return url


def migrate(url):
    from alembic import command
    from alembic.config import Config
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parent / 'migrations'))
    config.attributes['connection_url'] = database_url(url)
    command.upgrade(config, 'head')


class Store:
    def __init__(self, url, ttl_seconds=None):
        self.engine = create_engine(database_url(url), pool_pre_ping=True, hide_parameters=True)
        self.ttl = int(ttl_seconds or os.getenv('GARMIN_CACHE_TTL_SECONDS', '900'))

    def _upsert(self, conn, table, values, keys):
        insert = pg_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        stmt = insert(table).values(**values)
        conn.execute(stmt.on_conflict_do_update(index_elements=keys, set_={k: v for k,v in values.items() if k not in keys}))

    def get_day(self, profile_id, day):
        day = date.fromisoformat(day)
        with self.engine.connect() as conn:
            row = conn.execute(select(tables['daily_health']).where(tables['daily_health'].c.profile_id == profile_id, tables['daily_health'].c.date == day)).mappings().first()
            if row is None:
                return None
            updated = row['updated_at'].replace(tzinfo=timezone.utc)
            # Recent days are mutable; historical complete rows remain cached.
            result = row['data']
            if (day >= datetime.now(timezone.utc).date() - timedelta(days=3) or result.get('source_errors')) and (datetime.now(timezone.utc)-updated).total_seconds() > self.ttl:
                return None
            return result

    def put_day(self, profile_id, day, data):
        # Only caller's normalized projection is accepted; recursively reject credentials/GPS.
        _validate_projection(data)
        now = datetime.now(timezone.utc)
        values = {'profile_id':profile_id,'date':date.fromisoformat(day),'updated_at':now}
        with self.engine.begin() as conn:
            # Lock the profile row first, including cold-cache inserts. Otherwise
            # two writers can both see a missing day and the last partial result
            # can erase the other writer's successful measurements.
            self._upsert(conn, profiles, {'id':profile_id, 'timezone':data.get('timezone','America/Santiago')}, ['id'])
            previous = conn.execute(select(tables['daily_health'].c.data).where(tables['daily_health'].c.profile_id == profile_id, tables['daily_health'].c.date == values['date']).with_for_update()).scalar()
            data = merge_refresh(previous or {}, data, now)
            self._upsert(conn, tables['daily_health'], dict(values, data=data), ['profile_id','date'])
            for name in ('sleep','hrv','body_battery','training'):
                if name in data:
                    self._upsert(conn,tables[name],dict(values,data=data[name]),['profile_id','date'])
            if 'latest_activity' in data:
                self._upsert(conn,tables['activities'],dict(values,data=data['latest_activity']),['profile_id','date'])

    def put_activities(self, profile_id, activities):
        now = datetime.now(timezone.utc)
        with self.engine.begin() as conn:
            if conn.execute(select(profiles.c.id).where(profiles.c.id == profile_id)).scalar() is None:
                self._upsert(conn, profiles, {'id': profile_id, 'timezone': 'America/Santiago'}, ['id'])
            for activity in activities:
                _validate_projection(activity)
                identifier = str(activity.get('activity_id', ''))
                if not identifier.isdigit() or not 0 < len(identifier) <= 20:
                    continue
                self._upsert(conn, activities_records, {'profile_id': profile_id, 'activity_id': identifier, 'data': activity, 'updated_at': now}, ['profile_id','activity_id'])

    def checkpoint(self, profile_id, day, report):
        with self.engine.begin() as conn:
            if conn.execute(select(profiles.c.id).where(profiles.c.id == profile_id)).scalar() is None:
                self._upsert(conn, profiles, {'id': profile_id, 'timezone': 'America/Santiago'}, ['id'])
            self._upsert(conn,tables['sync_state'],{'profile_id':profile_id,'date':date.fromisoformat(day),'data':report,'updated_at':datetime.now(timezone.utc)},['profile_id','date'])

    def has_history(self, profile_id):
        with self.engine.connect() as conn:
            return conn.execute(select(tables['sync_state'].c.date).where(tables['sync_state'].c.profile_id == profile_id).limit(1)).first() is not None

    @contextmanager
    def sync_lock(self):
        if not _local_sync_lock.acquire(blocking=False):
            yield False
            return
        try:
            with self.engine.connect() as conn:
                postgres = self.engine.dialect.name == 'postgresql'
                acquired = not postgres or conn.execute(text('SELECT pg_try_advisory_lock(702061019)')).scalar()
                try:
                    yield acquired
                finally:
                    if postgres and acquired:
                        conn.execute(text('SELECT pg_advisory_unlock(702061019)'))
        finally:
            _local_sync_lock.release()


def _validate_projection(data):
    forbidden = {'password','email','authorization','cookies','token','access_token','refresh_token','latitude','longitude','gps','polyline','startLatitude','startLongitude','endLatitude','endLongitude'}
    if isinstance(data,dict):
        for key,value in data.items():
            if key in forbidden:
                raise ValueError('Unnormalized sensitive field rejected')
            _validate_projection(value)
    elif isinstance(data,list):
        for item in data:
            _validate_projection(item)
