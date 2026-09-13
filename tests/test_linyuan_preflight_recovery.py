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
