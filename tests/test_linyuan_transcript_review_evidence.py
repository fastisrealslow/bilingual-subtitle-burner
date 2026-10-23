import copy
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
sys.path.insert(0,str(ROOT/'linyuan/fc'))
import transcript_audit as A
import index as F
from test_linyuan_spoken_range_recovery import setup_case, RANGE


@pytest.mark.parametrize('reason',[
    '原文听感上更像另一词，但无法确证。',
    '已经核听原音，发音不同。',
    '根据音频可判断这是误识别。',
])
def test_unavailable_audio_claim_requires_new_independent_decision(tmp_path,reason):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        if len(calls)==1:
            return json.dumps(dict(issues=[dict(sentence_id=0,suspect_quote='原话',
                kind='unintelligible_term',reason=reason)],explanation='需复核'))
        assert '未收到原音' in prompt
        assert list(schema['properties'])==['explanation','issues']
        return json.dumps(dict(issues=[],explanation='仅按文字重新判断，未发现具体污染'))
    proof=A.review('这是真实原话。','林园','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and proof['passed'] and not proof['audio_verified']
    assert proof['transcript_sha256']==A.text_digest('这是真实原话。')


def test_audio_claim_in_clear_explanation_cannot_become_approval(tmp_path):
    reply=json.dumps(dict(issues=[],explanation='已核听并确认原文无误'))
    calls=[]
    with pytest.raises(ValueError,match='未收到原音'):
        A.review('企业现金流充足。','林园','model',lambda *a:calls.append(1) or reply,tmp_path/'audit.json')
    assert len(calls)==2 and not (tmp_path/'audit.json').exists()


def test_real_negative_not_discarded_due_to_extra_unavailable_evidence(tmp_path):
    reply=json.dumps(dict(issues=[
        dict(sentence_id=0,suspect_quote='TCATCA',kind='unintelligible_term',reason='公司名乱码'),
        dict(sentence_id=1,suspect_quote='原话',kind='unintelligible_term',reason='听感像另一词')],
        explanation='根据音频可确认，且有具体乱码'))
    proof=A.review('TCATCA。这里有原话。','林园','model',lambda *a:reply,tmp_path/'audit.json')
    assert not proof['passed'] and [i['suspect_quote'] for i in proof['issues']]==['TCATCA']
    assert len(proof['invalid_issue_evidence'])==2 and not proof['audio_verified']


def test_prior_text_only_policy_cache_requires_actual_fresh_decision(tmp_path):
    path=tmp_path/'audit.json';text='企业有自己的道理。';calls=[]
    path.write_text(json.dumps(dict(version=A.VERSION,speaker='林园',model='model',
        transcript_sha256=A.text_digest(text),spoken_range_policy_version=2026102305,
        issues=[dict(reason='听感像其他词')],passed=False,audio_verified=False)))
    proof=A.review(text,'林园','model',lambda *a:calls.append(1) or json.dumps(
        dict(issues=[],explanation='仅按文字重新判断，无具体污染')),path)
    assert calls==[1] and proof['passed'] and not proof['audio_verified']
    assert proof['spoken_range_policy_version']==2026102308


def test_audio_retry_is_source_only_not_anchored_to_previous_hallucination(tmp_path):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        if len(calls)==1:
            return json.dumps(dict(issues=[dict(sentence_id=0,suspect_quote='原话',
                kind='unintelligible_term',reason='听感像虚构替换词ABC')],explanation='需复核'))
        assert '虚构替换词ABC' not in prompt and '上次响应格式错误' not in prompt
        assert '连读前后句' in prompt and '错词、数字或否定疑点仍必须列出' in prompt
        return json.dumps(dict(issues=[],explanation='没有具体识别污染'))
    proof=A.review('这是真实原话。','林园','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and proof['passed'] and not proof['audio_verified']


def test_critical_fact_scope_never_waives_real_company_corruption(tmp_path):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        assert '专名错写' in prompt and '即使整体能猜懂也必须报告' in prompt
        assert '普通通用词不要求是行业术语' in prompt
        return json.dumps(dict(issues=[dict(sentence_id=0,suspect_quote='TCATCA',
            kind='unintelligible_term',reason='具体公司专名乱码')],explanation='名字污染须复核'))
    proof=A.review('TCATCA是后来的。','林园','model',call,tmp_path/'audit.json')
    assert len(calls)==1 and not proof['passed'] and not proof['audio_verified']


@pytest.mark.parametrize('reason',[
    '未听到原音，需核对原音。','无法核实发音。','应回听原音核实。',
    '没有听感证据，不能猜改。','具体公司名乱码，需人工核听。'])
def test_lack_of_audio_and_requests_are_not_falsely_matched(reason):
    assert not A.AUDIO_ACCESS_CLAIM.search(reason)


def modality_case(monkeypatch,reason):
    state,entry,run,writes=setup_case(monkeypatch,reason)
    monkeypatch.setattr(F,'gh',lambda *a,**k:dict(files=[dict(
        filename='linyuan/transcript_audit.py',patch='+SPOKEN_RANGE_POLICY_VERSION = 2026102306\n')]))
    return state,entry,run,writes


def test_audio_claim_recovery_is_once_only_and_preserves_lifetime_counters(monkeypatch):
    reason='原始ASR存在影响理解的疑点，禁止猜改后发布：原文听感上更像别的词'
    state,entry,run,writes=modality_case(monkeypatch,reason)
    assert F._recover_changed_production_rule(state,entry,run)
    assert entry['source_check_attempts']==2 and entry['quality_retries']==1
    assert entry['source_check_run_id']==123 and not state['published']
    assert 'transcript-text-only-evidence-v1' in entry['automatic_rule_recoveries']
    entry['failed']=True;before=copy.deepcopy(entry)
    assert not F._recover_changed_production_rule(state,entry,run)
    assert entry==before and len(writes)==1


def test_modality_followup_keeps_prior_bounded_range_recovery(monkeypatch):
    state,entry,run,writes=modality_case(monkeypatch,RANGE)
    assert F._recover_changed_production_rule(state,entry,run)
    assert 'transcript-spoken-ranges-v1' in entry['automatic_rule_recoveries']
    assert 'transcript-text-only-evidence-v1' not in entry['automatic_rule_recoveries']
    assert entry['source_check_attempts']==2 and entry['quality_retries']==1


@pytest.mark.parametrize('reason',[
    '原始ASR存在影响理解的疑点，禁止猜改后发布：TCATCA公司名乱码',
    '原始ASR存在影响理解的疑点，禁止猜改后发布：百分之元单位不明',
    '原始ASR存在影响理解的疑点，禁止猜改后发布：五六块钱与六十五块钱冲突',
    '原始ASR存在影响理解的疑点，禁止猜改后发布：需核实原音，不能猜词',
    '独立文字识别疑点审核不可用：invalid evidence',
    '原文中未找到满足20秒且起止边界明确的连续候选',
])
def test_modality_only_patch_never_reopens_other_asr_or_source_failures(monkeypatch,reason):
    state,entry,run,writes=modality_case(monkeypatch,reason)
    assert not F._recover_changed_production_rule(state,entry,run)
    assert entry['failed'] and not writes


@pytest.mark.parametrize('future_policy',[2026102309,2026102310,2026102401])
def test_future_cache_version_cannot_fall_through_legacy_broad_asr_recovery(monkeypatch,future_policy):
    state,entry,run,writes=modality_case(monkeypatch,
        '原始ASR存在影响理解的疑点，禁止猜改后发布：TCATCA公司名乱码')
    monkeypatch.setattr(F,'gh',lambda *a,**k:dict(files=[dict(
        filename='linyuan/transcript_audit.py',patch=f'+SPOKEN_RANGE_POLICY_VERSION = {future_policy}\n')]))
    assert not F._recover_changed_production_rule(state,entry,run)
    assert entry['failed'] and not writes and entry['source_check_attempts']==2


@pytest.mark.parametrize('changes',[
    dict(run_id=999),dict(slug='other'),dict(accepted=1),dict(artifact_id=0),dict(accepted=False)])
def test_unbound_secondary_audio_claim_cannot_authorize_recovery(monkeypatch,changes):
    state,entry,run,writes=modality_case(monkeypatch,
        '原始ASR存在影响理解的疑点，禁止猜改后发布：TCATCA公司名乱码')
    report=dict(slug='mother',run_id=123,artifact_id=456,accepted=0,
        failures=[dict(stage='part-quality',reason=
            '原始ASR存在影响理解的疑点，禁止猜改后发布：听感像其他词')])
    report.update(changes);entry['production_failure_summary']=report
    assert not F._recover_changed_production_rule(state,entry,run) and not writes
