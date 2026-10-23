"""A statistics wake-up cannot fall through into admission/publication."""
import copy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan'),str(ROOT/'linyuan/fc')]
import experiment_observation_dispatch as C
import index as F
import verify_production as V

NOW=datetime(2026,10,23,14,47,tzinfo=C.experiment.CST).timestamp()


def run(age=1800,status='completed',conclusion='success',**updates):
    row=dict(path='.github/workflows/'+C.WORKFLOW,head_branch='main',
        head_repository=dict(full_name=C.REPO),status=status,conclusion=conclusion,
        updated_at=datetime.fromtimestamp(NOW-age,C.experiment.CST).isoformat())
    row.update(updates);return row


def request(rows,now=NOW):
    calls=[]
    def gh(method,path,payload=None,**kwargs):
        calls.append((method,path,payload))
        return dict(workflow_runs=rows) if method=='GET' else {}
    return C.request(gh,now),calls


def test_fixed_analytics_policy_keeps_the_original_experiment_and_followup_window():
    assert C.policy()==dict(name='experiment-observations',cron='0 47 * * * *',
        workflow='linyuan-experiment-observations.yml',ref='main',start='2026-10-18',
        end_exclusive='2026-11-24',source_jobs=False,publication=False)
    assert C.experiment.EXPERIMENT_ID=='field-story-30d-20261018'
    assert C.experiment.VERSION==2026101801 and C.experiment.MIN_MOTHER_SECONDS==1200


def test_empty_verified_run_list_dispatches_only_fixed_trusted_analytics():
    result,calls=request([])
    assert result['scheduled'] and result['source_jobs']==result['publications']==0
    assert calls==[('GET',f'/actions/workflows/{C.WORKFLOW}/runs?per_page=5',None),
                   ('POST',f'/actions/workflows/{C.WORKFLOW}/dispatches',{'ref':'main'})]


@pytest.mark.parametrize('status',['queued','pending','requested','waiting','in_progress'])
def test_active_collector_is_not_duplicated(status):
    result,calls=request([run(status=status)])
    assert result==dict(scheduled=False,reason='collector_already_active')
    assert len(calls)==1


@pytest.mark.parametrize('age,schedule',[(0,False),(1499,False),(1500,True),(3600,True)])
def test_recent_success_deduplicates_without_erasing_the_next_hour(age,schedule):
    result,calls=request([run(age)])
    assert result['scheduled'] is schedule and len(calls)==1+int(schedule)


def test_a_recent_failed_collection_can_receive_a_new_read_only_attempt():
    result,calls=request([run(10,conclusion='failure')])
    assert result['scheduled'] and len(calls)==2


@pytest.mark.parametrize('rows',[
    None,{},[run()]*6,[{}],[run(path='.github/workflows/linyuan-produce-cn.yml')],
    [run(head_branch='untrusted')],[run(head_repository=dict(full_name='fork/repo'))],
    [run(status='unknown')],[run(updated_at='bad')],[run(updated_at=None)],[run(-1)],
])
def test_unknown_status_identity_or_clock_is_not_an_empty_run_budget(rows):
    result,calls=request(rows)
    assert result['scheduled'] is False and result['reason']=='collector_request_unavailable'
    assert len(calls)==1


@pytest.mark.parametrize('stamp',[
    datetime(2026,10,17,23,59,tzinfo=C.experiment.CST).timestamp(),
    datetime(2026,11,24,0,0,tzinfo=C.experiment.CST).timestamp(),
    datetime(2027,10,23,14,47,tzinfo=C.experiment.CST).timestamp(),
])
def test_outside_frozen_study_period_makes_no_remote_request(stamp):
    result,calls=request([],stamp)
    assert result==dict(scheduled=False,reason='outside_fixed_observation_period') and not calls


@pytest.mark.parametrize('failing_method',['GET','POST'])
def test_unknown_network_result_never_retries_a_post_or_invents_success(failing_method):
    calls=[]
    def gh(method,*args,**kwargs):
        calls.append(method)
        if method==failing_method:raise TimeoutError('unit-test transport failure')
        return dict(workflow_runs=[])
    result=C.request(gh,NOW)
    assert not result['scheduled'] and result['error_type']=='TimeoutError'
    assert calls==(['GET'] if failing_method=='GET' else ['GET','POST'])


def test_actual_fc_route_does_not_touch_production_state_leases_or_inputs(monkeypatch):
    calls=[]
    def forbidden(*a,**k):pytest.fail('Analytics may not enter a production path')
    for name in ('log_event','flush_logs','load_state','save_state','publish_handler',
                 'dispatch_handler','run_with_lease'):
        monkeypatch.setattr(F,name,forbidden)
    monkeypatch.setattr(C.time,'time',lambda:NOW)
    def gh(method,path,payload=None,**kw):
        calls.append((method,path,payload));return dict(workflow_runs=[]) if method=='GET' else {}
    monkeypatch.setattr(F,'gh',gh)
    result=F.handler(json.dumps(dict(triggerName=C.TRIGGER,force_publish=True,
        source='ignored-untrusted-input',slug='ignored',ref='untrusted')),None)
    assert result['scheduled'] and calls[-1][-1]=={'ref':'main'}
    assert all('produce-cn' not in path for _,path,_ in calls)


class TimerClient:
    def __init__(self):
        self.triggers={n:NS(trigger_name=n,trigger_type='timer',qualifier='LATEST',
            trigger_config=json.dumps(dict(cronExpression=cron,enable=enabled)))
            for n,cron,enabled in [('dispatch','0 37 * * * *',False),
                                  ('publish','0 0 2,6,8,13 * * *',True)]}
        self.writes=[]

    def list_triggers_with_options(self,*args):return NS(body=NS(triggers=list(self.triggers.values())))

    def create_trigger(self,function,request):
        body=request.body;assert body.trigger_name not in self.triggers
        self.writes.append(('create',body.trigger_name));self.triggers[body.trigger_name]=copy.deepcopy(body)

    def update_trigger_with_options(self,function,name,request,*args):
        self.writes.append(('update',name));t=self.triggers[name]
        t.trigger_config=request.body.trigger_config;t.qualifier=request.body.qualifier


MODELS=NS(ListTriggersRequest=NS,UpdateTriggerRequest=NS,UpdateTriggerInput=NS,
          CreateTriggerRequest=NS,CreateTriggerInput=NS)


def test_timer_create_readback_and_redeploy_do_not_change_source_or_publish_timers(monkeypatch):
    monkeypatch.setattr(V.time,'time',lambda:NOW)
    client=TimerClient();before=copy.deepcopy(client.triggers)
    proof=V.configure_observation_timer(client,'fc',MODELS,NS())
    V.configure_observation_timer(client,'fc',MODELS,NS())
    assert client.writes==[('create',C.TRIGGER),('update',C.TRIGGER)]
    assert client.triggers['dispatch']==before['dispatch'] and client.triggers['publish']==before['publish']
    assert proof['config']==dict(cronExpression=C.CRON,enable=True,payload=json.dumps(
        dict(triggerName=C.TRIGGER,purpose='read_only_experiment_statistics')))
    assert proof['qualifier']=='LATEST' and not proof['publication'] and not proof['source_jobs']


def test_wrong_trigger_type_and_full_trigger_list_are_never_overwritten(monkeypatch):
    monkeypatch.setattr(V.time,'time',lambda:NOW)
    client=TimerClient();client.triggers[C.TRIGGER]=NS(trigger_name=C.TRIGGER,trigger_type='http')
    with pytest.raises(SystemExit,match='not overwritten'):V.configure_observation_timer(client,'fc',MODELS,NS())
    assert not client.writes


def test_same_name_unowned_timer_is_not_repurposed(monkeypatch):
    monkeypatch.setattr(V.time,'time',lambda:NOW)
    client=TimerClient();client.triggers[C.TRIGGER]=NS(trigger_name=C.TRIGGER,trigger_type='timer',
        qualifier='LATEST',trigger_config=json.dumps(dict(payload='{}')))
    before=copy.deepcopy(client.triggers)
    with pytest.raises(SystemExit,match='ownership is unverified'):
        V.configure_observation_timer(client,'fc',MODELS,NS())
    assert not client.writes and client.triggers==before
    client=TimerClient()
    for i in range(8):client.triggers[str(i)]=NS(trigger_name=str(i),trigger_type='timer')
    with pytest.raises(SystemExit,match='No spare'):V.configure_observation_timer(client,'fc',MODELS,NS())
    assert not client.writes


def test_wrong_timer_readback_is_not_success(monkeypatch):
    monkeypatch.setattr(V.time,'time',lambda:NOW)
    client=TimerClient();create=client.create_trigger
    def corrupt(*args):
        create(*args);client.triggers[C.TRIGGER].qualifier='obsolete'
    client.create_trigger=corrupt
    with pytest.raises(SystemExit,match='read-back mismatch'):V.configure_observation_timer(client,'fc',MODELS,NS())


def test_next_deployment_after_followup_disables_only_the_analytics_timer(monkeypatch):
    end=datetime(2026,11,24,0,0,tzinfo=C.experiment.CST).timestamp()
    monkeypatch.setattr(V.time,'time',lambda:end)
    client=TimerClient();proof=V.configure_observation_timer(client,'fc',MODELS,NS())
    assert not proof['config']['enable'] and client.triggers['publish'].qualifier=='LATEST'


def test_package_and_config_health_include_the_independent_clock_contract():
    assert F.production_config()['experiment_observation_clock']==C.policy()
    spec=importlib.util.spec_from_file_location('observation_package',ROOT/'linyuan/fc/package_code.py')
    package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
    assert 'experiment_observation_dispatch.py' in package.SOURCES
    bad=F.production_config();bad['experiment_observation_clock']={}
    with pytest.raises(SystemExit,match='independent analytics clock'):V.verify_config_health(bad,ROOT)
    bad=F.production_config();bad['experiment_observation_clock_sha256']='0'*64
    with pytest.raises(SystemExit,match='independent analytics clock'):V.verify_config_health(bad,ROOT)
    assert '      - linyuan/experiment_observation_dispatch.py' in (
        ROOT/'.github/workflows/fc-production-deploy.yml').read_text()
