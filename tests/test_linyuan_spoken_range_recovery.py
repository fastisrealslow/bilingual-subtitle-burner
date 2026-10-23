"""A range-only prompt change must not resurrect unrelated ASR failures."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import index as F

PATCH='+SPOKEN_RANGE_POLICY_VERSION = 2026102303\n'
RANGE='原始ASR存在影响理解的疑点，禁止猜改后发布：'+json.dumps([
    dict(suspect_quote='五六块钱',kind='ambiguous_number_or_negation',
         reason='原文为口语化的模糊数字范围，非精确数值冲突，但需精确读法。')],ensure_ascii=False)


def setup_case(monkeypatch,reason):
    entry=dict(slug='mother',ts=1,failed=True,source_quality_rejected=True,
        source_url='https://example.com/mother',last_error=reason,source_check_attempts=2,
        quality_retries=1)
    state=dict(dispatched=[entry],published={})
    run=dict(id=123,head_sha='old',conclusion='failure',updated_at='2026-10-23T00:00:00Z')
    monkeypatch.setattr(F,'gh',lambda *a,**k:dict(files=[dict(
        filename='linyuan/transcript_audit.py',patch=PATCH)]))
    writes=[]
    monkeypatch.setattr(F,'save_state',lambda *a:writes.append(True))
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    return state,entry,run,writes


@pytest.mark.parametrize('reason',[
    '原始ASR存在影响理解的疑点，禁止猜改后发布：TCATCA公司名错误',
    '原始ASR存在影响理解的疑点，禁止猜改后发布：五六块钱与六十五块钱实际冲突',
    '原始ASR存在影响理解的疑点，禁止猜改后发布：否定字识别不清',
    '独立文字识别疑点审核不可用：invalid evidence',
    '真人动态区短边不足480',
    '原文中未找到满足20秒且起止边界明确的连续候选',
])
def test_range_only_patch_cannot_reopen_real_asr_or_unrelated_failures(monkeypatch,reason):
    state,entry,run,writes=setup_case(monkeypatch,reason)
    assert not F._recover_changed_production_rule(state,entry,run)
    assert not writes and entry['failed']
    assert entry['source_check_attempts']==2 and entry['quality_retries']==1


def test_recorded_self_contradictory_range_complaint_recovers_once_not_approval(monkeypatch):
    state,entry,run,writes=setup_case(monkeypatch,RANGE)
    assert F._recover_changed_production_rule(state,entry,run)
    assert len(writes)==1 and not entry['failed']
    assert entry['source_check_run_id']==run['id']
    assert entry['source_check_attempts']==2 and entry['quality_retries']==1
    assert not state['published']
    assert 'transcript-spoken-ranges-v1' in entry['automatic_rule_recoveries']
    entry['failed']=True;before=copy.deepcopy(entry)
    assert not F._recover_changed_production_rule(state,entry,run)
    assert entry==before and len(writes)==1


def test_actual_secondary_range_failure_does_not_waive_primary_corrupt_name(monkeypatch):
    state,entry,run,writes=setup_case(monkeypatch,
        '原始ASR存在影响理解的疑点，禁止猜改后发布：TCATCA公司名错误')
    entry['production_failure_summary']=dict(slug='mother',run_id=123,artifact_id=456,accepted=0,
        failures=[dict(stage='part-quality',reason=RANGE[:320])])
    assert F._recover_changed_production_rule(state,entry,run)
    # Recovery is only an intent: raw source and both parts are re-evaluated.
    assert entry['last_error'].endswith('TCATCA公司名错误')
    assert entry['source_check_run_id']==123 and not state['published']


@pytest.mark.parametrize('changes',[
    dict(run_id=999),dict(slug='other'),dict(accepted=1),dict(artifact_id=0),dict(accepted=False)])
def test_unbound_secondary_report_cannot_authorize_recovery(monkeypatch,changes):
    state,entry,run,writes=setup_case(monkeypatch,
        '原始ASR存在影响理解的疑点，禁止猜改后发布：TCATCA公司名错误')
    summary=dict(slug='mother',run_id=123,artifact_id=456,accepted=0,
                 failures=[dict(stage='part-quality',reason=RANGE)])
    summary.update(changes);entry['production_failure_summary']=summary
    assert not F._recover_changed_production_rule(state,entry,run)
    assert not writes


def test_existing_legacy_recovery_history_is_preserved(monkeypatch):
    state,entry,run,writes=setup_case(monkeypatch,RANGE)
    legacy={'transcript-review-model-v3':dict(run_id=99,reason='old failure')}
    entry['automatic_rule_recoveries']=copy.deepcopy(legacy)
    assert F._recover_changed_production_rule(state,entry,run)
    assert entry['automatic_rule_recoveries']['transcript-review-model-v3']==legacy['transcript-review-model-v3']
