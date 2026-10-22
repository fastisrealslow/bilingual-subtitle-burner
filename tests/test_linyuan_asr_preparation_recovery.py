"""A pre-render runtime failure must not be silently stranded without a report."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),
             str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as fc
from production_failure_report import report_missing


@pytest.fixture
def case(monkeypatch):
    now=1792652250
    monkeypatch.setattr(fc.time,'time',lambda:now)
    entry=dict(slug='fresh',source_url='https://www.bilibili.com/video/BV1ADetzQEuE',
               ts=1792646736,quality_retries=1)
    state=dict(dispatched=[entry],published={},daily_publish=dict(count=2))
    run=dict(id=39727953226,conclusion='failure',head_sha='old',created_at='2026-10-22T05:25:36Z')
    calls=[]
    steps=[dict(name='素材质量门禁',conclusion='success'),
           dict(name='准备CPU离线Qwen识别及逐字对齐',conclusion='failure')]
    changed=['linyuan/prepare_asr_runtime.py']
    def gh(method,path,*args,**kwargs):
        assert method=='GET';calls.append(path)
        if path.endswith('/jobs'):return dict(jobs=[dict(steps=steps)])
        assert path=='/compare/old...main'
        return dict(files=[dict(filename=p) for p in changed])
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc,'save_state',lambda _:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    return state,entry,run,steps,changed,calls


def test_old_unreported_fresh_failure_is_queued_once_without_old_asr_or_new_publication(case):
    state,entry,run,steps,changed,calls=case
    before=copy.deepcopy(state)
    assert fc._recover_preflight_failure(state,entry,run,[])
    assert entry['preflight_failure_run_id']==run['id']
    assert entry['failure_stage']=='asr-preparation-runtime'
    assert entry['source_check_retry_after']<fc.time.time()
    assert 'source_check_run_id' not in entry
    assert entry['quality_retries']==1 and state['daily_publish']==before['daily_publish']
    assert state['published']==before['published']
    assert not fc._recover_preflight_failure(state,entry,run,[])
    assert len(calls)==2


@pytest.mark.parametrize('kind',['deliver-','source-reject-','production-reject-'])
def test_real_output_or_negative_quality_evidence_wins(case,kind):
    state,entry,run,steps,changed,calls=case
    assert not fc._recover_preflight_failure(state,entry,run,[dict(name=kind+'fresh')])
    assert calls==[]


@pytest.mark.parametrize('change',['different_step','missing_source_gate','unrelated_fix','old_run'])
def test_only_exact_unreported_preparation_fault_with_matching_fix_is_recovered(case,change):
    state,entry,run,steps,changed,calls=case
    if change=='different_step':steps[1]['name']='出片'
    if change=='missing_source_gate':steps[0]['conclusion']='skipped'
    if change=='unrelated_fix':changed[:]=['linyuan/title_rewrite.py']
    if change=='old_run':run['created_at']='2026-10-21T05:25:36Z'
    assert not fc._recover_preflight_failure(state,entry,run,[])


def test_real_content_failure_paused_posted_and_exhausted_sources_are_not_reopened(case,monkeypatch):
    state,entry,run,steps,changed,calls=case
    entry['failed']=True
    assert not fc._recover_preflight_failure(state,entry,run,[])
    entry['failed']=False;entry['source_check_attempts']=2
    assert not fc._recover_preflight_failure(state,entry,run,[])
    entry.pop('source_check_attempts');state['published']['fresh']=dict(parts=[dict(bvid='B')])
    assert not fc._recover_preflight_failure(state,entry,run,[])
    state['published'].clear();monkeypatch.setattr(fc,'REVIEW_PAUSED_SLUGS',{'fresh'})
    assert not fc._recover_preflight_failure(state,entry,run,[])
    assert calls==[]


def test_future_preparation_failure_has_step_id_and_retryable_runtime_report(tmp_path):
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/linyuan-produce-cn.yml').read_text()
    step=workflow.split('- name: 准备CPU离线Qwen识别及逐字对齐',1)[1].split('      - name:',1)[0]
    assert 'id: asr_prepare' in step
    path=tmp_path/'batch_report.json'
    assert report_missing(path,dict(asr_prepare=dict(outcome='failure')),'fresh')
    report=json.loads(path.read_text())
    assert report['accepted']==0 and report['retryable'] is True
    assert report['rejected'][0]['stage']=='workflow-runtime'
    assert 'asr_prepare' in report['rejected'][0]['reason']


def test_actual_job_failure_without_step_id_is_not_silently_lost(tmp_path):
    path=tmp_path/'batch_report.json'
    steps=dict(source_gate=dict(outcome='success'))
    assert not report_missing(path,steps,'fresh')
    assert report_missing(path,steps,'fresh',job_failed=True)
    report=json.loads(path.read_text())
    assert report['retryable'] is True and report['accepted']==0
    assert report['rejected'][0]['stage']=='workflow-runtime'
    assert 'unreported_workflow_step' in report['rejected'][0]['reason']
    before=path.read_bytes()
    assert not report_missing(path,steps,'fresh',job_failed=True)
    assert path.read_bytes()==before


@pytest.mark.parametrize('step',['source_admission','src','source_gate'])
def test_job_failed_fallback_never_overrides_explicit_source_rejections(tmp_path,step):
    path=tmp_path/'batch_report.json'
    assert not report_missing(path,{step:dict(outcome='failure')},'fresh',job_failed=True)
    assert not path.exists()
