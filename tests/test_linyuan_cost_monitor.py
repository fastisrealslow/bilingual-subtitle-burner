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


def receipt(bvid='BV80',seconds=80,**extra):
    return dict(status='published',bvid=bvid,ts=10,duration_sec=seconds,upload_backend='github-qn',**extra)


def delivery(parts,**kwargs):
    return m.delivery_metrics(dict(published=dict(batch=dict(parts=parts))),start_ms=0,end_ms=H,
        experiment_id='test-cohort',experiment_version=1,**kwargs)


def trial(**extra):
    return dict(experiment_id='test-cohort',version=1,slot_number=2,
        actual='field-story-v1',planned='field-story-v1',compliant=True,**extra)


def test_cost_report_counts_80_second_output_and_does_not_hide_missing_daily_output():
    r=delivery([receipt(),receipt('BVother',30)])
    assert r['published_receipts']==2 and r['publication_gap']==2
    assert r['duration_bands']==dict(under_60=1,**{'60_to_100':1})
    assert r['treatment_gap']==2 and r['upload_backends']=={'github-qn':2}


def test_pending_skipped_empty_and_wrong_window_do_not_fill_daily_target():
    rows=[receipt(),{**receipt('BVpending'),'status':'uploading'},
        {**receipt('BVskip'),'status':'skipped'},receipt(''),
        {**receipt('BVnext'),'ts':H/1000},{**receipt('BVold'),'ts':-1}]
    assert delivery(rows)['published_receipts']==1


def test_duplicate_receipts_are_one_video_and_conflicting_proof_is_not_treatment():
    row=receipt(publishing_experiment=trial())
    r=delivery([row,dict(row)])
    assert r['published_receipts']==1 and r['actual_treatment_receipts']==1
    r=delivery([row,{**row,'publishing_experiment':{**trial(),'actual':'baseline'}}])
    assert r['published_receipts']==1 and r['actual_treatment_receipts']==0
    assert r['conflicting_receipts']==1


@pytest.mark.parametrize('changes',[
    dict(actual='baseline'),dict(planned='baseline'),dict(compliant=False),
    dict(experiment_id='another-cohort'),dict(version=0),dict(slot_number=1),
])
def test_plans_fallbacks_other_cohorts_and_wrong_slots_are_not_compliant_treatment(changes):
    proof={**trial(),**changes}
    assert delivery([receipt(publishing_experiment=proof)])['actual_treatment_receipts']==0


@pytest.mark.parametrize('seconds',[None,'80',float('nan'),True,-1])
def test_unknown_duration_is_not_a_short_video(seconds):
    assert delivery([receipt(seconds=seconds)])['duration_bands']=={'unknown':1}


def test_finished_experiment_has_no_perpetual_treatment_gap():
    r=delivery([receipt()],treatment_target=0)
    assert r['treatment_target']==r['treatment_gap']==0


def test_daily_delivery_policy_is_separate_from_old_comparison_identity():
    proof=dict(delivery_policy_id='daily-two',delivery_policy_version=2,slot_number=4,
        actual='field-story-v1',planned='field-story-v1',compliant=True)
    kwargs=dict(delivery_policy_id='daily-two',delivery_policy_version=2)
    assert delivery([receipt(publishing_experiment=proof)],**kwargs)['actual_treatment_receipts']==1
    assert delivery([receipt(publishing_experiment=proof)])['actual_treatment_receipts']==0
    assert delivery([receipt(publishing_experiment={**proof,'experiment_id':'other'})],**kwargs)['actual_treatment_receipts']==0
