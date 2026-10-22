import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import transcript_audit as A
import title_rewrite as T


def test_real_automatic_answer_is_available_for_independent_title_review():
    case=json.loads((Path(__file__).parent/'fixtures/linyuan_automatic_third_party_answer.json').read_text())
    units=T.answer_reading_units([x['text'] for x in case['source_cues']])
    assert units[2].startswith('他首先')
    assert T.explicit_host_cues(units)=={1}
    roles=['unknown','host']+['guest']*(len(units)-2)
    assert T.bind_answer_focus(dict(d_main_answer_quote=''.join(units[2:5])),units,roles)==[2,3,4]


@pytest.mark.parametrize('pronoun,starter', [('他','首先'),('她','主要'),('它','之所以'),('他们','最初')])
def test_declarative_third_party_answers_are_not_sticky_host_turns(pronoun,starter):
    units=['您觉得这家公司为什么成功？',f'{pronoun}{starter}有足够的现金流，是吧？','这让经营能够持续。']
    assert T.explicit_host_cues(units)=={0}
    reading=dict(d_main_answer_quote=''.join(units[1:]))
    assert T.bind_answer_focus(reading,units,['host','guest','guest'])==[1,2]
    with pytest.raises(ValueError):T.bind_answer_focus(reading,units,['host','host','guest'])


@pytest.mark.parametrize('followup',['他首先是不是靠运气？','她主要会不会依靠补贴？','它之所以成功是否因为运气？'])
def test_real_host_followup_questions_still_blocked(followup):
    assert 1 in T.explicit_host_cues(['您觉得为什么成功？',followup,'比如有人说是运气。'])


def test_issue_is_bound_to_untouched_source_and_cannot_be_overridden_by_passed_flag(tmp_path):
    text='我们暂时观察。这个词语无法理解。'
    response=dict(passed=True,issues=[dict(sentence_id=1,suspect_quote='词语',kind='unintelligible_term',reason='原词影响理解')],explanation='需复核原音')
    proof=A.review(text,'嘉宾','cpu-model',lambda *a:json.dumps(response),tmp_path/'audit.json')
    assert proof['passed'] is False and proof['audio_verified'] is False
    assert proof['issues'][0]['quote']=='这个词语无法理解。'
    assert not A.review(text,'嘉宾','cpu-model',lambda *a:pytest.fail('valid negative cache ignored'),tmp_path/'audit.json')['passed']


def test_changed_source_requires_new_review_and_cannot_reuse_previous_clear_result(tmp_path):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        return json.dumps(dict(issues=[],explanation='未发现影响理解的文字疑点'))
    path=tmp_path/'audit.json'
    A.review('企业现金流充足。','嘉宾','model',call,path)
    result=A.review('企业现金流不足。','嘉宾','model',call,path)
    assert len(calls)==2 and result['passed'] and not result['audio_verified']


def test_invalid_sentence_id_is_retried_but_never_guessed_or_clamped(tmp_path):
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        return json.dumps(dict(issues=[dict(sentence_id=9,suspect_quote='原句',kind='unintelligible_term',reason='句意不明')],explanation='有疑点'))
    with pytest.raises(ValueError):A.review('一条真实原句。','嘉宾','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and not (tmp_path/'audit.json').exists()


@pytest.mark.parametrize('quote',['现金现金','公司公司','机会机会','我们我们','嗯'])
def test_normal_disfluency_is_recorded_but_does_not_reject_untouched_speech(tmp_path,quote):
    text='原话：'+quote+'。'
    reply=dict(issues=[dict(sentence_id=0,suspect_quote=quote,kind='unintelligible_term',reason='不够流畅')],explanation='口吃')
    proof=A.review(text,'嘉宾','model',lambda *a:json.dumps(reply),tmp_path/'audit.json')
    assert proof['passed'] and proof['issues']==[]
    assert proof['ignored_style_objections'][0]['suspect_quote']==quote
    assert proof['transcript_sha256']==A.text_digest(text)


@pytest.mark.parametrize('quote',['不不','一万一万','未买未买','道琼市指数道琼市指数'])
def test_repetition_cannot_waive_negation_number_or_unrecognized_term(quote):
    assert not A.ordinary_disfluency(quote)


def test_unbound_suspect_quote_cannot_become_rejection_evidence(tmp_path):
    reply=dict(issues=[dict(sentence_id=0,suspect_quote='原文没有',kind='unintelligible_term',reason='坏词')],explanation='疑点')
    with pytest.raises(ValueError):A.review('这里有原话。','嘉宾','model',lambda *a:json.dumps(reply),tmp_path/'audit.json')


def test_transcript_issue_is_not_a_duplicate_video_due_to_quoted_word_repeat():
    from production_diagnostics import failure_category
    assert failure_category('原始ASR存在影响理解的疑点：重复两个字')=='asr_transcript'


def test_audit_prompt_distinguishes_spoken_choices_and_metaphors_without_waiving_a_real_issue(tmp_path):
    prompts=[]
    text='我们拥抱这个行业。原词是道琼市指数。'
    def call(prompt,schema):
        prompts.append(prompt)
        return json.dumps(dict(issues=[dict(sentence_id=1,suspect_quote='道琼市指数',
            kind='unintelligible_term',reason='专名错写影响理解')],explanation='具体专名需复核'))
    proof=A.review(text,'嘉宾','model',call,tmp_path/'audit.json')
    assert '明确的备选比例' in prompts[0] and '泛指少量' in prompts[0]
    assert '真正不认识的行业词' in prompts[0]
    assert not proof['passed'] and proof['issues'][0]['suspect_quote']=='道琼市指数'


def test_old_style_audit_cache_requires_new_actual_review(tmp_path):
    path=tmp_path/'audit.json';text='企业现金流充足。';calls=[]
    path.write_text(json.dumps(dict(version=2,transcript_sha256=A.text_digest(text),
        speaker='嘉宾',model='model',issues=[],passed=True)))
    def call(prompt,schema):
        calls.append(1)
        return json.dumps(dict(issues=[],explanation='已按当前规范重新核对'))
    proof=A.review(text,'嘉宾','model',call,path)
    assert calls==[1] and proof['version']==A.VERSION and not proof['audio_verified']


@pytest.mark.parametrize('quote',['六百万以上基金','三千万以上基金','300万元以下资金'])
def test_missing_subject_in_one_well_formed_amount_requires_new_review_never_an_automatic_pass(tmp_path,quote):
    text='这个要求'+quote+'。'
    bad=dict(issues=[dict(sentence_id=0,suspect_quote=quote,kind='ambiguous_number_or_negation',
        reason='如果指基金资产规模则缺少主语，语义不够书面')],explanation='主语省略')
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        return json.dumps(bad if len(calls)==1 else dict(issues=[],explanation='重新核对数词本身没有实际异常'))
    proof=A.review(text,'林园','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and proof['passed'] and not proof['audio_verified']
    assert proof['transcript_sha256']==A.text_digest(text)
    assert proof['rejected_review_evidence'][0]['invalid_issue_evidence'][0]['suspect_quote']==quote


def test_repeated_unsupported_subject_objection_still_fails_closed(tmp_path):
    reply=dict(issues=[dict(sentence_id=0,suspect_quote='六百万以上基金',
        kind='ambiguous_number_or_negation',reason='若指基金规模则缺少主语')],explanation='主语省略')
    calls=[];path=tmp_path/'audit.json'
    with pytest.raises(ValueError,match='口语省略主语'):
        A.review('要求六百万以上基金。','林园','model',lambda *a:calls.append(a) or json.dumps(reply),path)
    assert len(calls)==2 and not path.exists()


@pytest.mark.parametrize('quote',['百分之二一','不到六百万','六百万六百万','二一百万以上基金'])
def test_actual_critical_or_malformed_quote_cannot_use_subject_objection_retry(quote):
    assert not A.unsupported_subject_objection(dict(suspect_quote=quote,
        kind='ambiguous_number_or_negation',reason='缺少主语且数字不清'))


def test_actual_conflicting_amounts_remain_blockers_even_if_subject_is_also_missing():
    assert not A.unsupported_subject_objection(dict(suspect_quote='六百万',
        kind='ambiguous_number_or_negation',reason='缺少主语，而且与后文两千万冲突'))


def test_real_negative_survives_an_extra_unsupported_subject_objection(tmp_path):
    text='这个要求六百万以上基金。能有百分之二一。'
    reply=dict(issues=[
        dict(sentence_id=0,suspect_quote='六百万以上基金',kind='ambiguous_number_or_negation',reason='缺少主语'),
        dict(sentence_id=1,suspect_quote='百分之二一',kind='ambiguous_number_or_negation',reason='二一数词不完整')],
        explanation='保留真正的数字疑点')
    calls=[]
    proof=A.review(text,'林园','model',lambda *a:calls.append(a) or json.dumps(reply),tmp_path/'audit.json')
    assert len(calls)==1 and not proof['passed']
    assert proof['issues'][0]['suspect_quote']=='百分之二一'
    assert proof['invalid_issue_evidence'][0]['suspect_quote']=='六百万以上基金'


def test_valid_negative_issue_survives_misclassified_extra_issue(tmp_path):
    text='林元谈投资。否则叫剁胳膊剁腿。'
    reply=dict(issues=[
        dict(sentence_id=0,suspect_quote='林元',kind='unintelligible_term',reason='人名存在识别疑点'),
        dict(sentence_id=1,suspect_quote='剁胳膊剁腿',kind='ambiguous_number_or_negation',reason='普通比喻')],
        explanation='人名需核实，比喻不属于数字疑点')
    calls=[]
    proof=A.review(text,'林园','model',lambda *a:calls.append(a) or json.dumps(reply),tmp_path/'audit.json')
    assert len(calls)==1 and not proof['passed']
    assert proof['issues'][0]['suspect_quote']=='林元'
    assert proof['invalid_issue_evidence'][0]['suspect_quote']=='剁胳膊剁腿'
    assert not proof['audio_verified']


def test_invalid_only_classification_never_becomes_a_pass(tmp_path):
    reply=dict(issues=[dict(sentence_id=0,suspect_quote='剁胳膊剁腿',kind='ambiguous_number_or_negation',
        reason='普通比喻')],explanation='没有数字')
    calls=[]
    with pytest.raises(ValueError,match='数字或否定'):
        A.review('否则叫剁胳膊剁腿。','林园','model',lambda *a:calls.append(a) or json.dumps(reply),tmp_path/'audit.json')
    assert len(calls)==2 and not (tmp_path/'audit.json').exists()


def test_actual_relative_zero_fact_objection_requires_new_real_review(tmp_path):
    text='到二零五七年，这个老人的消费量，相对于今天，我认为今天是零，因为五十五岁以上才慢慢开始消费。'
    issue=dict(sentence_id=0,suspect_quote='我认为今天是零',kind='ambiguous_number_or_negation',
               reason='不符合常理，当前已有消费，绝对化表述不成立')
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        return json.dumps(dict(issues=[issue],explanation='常识判断') if len(calls)==1 else
                          dict(issues=[],explanation='跨时点的相对比较，不据观点真伪推断数词识别；未听到原音'))
    proof=A.review(text,'林园','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and proof['passed'] and not proof['audio_verified']
    assert proof['transcript_sha256']==A.text_digest(text)
    assert proof['rejected_review_evidence'][0]['invalid_issue_evidence'][0]['suspect_quote']=='我认为今天是零'


def test_repeated_fact_scope_objection_remains_fail_closed(tmp_path):
    reply=dict(issues=[dict(sentence_id=0,suspect_quote='今天是零',kind='ambiguous_number_or_negation',
        reason='不符合常理，当前已有消费')],explanation='常识')
    with pytest.raises(ValueError,match='相对比较'):
        A.review('到二零五七年，相对于今天，今天是零。','林园','model',
                 lambda *a:json.dumps(reply),tmp_path/'audit.json')
    assert not (tmp_path/'audit.json').exists()


@pytest.mark.parametrize('quote',['今天不是零','今天是零点二','百分之二一','今天是零零','零'])
def test_relative_comparison_evidence_check_does_not_waive_other_critical_tokens(quote):
    assert not A.unsupported_relative_zero_objection(dict(suspect_quote=quote,
        kind='ambiguous_number_or_negation',reason='不符合常理，当前已有消费'),
        '到二零五七年，相对于今天，今天是零。')


def test_same_period_actual_conflict_and_nonrelative_zero_remain_blockers():
    issue=dict(suspect_quote='今天是零',kind='ambiguous_number_or_negation',
               reason='同一时点的两个数字矛盾，不符合常理')
    assert not A.unsupported_relative_zero_objection(issue,'到二零五七年，相对于今天，今天是零。')
    assert not A.unsupported_relative_zero_objection({**issue,'reason':'不符合常理'},'今天是零，明天为一。')
