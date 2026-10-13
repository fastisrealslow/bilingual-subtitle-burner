import base64
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan/fc'))
import index as fc


def fixture():
    source='https://www.bilibili.com/video/BV1a84y1c7B7'
    choice=dict(recognition_context_profile='finance-v1',recognition_validation_run_id=42,
        recognition_validated_mp4_sha256='b'*64,source_origin_url=source,production_recovery_run_id=7)
    config={'source_overrides':{'a'*64:choice}}
    entry=dict(slug='ly-test',failed=True,source_url=source,ts=100,
        last_error='原始ASR存在影响理解的疑点',source_check_attempts=2,
        automatic_rule_recoveries={'transcript-review-model-v3':{'run_id':1}})
    run=dict(id=7,status='completed',conclusion='failure',head_sha='old',
        path='.github/workflows/linyuan-produce-cn.yml',display_title='中文源出片 · ly-test')
    return config,entry,run


def config_response(config):
    return dict(encoding='base64',content=base64.b64encode(json.dumps(config).encode()).decode())


def test_verified_pilot_is_read_only_and_bound_to_exact_failed_source(monkeypatch):
    config,entry,run=fixture();state={'published':{}};before=copy.deepcopy(entry)
    calls=[]
    def gh(method,path):
        calls.append(path)
        return config_response(config) if '/contents/' in path else run
    monkeypatch.setattr(fc,'gh',gh)
    assert fc._verified_recovery_runs(state,{'ly-test':entry})==[run]
    assert entry==before and len(calls)==2
    entry['source_url']+='?p=2'
    assert fc._verified_recovery_runs(state,{'ly-test':entry})==[]


@pytest.mark.parametrize('field,value',[
    ('recognition_context_profile','none'),('recognition_validation_run_id',False),
    ('recognition_validated_mp4_sha256',''),('production_recovery_run_id',False),
    ('source_origin_url','https://example.com/?url=https://www.bilibili.com/video/BV1a84y1c7B7'),
])
def test_unverified_config_cannot_prioritize_a_retry(monkeypatch,field,value):
    config,entry,run=fixture();config['source_overrides']['a'*64][field]=value
    monkeypatch.setattr(fc,'gh',lambda *a:config_response(config))
    assert fc._verified_recovery_runs({'published':{}},{'ly-test':entry})==[]


@pytest.mark.parametrize('change',[
    lambda e,r,s:r.update(conclusion='success'),
    lambda e,r,s:r.update(status='in_progress'),
    lambda e,r,s:r.update(display_title='中文源出片 · other'),
    lambda e,r,s:r.update(path='.github/workflows/linyuan-context-preview.yml'),
    lambda e,r,s:s['published'].update({'ly-test':{}}),
    lambda e,r,s:e['automatic_rule_recoveries'].update({'tracked-source-strips-v1':{}}),
])
def test_no_priority_for_live_wrong_or_consumed_run(monkeypatch,change):
    config,entry,run=fixture();state={'published':{}};change(entry,run,state)
    monkeypatch.setattr(fc,'gh',lambda m,p:config_response(config) if '/contents/' in p else run)
    assert fc._verified_recovery_runs(state,{'ly-test':entry})==[]


def test_archived_verified_pilot_precedes_page_and_still_reads_real_zero_batch(monkeypatch):
    config,entry,run=fixture();state={'dispatched':[entry],'published':{},'rule_recovery_page':9}
    def gh(method,path):
        if '/contents/' in path:return config_response(config)
        if '/workflows/' in path:return {'workflow_runs':[dict(id=100+i,display_title='unrelated') for i in range(100)]}
        if path=='/actions/runs/7':return run
        if '/compare/' in path:return {'files':[{'filename':'linyuan/live_tracking.py'}]}
        raise AssertionError(path)
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc,'save_state',lambda *a:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    observed=[]
    def observe(candidate,actual):
        observed.append(actual['id'])
        candidate['production_failure_summary']=dict(slug='ly-test',run_id=7,artifact_id=9,accepted=0,
            failures=[{'stage':'part-quality','reason':'成片存在持续黑色填充边'}])
    monkeypatch.setattr(fc,'_observe_zero_batch_failures',observe)
    assert fc._recover_rule_backlog(state,{'ly-test':entry})==1
    assert observed==[7] and not entry['failed']
    assert entry['source_check_attempts']==2 and entry['source_check_run_id']==7
    assert state['rule_recovery_page']==10
    entry['failed']=True
    assert fc._verified_recovery_runs(state,{'ly-test':entry})==[]


def test_transient_priority_read_falls_back_without_changing_failure(monkeypatch):
    config,entry,run=fixture();before=copy.deepcopy(entry)
    def unavailable(*a):raise TimeoutError('temporary observation error')
    monkeypatch.setattr(fc,'gh',unavailable)
    assert fc._verified_recovery_runs({'published':{}},{'ly-test':entry})==[]
    assert entry==before


def test_invalid_runs_cannot_expand_two_run_lookup_budget(monkeypatch):
    config,entry,run=fixture();choice=config['source_overrides']['a'*64]
    config['source_overrides']={format(i,'064x'):{**choice,'production_recovery_run_id':i+1} for i in range(8)}
    calls=[]
    def gh(method,path):
        if '/contents/' in path:return config_response(config)
        calls.append(path);return {**run,'status':'in_progress'}
    monkeypatch.setattr(fc,'gh',gh)
    assert fc._verified_recovery_runs({'published':{}},{'ly-test':entry})==[]
    assert len(calls)==2
