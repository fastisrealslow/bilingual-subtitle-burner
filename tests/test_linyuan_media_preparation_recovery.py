"""An observed installer failure is not an unusable mother or ASR cache."""
import copy
import json
from pathlib import Path
import sys
import time

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),
             str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as F
from production_failure_report import report_missing


@pytest.fixture
def case(monkeypatch):
    now=1792707000
    monkeypatch.setattr(F.time,'time',lambda:now)
    entry=dict(slug='fresh',source_url='https://www.bilibili.com/video/BV1esJv6rEgQ',
        ts=now-3600,source_check_attempts=1,quality_retries=1,source_check_run_id=123,
        recovery_origin_run_id=123)
    state=dict(dispatched=[entry],published={},daily_publish=dict(count=2))
    run=dict(id=123,path='.github/workflows/'+F.WF_PRODUCE,conclusion='failure',head_sha='old',
        display_title='中文源出片 · fresh',created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(now-3600)))
    steps=[dict(name='安装 ffmpeg 与中文字体',conclusion='failure'),
           dict(name='素材质量门禁',conclusion='skipped'),dict(name='出片',conclusion='skipped')]
    files=[dict(filename='linyuan/ci_install_media_tools.sh',patch='+MEDIA_TOOLS_POLICY_VERSION=2026102301')]
    calls=[];writes=[]
    def api(method,path,*args,**kwargs):
        assert method=='GET';calls.append(path)
        if path.endswith('/jobs'):return dict(jobs=[dict(steps=steps)])
        assert path=='/compare/old...main'
        return dict(files=files)
    monkeypatch.setattr(F,'gh',api)
    monkeypatch.setattr(F,'save_state',lambda st:writes.append(copy.deepcopy(st)))
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    return state,entry,run,steps,files,calls,writes


def test_reported_and_legacy_unreported_installer_fault_recovers_once_without_empty_asr(case):
    state,entry,run,steps,files,calls,writes=case;before=copy.deepcopy(state)
    artifacts=[dict(name='production-reject-fresh',id=456)]
    assert F._recover_preflight_failure(state,entry,run,artifacts)
    assert entry['failure_stage']=='media-tools-runtime'
    assert entry['source_check_retry_after']<F.time.time()
    assert 'source_check_run_id' not in entry and 'recovery_origin_run_id' not in entry
    assert entry['source_check_attempts']==1 and entry['quality_retries']==1
    assert state['published']==before['published'] and state['daily_publish']==before['daily_publish']
    assert not F._recover_preflight_failure(state,entry,run,artifacts)
    assert len(writes)==1 and len(calls)==2


@pytest.mark.parametrize('change',[
    'published','quality_rejected','exhausted','old_run','render_failed','source_checked',
    'unrelated_fix','unproven_patch','another_slug','different_workflow','paused',
])
def test_not_a_generic_retry_or_source_verdict_override(case,monkeypatch,change):
    state,entry,run,steps,files,calls,writes=case
    if change=='published':state['published']['fresh']=dict(bvid='BVposted')
    if change=='quality_rejected':entry['source_quality_rejected']=True
    if change=='exhausted':entry['source_check_attempts']=2
    if change=='old_run':run['created_at']='2026-10-21T00:00:00Z'
    if change=='render_failed':steps[0]['name']='出片'
    if change=='source_checked':steps[1]['conclusion']='success'
    if change=='unrelated_fix':files[0]['filename']='linyuan/title_rewrite.py'
    if change=='unproven_patch':files[0]['patch']='+# Installer documentation only'
    if change=='another_slug':run['display_title']='中文源出片 · other'
    if change=='different_workflow':run['path']='.github/workflows/other.yml'
    if change=='paused':monkeypatch.setattr(F,'REVIEW_PAUSED_SLUGS',{'fresh'})
    before=copy.deepcopy(state)
    assert not F._recover_media_tools_preparation(state,entry,run,[])
    assert state==before and not writes


@pytest.mark.parametrize('prefix',['deliver-','source-reject-','mother-media-','raw-asr-','editorial-','render-evidence-'])
def test_any_existing_source_evidence_requires_its_own_normal_recovery(case,prefix):
    state,entry,run,steps,files,calls,writes=case
    assert not F._recover_media_tools_preparation(state,entry,run,[dict(name=prefix+'fresh')])
    assert calls==[] and writes==[]


def test_valid_prior_cache_provenance_is_not_replaced_by_the_empty_setup_run(case):
    state,entry,run,*_=case
    entry.update(source_check_run_id=99,recovery_origin_run_id=99)
    assert F._recover_media_tools_preparation(state,entry,run,[])
    assert entry['source_check_run_id']==99 and entry['recovery_origin_run_id']==99


def test_new_media_step_id_reports_pre_source_runtime_not_source_rejection(tmp_path):
    path=tmp_path/'batch_report.json'
    assert report_missing(path,dict(media_setup=dict(outcome='failure')),'fresh')
    data=json.loads(path.read_text())
    assert data['failure_stage']=='media-tools-runtime' and data['source_downloaded'] is False
    assert data['retryable'] and data['accepted']==0
    path2=tmp_path/'render.json'
    report_missing(path2,dict(render=dict(outcome='failure'),source_gate=dict(outcome='success')),'fresh')
    assert 'source_downloaded' not in json.loads(path2.read_text())


def test_runtime_report_collector_does_not_restore_an_empty_pre_source_run(monkeypatch):
    import io,zipfile
    entry=dict(slug='fresh',ts=1,source_check_attempts=1,quality_retries=1,
        source_url='https://www.bilibili.com/video/BV1esJv6rEgQ',source_check_run_id=10,recovery_origin_run_id=10)
    state=dict(dispatched=[entry],published={},rejected=[])
    artifact=dict(id=9,name='production-reject-fresh',expired=False)
    def api(method,path,*a,**kw):
        assert method=='GET'
        return dict(workflow_runs=[dict(id=10)]) if '/workflows/' in path else dict(artifacts=[artifact])
    data=io.BytesIO()
    report=dict(slug='fresh',accepted=0,retryable=True,failure_stage='media-tools-runtime',
        source_downloaded=False,rejected=[dict(stage='workflow-runtime',reason='准备失败',retryable=True)])
    with zipfile.ZipFile(data,'w') as z:z.writestr('batch_report.json',json.dumps(report))
    monkeypatch.setattr(F,'gh',api)
    monkeypatch.setattr(F,'download_reviewed_zip',lambda aid,path,**kw:Path(path).write_bytes(data.getvalue()))
    monkeypatch.setattr(F,'save_state',lambda *a:None)
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    assert F._collect_source_rejections(state)==0
    assert 'source_check_run_id' not in entry and 'recovery_origin_run_id' not in entry
    assert entry['source_check_attempts']==1 and entry['quality_retries']==1
    assert entry['source_check_retry_after']>F.time.time() and not entry.get('failed')
