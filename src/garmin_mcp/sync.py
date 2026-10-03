"""Incremental cache refresh; an advisory lock prevents overlapping writers."""
import argparse
import asyncio
from datetime import date, timedelta
import json
import os
from garmin_mcp.observability import configure_private_logging, log_event

async def sync_once(service,store,days=None):
    today = await service.today() if hasattr(service,'today') else None
    if not isinstance(today,str):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo(os.getenv('GARMIN_TIMEZONE','America/Santiago'))).date().isoformat()
    end = date.fromisoformat(today)
    initial = not store.has_history(service.profile_id)
    count = days or int(os.getenv('GARMIN_INITIAL_BACKFILL_DAYS','90') if initial else os.getenv('GARMIN_SYNC_LOOKBACK_DAYS','3'))
    if not 1 <= count <= 365:
        raise ValueError('Sync range must be 1..365 days')
    report = {'start_date':(end-timedelta(days=count-1)).isoformat(),'end_date':today,'requested_days':count,'attempted_days':0,'successful_days':0,'missing_days':[],'errors':[]}
    with store.sync_lock() as acquired:
        if not acquired:
            return {'status':'skipped','reason':'Another sync holds the lock'}
        for offset in range(count-1,-1,-1):
            day=(end-timedelta(days=offset)).isoformat()
            try:
                report['attempted_days'] += 1
                data=await service.daily(day,refresh=not initial)
                groups=[data.get(k,{}) for k in ('health','sleep','hrv','body_battery','stress','training')]
                if any(isinstance(g,dict) and g.get('available') for g in groups):
                    report['successful_days']+=1
                else:
                    report['missing_days'].append(day)
                if data.get('source_errors'):
                    report['errors'].append({'date':day,'sources':list(data['source_errors'])})
                    reasons = set(data['source_errors'].values())
                    # Stop systemic failures, but an unsupported historical metric
                    # must not prevent reaching recent dates or advancing backfill.
                    fatal = bool(reasons & {'authentication_required', 'rate_limited'})
                    if fatal or (len(data['source_errors']) >= 10 and not any(g.get('available') for g in groups if isinstance(g,dict))):
                        report['aborted'] = True
                        report['unattempted_days']=offset
                        break
            except Exception as exc:
                report['aborted'] = True
                report['errors'].append({'date':day,'error_type':type(exc).__name__})
                report['unattempted_days']=offset
                break
            await asyncio.sleep(float(os.getenv('GARMIN_SYNC_DAY_PAUSE_SECONDS','1')))
        completed = report['attempted_days'] == count and not report.get('aborted')
        report['range_completed'] = completed
        # One bounded activity list read per run. State explicitly if 100 recent
        # activities do not prove full requested historical coverage.
        if completed and hasattr(service, 'call'):
            from garmin_mcp.wellness import normalize_activity
            raw, error = await service.call('get_activities', 0, 100)
            if error:
                report['activities'] = {'status': 'unavailable', 'reason': error, 'historical_coverage_complete': False}
            else:
                raw = raw if isinstance(raw, list) else []
                normalized = [normalize_activity(item) for item in raw[:100]]
                rows = [item for item in normalized if report['start_date'] <= str(item.get('start_time', ''))[:10] <= today]
                await asyncio.to_thread(store.put_activities, service.profile_id, rows)
                dates = [str(item.get('start_time', ''))[:10] for item in normalized if item.get('start_time')]
                truncated = len(raw) >= 100 and (not dates or min(dates) > report['start_date'])
                report['activities'] = {'status':'success','fetched':len(raw),'stored':len(rows),'limit':100,'historical_coverage_complete':not truncated,'truncated':truncated}
        # A fully attempted range with per-metric gaps is a completed backfill.
        # Otherwise unsupported metrics would trigger 90 days again every hour.
        if completed and (report['successful_days'] or not report['errors']):
            await asyncio.to_thread(store.checkpoint,service.profile_id,today,report)
    log_event('sync',status='success' if not report['errors'] else 'error')
    return report

async def background_sync(service,store,client):
    interval=max(3600,int(os.getenv('GARMIN_SYNC_INTERVAL_SECONDS','3600')))
    while True:
        try:
            if client.ready():
                await sync_once(service,store)
            else:
                log_event('sync',status='skipped',error_type='GarminTokensMissing')
        except Exception as exc:
            log_event('sync',status='error',error_type=type(exc).__name__)
        await asyncio.sleep(interval)

def main():
    from garmin_mcp.storage import Store,migrate
    from garmin_mcp.garmin_client import TokenClient
    from garmin_mcp.runtime import profile_id
    from garmin_mcp.wellness import WellnessService
    configure_private_logging()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days',type=int)
    args=parser.parse_args()
    url=os.environ['DATABASE_URL']; migrate(url)
    client=TokenClient()
    if not client.ready():
        print(json.dumps({'status':'error','reason':'Garmin tokens missing','attempted_days':0}))
        raise SystemExit(1)
    store=Store(url)
    service=WellnessService(client,store=store,timezone=os.getenv('GARMIN_TIMEZONE','America/Santiago'),profile_id=profile_id(client.token_path))
    report=asyncio.run(sync_once(service,store,args.days))
    print(json.dumps(report))
    if report.get('errors'): raise SystemExit(1)
