"""Operator-only read-only smoke checks. Prints availability, never health values."""
import argparse
import asyncio
from datetime import date,timedelta
import json
import os
from garmin_mcp.runtime import build_app
from garmin_mcp.garmin_client import TokenClient
from garmin_mcp.storage import Store, tables, activities_records
from sqlalchemy import select
from garmin_mcp.wellness import METRICS, number
from garmin_mcp.observability import configure_private_logging

async def run(recovery=False):
    client=TokenClient()
    store=Store(os.environ['DATABASE_URL'])
    _,app,service=build_app(client=client,store=store)
    today=await service.today()
    yesterday=(date.fromisoformat(today)-timedelta(days=1)).isoformat()
    checks=[('get_profile',{}),('get_daily_health',{'date':yesterday}),('get_sleep',{'date':yesterday}),('get_hrv',{'date':yesterday}),('get_body_battery',{'date':yesterday}),('get_stress',{'date':yesterday}),('get_training_readiness',{'date':yesterday}),('get_training_status',{'date':yesterday}),('get_vo2max',{'date':yesterday}),('get_activities',{'limit':3}),('get_wellness_today',{}),('get_capabilities',{})]
    with store.engine.connect() as conn:
        rows=list(conn.execute(select(tables['daily_health'].c.data).where(tables['daily_health'].c.profile_id == service.profile_id)).scalars())
        activity=conn.execute(select(activities_records.c.activity_id).where(activities_records.c.profile_id == service.profile_id).limit(1)).scalar()
    metrics={'get_sleep':'sleep_duration','get_hrv':'hrv','get_body_battery':'body_battery','get_stress':'stress','get_training_readiness':'training_readiness','get_vo2max':'vo2max'}
    for name,args in checks:
        if name in metrics:
            group,field,_=METRICS[metrics[name]]
            dates=[r['date'] for r in rows if number(r.get(group,{}).get(field))]
            if dates: args['date']=max(dates)
    if activity: checks.append(('get_activity',{'activity_id':activity}))
    if recovery: checks.append(('get_recovery_context',{'days':30}))
    report={'transport':'operator local tool invocation in Railway container','date':today,'checks':{},'tools_registered':len(await app.list_tools())}
    for name,args in checks:
        try:
            output=await app.call_tool(name,args)
            data=output[1] if isinstance(output,tuple) else {}
            summary={'status':'PASS','available':data.get('available')}
            if 'date' in args:summary['date_used']=args['date']
            if data.get('source_errors'):summary['source_errors']=data['source_errors']
            if data.get('metrics') and name=='get_capabilities':summary['metrics']=data['metrics']
            if 'missing_metrics' in data:summary['missing_metrics']=data['missing_metrics']
            report['checks'][name]=summary
        except Exception as exc:
            report['checks'][name]={'status':'FAIL','error_type':type(exc).__name__}
    report['last_successful_garmin_call']=client.last_success
    token_file=os.path.join(client.token_path,'garmin_tokens.json')
    report['token_file_exists']=os.path.isfile(token_file)
    report['token_file_mode']=oct(os.stat(token_file).st_mode & 0o777) if os.path.isfile(token_file) else None
    print(json.dumps(report,sort_keys=True))
    return report

def main():
    configure_private_logging()
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--recovery',action='store_true');args=parser.parse_args()
    report=asyncio.run(run(args.recovery))
    if any(x['status']=='FAIL' for x in report['checks'].values()):raise SystemExit(1)

if __name__=='__main__':main()
