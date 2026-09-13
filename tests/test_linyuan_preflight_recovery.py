"""Regression-only failures must not strand transcribed production sources."""
from copy import deepcopy
import sys
from pathlib import Path
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan/fc'))
import index as fc


def evidence(monkeypatch, changed='linyuan/produce_cn.py', failed_step=None):
    now = 1789264800
    monkeypatch.setattr(fc.time, 'time', lambda: now)
    candidate = dict(slug='mother', ts=now-3600, reprocessing_quality=True,
                     quality_retries=1, source_check_run_id=100)
    state = dict(dispatched=[candidate], published={}, rejected=[])
    run = dict(id=101, conclusion='failure', head_sha='old',
               created_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(candidate['ts']-10)))
    calls = []
    def gh(method, path, *args, **kwargs):
        calls.append(path)
        assert method == 'GET'
        if path.endswith('/jobs'):
            return dict(jobs=[dict(steps=[dict(conclusion='failure', name=failed_step or
                'CPU离线ASR与完整观点规则回归（失败不进入实产）')])])
        assert path == '/compare/old...main'
        return dict(files=[dict(filename=changed)])
    monkeypatch.setattr(fc, 'gh', gh)
    monkeypatch.setattr(fc, 'save_state', lambda st: None)
    monkeypatch.setattr(fc, 'log_event', lambda *args: None)
    return state, candidate, run, calls


def test_code_fix_recovers_stranded_mother_once_without_inventing_asr_evidence(monkeypatch):
    state, entry, run, calls = evidence(monkeypatch)
    assert fc._recover_preflight_failure(state, entry, run, [])
    assert entry['source_check_retry_after'] < time.time()
    assert entry['failure_stage'] == 'workflow-preflight'
    assert 'source_check_run_id' not in entry
    assert not entry.get('failed') and state['rejected'] == []
    assert not fc._recover_preflight_failure(state, entry, run, [])
    assert len(calls) == 2


@pytest.mark.parametrize('field,value', [
    ('failed', True), ('reprocessing_quality', False),
    ('source_check_attempts', 2), ('ts', 1789264800),
])
def test_rejected_active_or_exhausted_sources_are_not_restarted(monkeypatch, field, value):
    state, entry, run, calls = evidence(monkeypatch)
    entry[field] = value
    assert not fc._recover_preflight_failure(state, entry, run, [])
    assert calls == []


@pytest.mark.parametrize('kind', ['deliver-', 'production-reject-', 'source-reject-'])
def test_actual_delivery_or_media_verdict_wins_over_preflight_recovery(monkeypatch, kind):
    state, entry, run, calls = evidence(monkeypatch)
    assert not fc._recover_preflight_failure(state, entry, run, [dict(name=kind+'mother')])
    assert calls == []


def test_log_only_updates_and_real_render_failures_do_not_retry(monkeypatch):
    state, entry, run, calls = evidence(monkeypatch, changed='linyuan/.automation/fc_state.json')
    before = deepcopy(entry)
    assert not fc._recover_preflight_failure(state, entry, run, [])
    assert entry == before
    state, entry, run, calls = evidence(monkeypatch, failed_step='出片')
    assert not fc._recover_preflight_failure(state, entry, run, [])
    assert len(calls) == 1


def test_newer_success_prevents_old_failure_from_reentering_retry_queue(monkeypatch):
    state, entry, run, calls = evidence(monkeypatch)
    run.update(display_title='中文源出片 · mother')
    newer = dict(run, id=102, conclusion='success')
    def gh(method, path, *args, **kwargs):
        if '/workflows/' in path:
            return dict(workflow_runs=[newer, run])
        assert path.endswith('/artifacts')
        return dict(artifacts=[])
    monkeypatch.setattr(fc, 'gh', gh)
    assert fc._collect_source_rejections(state) == 0
    assert 'source_check_retry_after' not in entry


def test_weekly_reserved_clips_do_not_masquerade_as_daily_inventory():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
    import source_supply
    parts = [dict(index=0, status='verified', render_mode='live_video_card'),
             dict(index=1, status='verified', render_mode='live_video_card', content_type='full_interview')]
    state = dict(dispatched=[dict(slug='mother', weekly_full_week='2026-W37')])
    records = [dict(slug='mother', parts=parts)]
    assert source_supply.inventory_counts(records, state)['verified_live'] == 1
    assert fc.source_inventory(state, dict(artifacts=records, updated_at=time.time(),
        quality_gate_version=fc.QUALITY_GATE_VERSION,
        editorial_policy_version=fc.editorial.VERSION))['verified_live'] == 1


def test_inspected_stock_title_failure_reuses_its_real_evidence_after_exact_fix(monkeypatch):
    state,entry,run,calls=evidence(monkeypatch,changed='linyuan/stock_upgrade_plan.py',failed_step='出片')
    entry['slug']='ly-0910-interview-clean-v4-wide0911v2'
    run['id']=34741749753
    assert fc._recover_preflight_failure(state,entry,run,[])
    assert entry['failure_stage']=='stock-title'
    assert entry['source_check_run_id']==34741749753
    assert not fc._recover_preflight_failure(state,entry,run,[])


def test_stock_title_recovery_rejects_unrelated_code_changes(monkeypatch):
    state,entry,run,calls=evidence(monkeypatch,changed='linyuan/title_rewrite.py',failed_step='出片')
    entry['slug']='ly-0910-interview-clean-v4-wide0911v2'
    run['id']=34741749753
    assert not fc._recover_preflight_failure(state,entry,run,[])


def test_exact_native_interview_bug_gets_one_repair_without_resetting_retry_history(monkeypatch):
    state,entry,run,calls=evidence(monkeypatch,changed='linyuan/produce_cn.py',failed_step='出片')
    entry.update(slug='ly-0910-interview-clean-v4-wide0911v2',source_check_attempts=2)
    run['id']=34743799382
    assert fc._recover_preflight_failure(state,entry,run,[])
    assert entry['failure_stage']=='native-interview' and entry['source_check_attempts']==2
    assert entry['source_check_run_id']==34743799382
    assert not fc._recover_preflight_failure(state,entry,run,[])


def test_running_same_slug_is_never_redispatched_or_deleted(monkeypatch):
    calls=[]
    def gh(method,path,*args,**kwargs):
        calls.append((method,path))
        assert method=='GET'
        return dict(workflow_runs=[dict(display_title='中文源出片 · mother')])
    monkeypatch.setattr(fc,'gh',gh)
    assert not fc._request_quality_reprocess({},dict(slug='mother'),'mother','标题是关键词目录',123)
    assert len(calls)==2


def test_consumed_exact_native_bug_verdict_can_recover_once_with_history(monkeypatch):
    state,entry,run,calls=evidence(monkeypatch,changed='linyuan/produce_cn.py',failed_step='出片')
    reason='真人取景源区域仅272x202像素，超过2倍放大上限，疑似远景小头像'
    entry.update(slug='ly-0910-interview-clean-v4-wide0911v2',source_check_attempts=2,
                 failed=True,source_quality_rejected=True,
                 failure_stage='editorial-or-render',last_error=reason)
    state['rejected']=[dict(slug=entry['slug'],error=reason)]
    history=deepcopy(state['rejected'])
    run['id']=34743799382
    assert fc._recover_preflight_failure(state,entry,run,[])
    assert not entry['failed'] and not entry['source_quality_rejected']
    assert entry['recovered_render_failure']['reason']==reason
    assert entry['source_check_attempts']==2 and state['rejected']==history
    assert not fc._recover_preflight_failure(state,entry,run,[])


@pytest.mark.parametrize('reason',['角标未清理','原素材清晰度不足'])
def test_exact_native_run_does_not_reopen_other_quality_verdicts(monkeypatch,reason):
    state,entry,run,calls=evidence(monkeypatch,changed='linyuan/produce_cn.py',failed_step='出片')
    entry.update(slug='ly-0910-interview-clean-v4-wide0911v2',source_check_attempts=2,
                 failed=True,source_quality_rejected=True,
                 failure_stage='editorial-or-render',last_error=reason)
    run['id']=34743799382
    assert not fc._recover_preflight_failure(state,entry,run,[])
    assert entry['failed'] and calls==[]


def test_source_fetch_retry_survives_empty_old_cache_without_resetting_history(monkeypatch):
    state,entry,run,calls=evidence(monkeypatch)
    entry.update(reprocessing_quality=False,source_check_attempts=1)
    def checked(method,path,*a,**kw):
        if path.endswith('/jobs'):
            return dict(jobs=[dict(steps=[dict(name='素材质量门禁',conclusion='success'),
                dict(name='复用同源失败任务的已完成证据',conclusion='failure')])])
        return dict(files=[dict(filename='linyuan/restore_production_evidence.py')])
    monkeypatch.setattr(fc,'gh',checked)
    assert fc._recover_preflight_failure(state,entry,run,[])
    assert entry['source_check_run_id']==run['id'] and entry['source_check_attempts']==1
    assert entry['failure_stage']=='cache-recovery' and not entry.get('failed')
    assert not fc._recover_preflight_failure(state,entry,run,[])


def test_cache_recovery_does_not_retry_after_failed_source_gate(monkeypatch):
    state,entry,run,calls=evidence(monkeypatch)
    entry.update(reprocessing_quality=False,source_check_attempts=1)
    monkeypatch.setattr(fc,'gh',lambda *a,**kw:dict(jobs=[dict(steps=[
        dict(name='素材质量门禁',conclusion='failure'),
        dict(name='复用同源失败任务的已完成证据',conclusion='failure')])]))
    assert not fc._recover_preflight_failure(state,entry,run,[])
