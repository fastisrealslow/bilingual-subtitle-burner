"""Read-only async execution estimate; never invoke a function or claim a bill."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import math

CST=timezone(timedelta(hours=8))
CU_PRICE=0.00011
HOUR_MINIMUM=0.01


def estimate(tasks, *, start_ms, end_ms, cpu, memory_gb, complete=True):
    """Allocate execution across billing hours; exclude unknown/sync/CDT costs."""
    if not 0<=start_ms<end_ms or cpu<=0 or memory_gb<=0:
        raise ValueError('Invalid observation window/resources')
    hours=defaultdict(float); counts=defaultdict(int); seen=set(); unresolved=0
    for row in tasks:
        task_id=row.get('taskId')
        if not task_id or task_id in seen:continue
        seen.add(task_id)
        begin=row.get('startedTime'); finish=row.get('endTime')
        if not isinstance(begin,(int,float)) or not start_ms<=begin<end_ms:continue
        if not isinstance(finish,(int,float)) or finish<begin:
            unresolved+=1;continue
        counts['completed_async_calls']+=1
        hours[int(begin//3600000)]+=0.0075
        cursor=begin; stop=min(finish,end_ms)
        while cursor<stop:
            hour=int(cursor//3600000); boundary=min(stop,(hour+1)*3600000)
            hours[hour]+=(boundary-cursor)/1000*(cpu+0.15*memory_gb)
            cursor=boundary
    estimates={datetime.fromtimestamp(h*3600,CST).isoformat():round(max(HOUR_MINIMUM,math.ceil(cu)*CU_PRICE),6)
               for h,cu in sorted(hours.items())}
    return dict(kind='async_compute_estimate_not_invoice',currency='CNY',
        observed_start_ms=start_ms,observed_end_ms=end_ms,
        completed_async_calls=counts['completed_async_calls'],unresolved_async_calls=unresolved,
        complete_sample=bool(complete and not unresolved),
        estimated_cny=round(sum(estimates.values()),6),active_hours=len(hours),hourly_estimates=estimates,
        excluded=['synchronous_invocations','CDT_outbound_traffic','other_functions','discounts','carryover_from_before_window'],
        target_ratio=0.10,target_verified=False)


def collect(client,function,models,runtime,*,start_ms,end_ms,max_pages=20):
    """Bounded control-plane reads only, withholding private task payloads."""
    rows=[];token=None;complete=False
    for _ in range(max_pages):
        kwargs=dict(qualifier='LATEST',limit=100,include_payload=False,
                    started_time_begin=start_ms,started_time_end=end_ms,sort_order_by_time='asc')
        if token:kwargs['next_token']=token
        result=client.list_async_tasks_with_options(function,models.ListAsyncTasksRequest(**kwargs),{},runtime).body
        batch=[r.to_map() for r in (result.tasks or [])]
        rows.extend(batch)
        token=getattr(result,'next_token',None)
        if not token:
            complete=True;break
    cfg=client.get_function_with_options(function,models.GetFunctionRequest(qualifier='LATEST'),{},runtime).body
    report=estimate(rows,start_ms=start_ms,end_ms=end_ms,cpu=cfg.cpu,memory_gb=cfg.memory_size/1024,complete=complete)
    report.update(function_invocations=0,control_plane_only=True,pages_limit=max_pages,
                  resources=dict(cpu=cfg.cpu,memory_gb=cfg.memory_size/1024),
                  resource_assumption='current_spec_applied_to_observed_window')
    return report


def completed_day_window(now):
    """The previous full CST day is stable even when the hosted cron is late."""
    if now.tzinfo is None:raise ValueError('An aware clock is required')
    end=now.astimezone(CST).replace(hour=0,minute=0,second=0,microsecond=0)
    return end-timedelta(days=1),end


def main():
    import os,json
    from pathlib import Path
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    start,end=completed_day_window(datetime.now(CST))
    client=Client(api.Config(access_key_id=os.environ['ALIYUN_AK'],access_key_secret=os.environ['ALIYUN_SK'],
        endpoint='fcv3.'+os.environ.get('FC_REGION','cn-hangzhou')+'.aliyuncs.com'))
    report=collect(client,os.environ.get('FC_FUNCTION_NAME','fc-develop'),models,
        util.RuntimeOptions(connect_timeout=10000,read_timeout=60000,autoretry=False),
        start_ms=int(start.timestamp()*1000),end_ms=int(end.timestamp()*1000))
    report['observed_date']=start.date().isoformat()
    Path('fc-cost-daily-estimate.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a') as f:
            f.write('FC async compute estimate (not invoice): '+str(report['estimated_cny'])+' CNY; '
                    +str(report['completed_async_calls'])+' completed async executions; '
                    +'complete sample: '+str(report['complete_sample'])+'.\n\n'
                    +'Excludes synchronous calls, CDT, other functions and discounts. No function was invoked. '
                    +'The 10% invoice target remains unverified.\n')


if __name__=='__main__':main()
