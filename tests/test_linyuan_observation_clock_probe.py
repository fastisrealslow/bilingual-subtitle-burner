import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('clock_runtime_probe',ROOT/'linyuan/fc/probe_observation_clock.py')
P=importlib.util.module_from_spec(spec);spec.loader.exec_module(P)


@pytest.mark.parametrize('result',[
    dict(scheduled=False,reason='collector_already_active'),
    dict(scheduled=False,reason='recent_collector_success'),
    dict(scheduled=True,workflow='linyuan-experiment-observations.yml',ref='main',source_jobs=0,publications=0),
])
def test_probe_attests_before_exact_route_and_never_invokes_production(monkeypatch,result):
    calls=[]
    health=dict(code_sha256='attested-code',experiment_observation_clock_sha256='attested-clock')
    monkeypatch.setattr(P,'verify_config_health',lambda value,root:calls.append('attested'))
    def invoke(payload):
        calls.append(payload)
        return health if payload['triggerName']=='diagnose-config' else result
    proof=P.probe(invoke)
    assert calls==[{'triggerName':'diagnose-config'},'attested',{'triggerName':'experiment-observations'}]
    assert proof['verified_runtime'] and proof['source_job_invocations']==proof['publication_invocations']==0


def test_unattested_old_function_never_receives_the_new_trigger(monkeypatch):
    calls=[]
    def denied(*a):raise SystemExit('old runtime must not receive unknown trigger')
    monkeypatch.setattr(P,'verify_config_health',denied)
    with pytest.raises(SystemExit):P.probe(lambda payload:calls.append(payload) or {})
    assert calls==[{'triggerName':'diagnose-config'}]


@pytest.mark.parametrize('result',[
    {},None,dict(scheduled=False,reason='collector_request_unavailable'),
    dict(scheduled=False,reason='outside_fixed_observation_period'),
    dict(scheduled=True,workflow='linyuan-produce-cn.yml',ref='main',source_jobs=0,publications=0),
    dict(scheduled=True,workflow='linyuan-experiment-observations.yml',ref='untrusted',source_jobs=0,publications=0),
    dict(scheduled=True,workflow='linyuan-experiment-observations.yml',ref='main',source_jobs=1,publications=0),
])
def test_missing_unavailable_or_wrong_route_is_not_a_successful_probe(monkeypatch,result):
    monkeypatch.setattr(P,'verify_config_health',lambda *a:None)
    with pytest.raises(ValueError):P.probe(lambda p:{} if p['triggerName']=='diagnose-config' else result)
