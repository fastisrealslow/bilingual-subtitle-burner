"""Release a real reply for attribution, never auto-certify its speaker."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as title

UNITS=['半导体行业的一些可能，它的一个周期复','苏这些，您怎么看？',
       '他们都会，但是我这个人还比较专心。','嗯，我就聚焦于这这上面啊，消费这些，',
       '呃，当然与嘴巴相关的挺多的啊。','但是我不投，',
       '是吧？我只投我看好的，','我是在一个一我这一亩三分地里去耕耘。']


def test_actual_reply_requires_independent_attribution():
    assert title.explicit_host_cues(UNITS)=={1}
    assert title.guest_evidence_ids(UNITS,['unknown']*len(UNITS))==[]
    reading=dict(a_guest_answer='嘉宾只关注自己看好的消费，不跟投其他领域。',
        b_question_premise='主持人询问消费与半导体复苏背景下的选择。',
        c_guest_spans=[dict(a_start=2,b_end=7)])
    roles=title.bind_reading(reading,UNITS)
    assert 2 in title.guest_evidence_ids(UNITS,roles)
    assert 1 not in title.guest_evidence_ids(UNITS,['guest']*len(UNITS))


@pytest.mark.parametrize('reply',[
    '他们都会，但是我这个人还想请教您。',
    '他们都会，但是我想知道你怎么看。',
    '他们都会，但是我来总结您的意思。',
    '他们都会，但是我这个人还比较专心？',
    '他们都会，但是我这个人还比较专心，',
    '他们都会，但是我',
    '但是我这个人还比较专心。',
])
def test_host_followups_and_fragments_remain_blocked(reply):
    assert 1 in title.explicit_host_cues(['您怎么看？',reply])


def test_requires_completed_question_and_respects_next_question():
    reply=UNITS[2]
    assert 1 in title.explicit_host_cues(['您怎么看，',reply])
    blocked=title.explicit_host_cues(['您怎么看？',reply,'您觉得消费还会好吗？',
                                    '他们都会，但是您怎么看？'])
    assert 1 not in blocked and {0,2,3}<=blocked


def test_does_not_change_production_writer_profile(monkeypatch):
    import produce_cn as producer
    monkeypatch.delenv('LINYUAN_TITLE_DRAFT_PROFILE',raising=False)
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','field-story-v1')
    assert producer._copy_style_identity('林园')['title_draft_profile']=='production'


def test_actor_condition_guard_remains_in_shared_path():
    source='我只投我看好的，除非这个人不吃不喝了，真是变成机器人了，那我也不行。如果他还真是个人，他少不了。'
    for copy in ('除非变机器人，我少不了投消费','除非人变机器人，不然少不了投吃喝',
                 '人总要吃喝，我必须投消费'):
        assert title.forecast_copy_error('林园：'+copy,copy,source)


def test_literal_action_reason_is_not_forced_into_metaphor_noun():
    import publishing_experiment as experiment
    evidence=dict(kind='personal_story',subject_quote='我这个人还比较专心。',
        event_quote='我只投我看好的。',detail_quote='除非这个人不吃不喝了，真是变成机器人了，那我也不行。')
    focus=experiment.title_focus(evidence)
    assert focus['detail_actions_0']==['吃','喝']
    assert '不吃不喝' in focus['details'] and '机器人' in focus['details']
    assert experiment.title_focus_error('林园：人总得吃喝，我就只投消费',focus) is None
    assert experiment.title_focus_error('林园：我只投消费，除非人不吃不喝',focus) is None
    assert experiment.title_focus_error('林园：我只投消费，因为人得吃饭',focus)
    assert experiment.title_focus_error('林园：除非人变机器人不吃不喝，否则少不了要吃喝，我专攻消费',focus)
    prompt=experiment.story_title_prompt('原文仍须独立核对',dict(properties=dict(c_candidates={})),evidence)
    assert '正反重复' in prompt and '不改变原句条件、否定或动作主体' in prompt
    # Lexical coverage is not factual approval; the shared actor guard remains.
    assert title.forecast_copy_error('林园：人总得吃喝，所以我必须投消费',
        '人总得吃喝，所以我必须投消费',evidence['detail_quote']+'我只投消费。嗯，如果他还真是个人，他少不了。')


@pytest.mark.parametrize('detail',['不三不四的东西我不碰。','不明不白的投资我不做。'])
def test_nonverb_parallel_negation_cannot_create_action_group(detail):
    import publishing_experiment as experiment
    focus=experiment.title_focus(dict(kind='personal_story',detail_quote=detail))
    assert not any(k.startswith('detail_actions_') for k in focus)


def test_actual_host_comparison_is_not_a_personal_event():
    import publishing_experiment as experiment
    text='就是在这个过山车行情中，可能大部分人真的没有您这么坚定，就真的会很痛苦，然后一下子就吓跑了。'
    assert experiment.personal_event_error(text)
    assert experiment.personal_event_error('他们都会，但是我这个人还比较专心。') is None
    assert experiment.personal_event_error('我只投我看好的，我是在自己这一亩三分地里去耕耘。') is None


def test_actual_latest_deictic_detail_does_not_force_generic_person_noun():
    import publishing_experiment as experiment
    evidence=dict(kind='personal_story',subject_quote='消费与半导体复苏您怎么看？',
        event_quote='我只投我看好的。',detail_quote='嗯，如果他还真是个人，他少不了。')
    focus=experiment.title_focus(evidence)
    assert focus['details']==[]
    prompt=experiment.story_title_prompt('全部原文',dict(properties=dict(c_candidates={})),evidence)
    assert '泛指词不是标题关键词' in prompt and '仍须通读完整原文' in prompt


def test_action_reason_scope_reaches_real_generator_contract_before_model_call():
    import publishing_experiment as experiment
    evidence=dict(kind='personal_story',subject_quote='我只投消费。',event_quote='我只投消费。',
        detail_quote='除非这个人不吃不喝了，变成机器人了，那我也不行。')
    text=evidence['subject_quote']+evidence['detail_quote']
    focus=experiment.title_focus(evidence)
    class ModelReached(BaseException):pass
    def model(*args):raise ModelReached()
    with pytest.raises(ModelReached):
        title.generate(text,structured_model=model,editorial_focus=focus)
    with pytest.raises(ValueError,match='本段原文字词'):
        title.generate(text,structured_model=model,editorial_focus={**focus,'details':['源外理由']})
