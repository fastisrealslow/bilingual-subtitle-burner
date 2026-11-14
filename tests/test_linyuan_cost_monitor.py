import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

spec=importlib.util.spec_from_file_location('fc_cost_monitor',Path(__file__).resolve().parents[1]/'linyuan/fc/cost_monitor.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
H=3600000


def row(i,begin,end):return dict(taskId=i,startedTime=begin,endTime=end)


def test_hour_floor_is_per_function_active_hour_not_per_call():
    report=m.estimate([row('a',1000,2000),row('b',3000,4000)],start_ms=0,end_ms=H,cpu=.5,memory_gb=1)
    assert report['estimated_cny']==.01
    assert report['completed_async_calls']==2
    assert report['target_verified'] is False


def test_allocate_spanning_execution_across_hour_boundaries():
    report=m.estimate([row('a',H-1000,H+1000)],start_ms=0,end_ms=2*H,cpu=.5,memory_gb=1)
    assert report['active_hours']==2
    assert report['estimated_cny']==.02


def test_duration_charge_deduplication_and_window():
    tasks=[row('a',1000,1001000),row('a',1000,1001000),row('old',-H,-1000)]
    report=m.estimate(tasks,start_ms=0,end_ms=H,cpu=.5,memory_gb=1)
    assert report['completed_async_calls']==1
    assert report['estimated_cny']==pytest.approx(651*.00011)
    assert report['complete_sample']


def test_pending_and_truncated_samples_cannot_certify_target():
    report=m.estimate([row('a',1000,None)],start_ms=0,end_ms=H,cpu=.5,memory_gb=1,complete=False)
    assert report['unresolved_async_calls']==1
    assert not report['complete_sample'] and not report['target_verified']


def test_collector_reads_paginated_control_plane_without_invocation():
    pages=iter([NS(tasks=[NS(to_map=lambda:row('a',1000,2000))],next_token='more'),NS(tasks=[],next_token=None)])
    requests=[]
    class Client:
        def list_async_tasks_with_options(self,function,request,headers,runtime):
            requests.append(request);return NS(body=next(pages))
        def get_function_with_options(self,*args):return NS(body=NS(cpu=.5,memory_size=1024))
        def invoke_function_with_options(self,*args):raise AssertionError('Cost monitor must never invoke FC')
    models=NS(ListAsyncTasksRequest=lambda **k:k,GetFunctionRequest=lambda **k:k)
    report=m.collect(Client(),'test',models,None,start_ms=0,end_ms=H)
    assert requests[0]['include_payload'] is False and requests[1]['next_token']=='more'
    assert requests[0]['started_time_begin']==0 and requests[0]['started_time_end']==H
    assert report['function_invocations']==0 and report['control_plane_only']
    assert report['complete_sample']


@pytest.mark.parametrize('clock,date',[
    ('2026-11-14T04:55:00+08:00','2026-11-13'),
    ('2026-11-14T22:20:00+08:00','2026-11-13'),
    ('2026-11-13T23:55:00+00:00','2026-11-13'),
    ('2027-01-01T07:20:00+08:00','2026-12-31'),
])
def test_delayed_cron_always_observes_one_complete_cst_day(clock,date):
    from datetime import datetime
    start,end=m.completed_day_window(datetime.fromisoformat(clock))
    assert start.date().isoformat()==date
    assert start.hour==end.hour==0 and (end-start).total_seconds()==86400


def test_daily_window_rejects_an_ambiguous_naive_clock():
    from datetime import datetime
    with pytest.raises(ValueError):m.completed_day_window(datetime(2026,11,14))
