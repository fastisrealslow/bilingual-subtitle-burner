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

def review_json(value):
    """Explicit verdicts for review fixtures; raw JSON remains available for malformed-contract tests."""
    if isinstance(value,dict) and isinstance(value.get('issues'),list) and 'explanation' in value:
        value={'verdict':'needs_audio' if value['issues'] else 'clear',**value}
    return json.dumps(value)



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
            return review_json(dict(issues=[dict(sentence_id=0,suspect_quote='原话',
                kind='unintelligible_term',reason=reason)],explanation='需复核'))
        assert '未收到原音' in prompt
        assert list(schema['properties'])==['issues','explanation','verdict']
        return review_json(dict(issues=[],explanation='仅按文字重新判断，未发现具体污染'))
    proof=A.review('这是真实原话。','林园','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and proof['passed'] and not proof['audio_verified']
    assert proof['transcript_sha256']==A.text_digest('这是真实原话。')


def test_audio_claim_in_clear_explanation_cannot_become_approval(tmp_path):
    reply=review_json(dict(issues=[],explanation='已核听并确认原文无误'))
    calls=[]
    with pytest.raises(ValueError,match='未收到原音'):
        A.review('企业现金流充足。','林园','model',lambda *a:calls.append(1) or reply,tmp_path/'audit.json')
    assert len(calls)==2 and not (tmp_path/'audit.json').exists()


@pytest.mark.parametrize('verdict',[None,'approved','clear','needs_audio'])
def test_explicit_decision_contract_cannot_clear_contradictory_or_missing_evidence(tmp_path,verdict):
    issues=[] if verdict=='needs_audio' else [dict(sentence_id=0,suspect_quote='TCATCA',
        kind='unintelligible_term',reason='具体公司名乱码')]
    reply=json.dumps(dict(verdict=verdict,issues=issues,explanation='协议必须一致'))
    calls=[]
    with pytest.raises(ValueError):
        A.review('TCATCA是后来的。','林园','model',lambda *a:calls.append(a) or reply,tmp_path/'audit.json')
    assert len(calls)==2 and not (tmp_path/'audit.json').exists()


def test_explicit_clear_decision_does_not_waive_unavailable_audio_claim(tmp_path):
    calls=[]
    reply=json.dumps(dict(verdict='clear',issues=[],explanation='已经回听原音确认无误'))
    with pytest.raises(ValueError,match='未收到原音'):
        A.review('企业现金流充足。','林园','model',lambda *a:calls.append(a) or reply,tmp_path/'audit.json')
    assert len(calls)==2 and not (tmp_path/'audit.json').exists()


def test_explicit_negative_cannot_be_overridden_by_clear_summary(tmp_path):
    reply=dict(verdict='needs_audio',issues=[dict(sentence_id=0,suspect_quote='TCATCA',
        kind='unintelligible_term',reason='具体公司名乱码')],explanation='其他语句均可理解')
    proof=A.review('TCATCA是后来的。','林园','model',lambda *a:json.dumps(reply),tmp_path/'audit.json')
    assert not proof['passed'] and proof['verdict']=='needs_audio' and not proof['audio_verified']


def test_new_contract_cache_without_explicit_decision_is_rejected(tmp_path):
    path=tmp_path/'audit.json';text='企业现金流充足。';calls=[]
    path.write_text(json.dumps(dict(version=A.VERSION,transcript_sha256=A.text_digest(text),
        speaker='林园',model='model',spoken_range_policy_version=A.SPOKEN_RANGE_POLICY_VERSION,
        issues=[],passed=True)))
    proof=A.review(text,'林园','model',lambda *a:calls.append(a) or json.dumps(
        dict(verdict='clear',issues=[],explanation='无具体污染')),path)
    assert len(calls)==1 and proof['passed'] and proof['verdict']=='clear'


def test_real_negative_not_discarded_due_to_extra_unavailable_evidence(tmp_path):
    reply=review_json(dict(issues=[
        dict(sentence_id=0,suspect_quote='TCATCA',kind='unintelligible_term',reason='公司名乱码'),
        dict(sentence_id=1,suspect_quote='原话',kind='unintelligible_term',reason='听感像另一词')],
        explanation='根据音频可确认，且有具体乱码'))
    proof=A.review('TCATCA。这里有原话。','林园','model',lambda *a:reply,tmp_path/'audit.json')
    assert not proof['passed'] and [i['suspect_quote'] for i in proof['issues']]==['TCATCA']
    assert len(proof['invalid_issue_evidence'])==2 and not proof['audio_verified']


def test_prior_text_only_policy_cache_requires_actual_fresh_decision(tmp_path):
    path=tmp_path/'audit.json';text='企业有自己的道理。';calls=[]
    path.write_text(review_json(dict(version=A.VERSION,speaker='林园',model='model',
        transcript_sha256=A.text_digest(text),spoken_range_policy_version=2026102305,
        issues=[dict(reason='听感像其他词')],passed=False,audio_verified=False)))
    proof=A.review(text,'林园','model',lambda *a:calls.append(1) or review_json(
        dict(issues=[],explanation='仅按文字重新判断，无具体污染')),path)
    assert calls==[1] and proof['passed'] and not proof['audio_verified']
    assert proof['spoken_range_policy_version']==A.SPOKEN_RANGE_POLICY_VERSION


def test_layout_spaces_retain_real_negative_not_approve_or_rewrite_source(tmp_path):
    text='一大堆长虹、康佳、TCATCA是后来的，是吧？'
    reply=dict(issues=[dict(sentence_id=0,suspect_quote='一大堆长虹、康佳、TCATCA 是后来的',
        kind='unintelligible_term',reason='具体公司名乱码')],explanation='保留真实专名疑点')
    proof=A.review(text,'林园','model',lambda *a:review_json(reply),tmp_path/'audit.json')
    assert not proof['passed'] and proof['transcript_sha256']==A.text_digest(text)
    item=proof['issues'][0]
    assert item['suspect_quote']=='一大堆长虹、康佳、TCATCA是后来的'
    assert item['quote']==text and item['reported_suspect_quote']==reply['issues'][0]['suspect_quote']
    assert item['quote_binding']=='unique_contiguous_cjk_latin_layout_space'


@pytest.mark.parametrize('quote,source',[
    ('TCATCA是','TCA TCA是另一个名字。'),
    ('10元','10 元是这个报价。'),
    ('不 买','不买是原话。'),
    ('TCL买','TCL不买是原话。'),
    ('TCL 是','TCL是甲，TCL是乙。'),
])
def test_layout_binding_cannot_join_identifiers_numbers_negations_or_ambiguous_spans(quote,source):
    assert A.bind_layout_quote(quote,source) is None


def test_same_issue_contradictory_copies_require_independent_decision(tmp_path):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        if len(calls)==1:
            base=dict(sentence_id=0,suspect_quote='五六块钱',kind='ambiguous_number_or_negation')
            return review_json(dict(issues=[{**base,'reason':'范围表述，理由被截断。'},
                {**base,'reason':'该句未构成识别污染，无需报错。'}],explanation='正常口语范围'))
        return review_json(dict(issues=[],explanation='独立核对完整原文，无具体污染'))
    proof=A.review('大概五六块钱。','林园','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and proof['passed'] and not proof['audio_verified']


def test_same_issue_conflict_is_not_automatic_clearance_of_real_corruption(tmp_path):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        base=dict(sentence_id=0,suspect_quote='TCATCA',kind='unintelligible_term')
        issues=[{**base,'reason':'具体公司名乱码'}]
        if len(calls)==1:issues.append({**base,'reason':'未构成识别污染'})
        return review_json(dict(issues=issues,explanation='需核实具体公司名'))
    proof=A.review('TCATCA是后来的。','林园','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and not proof['passed'] and proof['issues'][0]['suspect_quote']=='TCATCA'


@pytest.mark.parametrize('quote,reason',[
    ('TCATCA','专名识别污染：异常字母堆积，无法确定实体，需复核原音。'),
    ('林元','人名错写，具体人物身份需核实。'),
])
def test_wrong_numeric_label_cannot_discard_explicit_source_bound_entity_negative(tmp_path,quote,reason):
    text=quote+'谈投资。';calls=[]
    reply=dict(verdict='needs_audio',issues=[dict(sentence_id=0,suspect_quote=quote,
        kind='ambiguous_number_or_negation',reason=reason)],explanation='需核实具体实体')
    proof=A.review(text,'林园','model',lambda *a:calls.append(a) or json.dumps(reply),tmp_path/'audit.json')
    assert len(calls)==1 and not proof['passed'] and not proof['audio_verified']
    issue=proof['issues'][0]
    assert issue['suspect_quote']==quote and issue['quote']==text
    assert issue['kind']=='unintelligible_term' and issue['reported_kind']=='ambiguous_number_or_negation'
    assert issue['kind_binding']=='explicit_entity_contamination_negative_only'
    assert proof['transcript_sha256']==A.text_digest(text)


def test_audio_retry_is_source_only_not_anchored_to_previous_hallucination(tmp_path):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        if len(calls)==1:
            return review_json(dict(issues=[dict(sentence_id=0,suspect_quote='原话',
                kind='unintelligible_term',reason='听感像虚构替换词ABC')],explanation='需复核'))
        assert '虚构替换词ABC' not in prompt and '上次响应格式错误' not in prompt
        assert '连读前后句' in prompt and '错词、数字或否定疑点仍必须列出' in prompt
        return review_json(dict(issues=[],explanation='没有具体识别污染'))
    proof=A.review('这是真实原话。','林园','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and proof['passed'] and not proof['audio_verified']


def test_critical_fact_scope_never_waives_real_company_corruption(tmp_path):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        assert '专名错写' in prompt and '即使整体能猜懂也必须报告' in prompt
        assert '普通通用词不要求是行业术语' in prompt
        return review_json(dict(issues=[dict(sentence_id=0,suspect_quote='TCATCA',
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
    RANGE,
    '原始ASR存在影响理解的疑点，禁止猜改后发布：原文听感上更像别的词',
])
@pytest.mark.parametrize('policy',[2026102311,2026102312])
def test_explicit_decision_policy_keeps_only_existing_scoped_recovery(monkeypatch,reason,policy):
    state,entry,run,writes=modality_case(monkeypatch,reason)
    monkeypatch.setattr(F,'gh',lambda *a,**k:dict(files=[dict(
        filename='linyuan/transcript_audit.py',patch=f'+SPOKEN_RANGE_POLICY_VERSION = {policy}\n')]))
    assert F._recover_changed_production_rule(state,entry,run)
    assert entry['source_check_attempts']==2 and entry['quality_retries']==1 and len(writes)==1


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


@pytest.mark.parametrize('future_policy',[2026102309,2026102310,2026102311,2026102312,2026102401])
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
