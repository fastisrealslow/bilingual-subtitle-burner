"""Deployment 148: a source-inventory probe exhausted the synchronous gateway."""
import importlib.util
import json
from pathlib import Path
import pytest


ROOT=Path(__file__).resolve().parents[1]


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


def test_configuration_probe_uses_no_remote_ledgers_or_log_writes(monkeypatch):
    fc=module('fc_config_probe','linyuan/fc/index.py')
    def forbidden(*args,**kwargs):raise AssertionError('health must not touch remote data')
    for name in ('gh','load_state','flush_logs','log_event','source_inventory','pick'):
        monkeypatch.setattr(fc,name,forbidden)
    health=fc.handler(json.dumps(dict(triggerName='diagnose-config')),None)
    assert health['daily_limit']==4 and health['publish_hours_beijing']==[10,14,16,21]
    assert health['audio_max_per_day']==0 and health['minimum_final_seconds']==120
    assert len(health['code_sha256'])==64 and len(health['editorial_code_sha256'])==64


def test_transient_config_probe_is_retried_but_permission_failure_is_not(monkeypatch):
    verify=module('fc_health_verify','linyuan/fc/verify_production.py')
    monkeypatch.setattr(verify.time,'sleep',lambda _:None)
    calls=[]
    def probe():
        calls.append(1)
        if len(calls)==1:raise RuntimeError('ServiceUnavailable code: 503')
        return dict(ok=True)
    assert verify.read_config_health(probe)==dict(ok=True) and len(calls)==2
    calls.clear()
    def denied():calls.append(1);raise RuntimeError('Forbidden')
    with pytest.raises(RuntimeError,match='Forbidden'):verify.read_config_health(denied)
    assert len(calls)==1
