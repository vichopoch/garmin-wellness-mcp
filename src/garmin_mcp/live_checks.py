"""Operator-only read-only smoke checks. Prints availability, never health values."""
import argparse
import asyncio
from datetime import date,timedelta
import json
import os
from garmin_mcp.runtime import build_app
from garmin_mcp.garmin_client import TokenClient
from garmin_mcp.storage import Store
from garmin_mcp.observability import configure_private_logging

async def run(recovery=False):
    client=TokenClient()
    _,app,service=build_app(client=client,store=Store(os.environ['DATABASE_URL']))
    today=await service.today()
    yesterday=(date.fromisoformat(today)-timedelta(days=1)).isoformat()
    checks=[('get_profile',{}),('get_daily_health',{'date':yesterday}),('get_sleep',{'date':yesterday}),('get_hrv',{'date':yesterday}),('get_body_battery',{'date':yesterday}),('get_stress',{'date':yesterday}),('get_training_readiness',{'date':yesterday}),('get_training_status',{'date':yesterday}),('get_vo2max',{'date':yesterday}),('get_activities',{'limit':3}),('get_wellness_today',{}),('get_capabilities',{})]
    if recovery: checks.append(('get_recovery_context',{'days':30}))
    report={'transport':'operator local tool invocation in Railway container','date':today,'checks':{},'tools_registered':len(await app.list_tools())}
    for name,args in checks:
        try:
            output=await app.call_tool(name,args)
            data=output[1] if isinstance(output,tuple) else {}
            summary={'status':'PASS','available':data.get('available')}
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
