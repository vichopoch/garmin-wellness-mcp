from datetime import datetime, timezone
import asyncio
import pytest
from sqlalchemy import inspect,select
from garmin_mcp.storage import Store,migrate,tables
from garmin_mcp.sync import sync_once
from garmin_mcp.runtime import config,profile_id
from garmin_mcp.garmin_client import TokenClient,GarminUnavailable

@pytest.fixture
def store(tmp_path):
    url='sqlite:///'+str(tmp_path/'cache.db')
    migrate(url); migrate(url)
    return Store(url)

def test_migrations_upsert_and_projection(store):
    assert set(tables)<=set(inspect(store.engine).get_table_names())
    data={'date':'2025-01-01','timezone':'America/Santiago','health':{'available':True,'steps':123},'sleep':{'available':False},'source_errors':{}}
    store.put_day('opaque','2025-01-01',data)
    data['health']['steps']=456
    store.put_day('opaque','2025-01-01',data)
    assert store.get_day('opaque','2025-01-01')['health']['steps']==456
    with store.engine.connect() as conn:
        assert len(conn.execute(select(tables['daily_health'])).all())==1
    assert store.get_day('another-account','2025-01-01') is None
    with pytest.raises(ValueError):store.put_day('opaque','2025-01-02',{'latitude':-33})

def test_sync_lock(store):
    with store.sync_lock() as acquired:
        assert acquired
        with store.sync_lock() as second: assert not second
    with store.sync_lock() as third: assert third

@pytest.mark.asyncio
async def test_sync_report_and_idempotency(store,monkeypatch):
    monkeypatch.setenv('GARMIN_SYNC_DAY_PAUSE_SECONDS','0')
    class Service:
        profile_id='opaque'
        async def today(self):return '2025-01-03'
        async def daily(self,day,refresh=False):
            result={'date':day,'health':{'available':True,'steps':1},'source_errors':{}}
            store.put_day(self.profile_id,day,result)
            return result
    report=await sync_once(Service(),store,3)
    assert report['successful_days']==3
    assert report['start_date']=='2025-01-01'
    assert store.has_history('opaque')
    await sync_once(Service(),store,3)
    with store.engine.connect() as conn:assert len(conn.execute(select(tables['daily_health'])).all())==3

@pytest.mark.asyncio
async def test_failed_sync_stops_without_false_success_checkpoint(store,monkeypatch):
    monkeypatch.setenv('GARMIN_SYNC_DAY_PAUSE_SECONDS','0')
    class Service:
        profile_id='opaque'
        async def today(self):return '2025-01-03'
        async def daily(self,day,refresh=False):return {'source_errors':{'hrv':'authentication_required'}}
    report=await sync_once(Service(),store,3)
    assert report['successful_days']==0
    assert report['unattempted_days']==2
    assert not store.has_history('opaque')

def test_port_readonly_and_profile_persistence(tmp_path,monkeypatch):
    monkeypatch.setenv('PORT','9231');monkeypatch.setenv('GARMIN_MCP_PORT','8001')
    assert config()==('0.0.0.0',9231)
    assert profile_id(tmp_path)==profile_id(tmp_path)
    assert (tmp_path/'profile-id').stat().st_mode & 0o777==0o600
    monkeypatch.setenv('GARMIN_READ_ONLY','false')
    with pytest.raises(ValueError): config()

def test_token_client_never_falls_back_to_password(tmp_path,monkeypatch):
    monkeypatch.setenv('GARMIN_PASSWORD','not-used')
    c=TokenClient(str(tmp_path))
    with pytest.raises(AttributeError):c.set_weight(1)
    with pytest.raises(AttributeError):c.connectapi('/anything')
    with pytest.raises(GarminUnavailable):c.get_stats('2025-01-01')

def test_token_client_persists_refresh_and_limits_retry(tmp_path,monkeypatch):
    from unittest.mock import Mock
    from garminconnect import GarminConnectConnectionError
    (tmp_path/'garmin_tokens.json').write_text('{}')
    api=Mock();api.get_stats.side_effect=[GarminConnectConnectionError('private'),{'totalSteps':4}]
    c=TokenClient(str(tmp_path),factory=Mock(return_value=api))
    monkeypatch.setattr('garmin_mcp.garmin_client.time.sleep',lambda _:None)
    assert c.get_stats('2025-01-01')=={'totalSteps':4}
    assert api.get_stats.call_count==2
    api.client.dump.assert_called_once_with(str(tmp_path))
    assert c.last_success


def test_partial_refresh_preserves_metrics_with_original_source_timestamp(store):
    first = {'date':'2025-01-01','health':{'available':True,'steps':100,'spo2_avg':97},
             'sleep':{'available':True,'sleep_score':88},'source_errors':{}}
    store.put_day('opaque','2025-01-01',first)
    original = store.get_day('opaque','2025-01-01')
    second = {'date':'2025-01-01','health':{'available':True,'steps':200},
              'sleep':{'available':False,'reason':'upstream_error'},
              'source_errors':{'sleep':'upstream_error','spo2':'upstream_error'}}
    store.put_day('opaque','2025-01-01',second)
    merged = store.get_day('opaque','2025-01-01')
    assert merged['health']['steps'] == 200
    assert merged['health']['spo2_avg'] == 97
    assert merged['sleep']['sleep_score'] == 88
    assert merged['source_updated_at']['sleep'] == original['source_updated_at']['sleep']
    assert merged['stale_groups']['sleep']['sleep']['last_success_at'] == original['source_updated_at']['sleep']
    assert merged['stale_groups']['health']['spo2']['fields'] == ['spo2_avg']
    store.put_day('opaque','2025-01-01',first)
    assert 'stale_groups' not in store.get_day('opaque','2025-01-01')


@pytest.mark.asyncio
async def test_optional_metric_errors_finish_backfill_then_use_incremental_range(store, monkeypatch):
    monkeypatch.setenv('GARMIN_SYNC_DAY_PAUSE_SECONDS','0')
    monkeypatch.setenv('GARMIN_INITIAL_BACKFILL_DAYS','4')
    monkeypatch.setenv('GARMIN_SYNC_LOOKBACK_DAYS','2')
    class Service:
        profile_id='opaque'
        async def today(self):return '2025-01-04'
        async def daily(self,day,refresh=False):
            # Oldest date unavailable is not a global authentication outage.
            data={'date':day,'health':{'available':day != '2025-01-01','steps':1},'source_errors':{'spo2':'unsupported_method'}}
            store.put_day(self.profile_id,day,data)
            return data
    first = await sync_once(Service(),store)
    assert first['attempted_days'] == 4 and first['range_completed']
    assert store.has_history('opaque')
    second = await sync_once(Service(),store)
    assert second['requested_days'] == 2


@pytest.mark.asyncio
async def test_activity_sync_upserts_ids_and_reports_bounded_historical_coverage(store, monkeypatch):
    from garmin_mcp.storage import activities_records
    monkeypatch.setenv('GARMIN_SYNC_DAY_PAUSE_SECONDS','0')
    class Service:
        profile_id='opaque'
        async def today(self):return '2025-01-03'
        async def daily(self,day,refresh=False):
            data={'date':day,'health':{'available':True,'steps':1},'source_errors':{}}
            store.put_day(self.profile_id,day,data)
            return data
        async def call(self,method,*args):
            assert (method,args) == ('get_activities',(0,100))
            return [{'activityId':i,'startTimeLocal':'2025-01-03 08:00:00','duration':50,'startLatitude':-33} for i in range(1,101)],None
    first = await sync_once(Service(),store,3)
    assert first['activities']['truncated']
    assert not first['activities']['historical_coverage_complete']
    await sync_once(Service(),store,3)
    with store.engine.connect() as conn:
        rows=conn.execute(select(activities_records)).mappings().all()
    assert len(rows)==100
    assert all('startLatitude' not in row['data'] for row in rows)
    assert {row['activity_id'] for row in rows} == {str(i) for i in range(1,101)}


def test_concurrent_partial_first_writes_merge_without_losing_good_metrics(store):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier = Barrier(2)
    def write(data):
        barrier.wait()
        store.put_day('opaque','2025-01-01',data)
    health = {'health':{'available':True,'steps':123},'sleep':{'available':False},'source_errors':{'sleep':'upstream_error'}}
    sleep = {'health':{'available':False},'sleep':{'available':True,'sleep_score':88},'source_errors':{'health':'upstream_error'}}
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write, payload) for payload in (health,sleep)]
        for future in futures:future.result(timeout=10)
    result = store.get_day('opaque','2025-01-01')
    assert result['health']['steps'] == 123
    assert result['sleep']['sleep_score'] == 88
