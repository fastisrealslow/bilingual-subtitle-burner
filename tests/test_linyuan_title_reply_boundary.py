"""A third-person acknowledgement may precede a real first-person answer."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as title

UNITS=['半导体行业的一些可能，它的一个周期复','苏这些，您怎么看？',
       '他们都会，但是我这个人还比较专心。','嗯，我就聚焦于这这上面啊，消费这些，',
       '呃，当然与嘴巴相关的挺多的啊。','但是我不投，',
       '是吧？我只投我看好的，','我是在一个一我这一亩三分地里去耕耘。']


def test_actual_answer_is_available_to_independent_reader_not_auto_certified():
    assert title.explicit_host_cues(UNITS)=={1}
    assert title.guest_evidence_ids(UNITS,['unknown']*len(UNITS))==[]
    reading=dict(a_guest_answer='嘉宾明确表示自己只关注与嘴巴相关的消费，不跟投其他领域。',
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
def test_host_followups_questions_and_fragments_stay_excluded(reply):
    assert 1 in title.explicit_host_cues(['您怎么看？',reply])


def test_requires_prior_completed_question_and_preserves_later_new_questions():
    reply=UNITS[2]
    assert 1 in title.explicit_host_cues(['您怎么看，',reply])
    units=['您怎么看？',reply,'您觉得消费还会好吗？','他们都会，但是您怎么看？']
    blocked=title.explicit_host_cues(units)
    assert 1 not in blocked and {0,2,3}<=blocked


def test_actual_cpu_draft_cannot_change_people_condition_to_investor_condition():
    source='我只投我看好的，除非这个人不吃不喝了，真是变成机器人了，那我也不行。'
    bad='除非变机器人，我少不了投消费'
    assert title.human_condition_actor_error('林园：'+bad,bad,source)
    assert title.forecast_copy_error('林园：'+bad,bad,source)
    source+='如果他还真是个人，他少不了。'
    also_bad='除非人变机器人，我少不了投消费'
    assert title.forecast_copy_error('林园：'+also_bad,also_bad,source)
    subject_omitted='除非人变机器人，不然少不了投吃喝'
    assert title.forecast_copy_error('林园：'+subject_omitted,subject_omitted,source)
    for copy in ('人总要吃喝，我必须投消费','人得吃喝，我不得不投消费','人人就得投消费'):
        assert title.forecast_copy_error('林园：'+copy,copy,source)
    assert title.human_condition_actor_error('林园：除非人变机器人，我只投消费',
        '除非大家变机器人，我只投消费',source) is None
    assert title.human_condition_actor_error('林园：人总得吃喝，我就只投消费',
        '人总得吃喝，我就只投消费',source) is None
    assert title.human_condition_actor_error('林园：除非变机器人，我不再做饭',
        '除非变机器人，我不再做饭','除非我变成机器人，否则我每天都要做饭。') is None


def test_literal_complete_reason_is_an_alternative_to_forced_metaphor_noun():
    import publishing_experiment as experiment
    selection=dict(kind='personal_story',subject_quote='我这个人还比较专心。',
        event_quote='我只投我看好的。',detail_quote='除非这个人不吃不喝了，真是变成机器人了，那我也不行。')
    focus=experiment.title_focus(selection)
    assert '不吃不喝' in focus['details'] and '机器人' in focus['details']
    assert focus['detail_verbs_0']==['吃','喝']
    assert all(term in selection['detail_quote'] for term in focus['details'])
    assert experiment.title_focus_error('林园：我只投消费，除非人不吃不喝',focus) is None
    assert experiment.title_focus_error('林园：人总得吃喝，我就专心投消费',focus) is None
    assert experiment.title_focus_error('林园：我只投消费，人还得吃饭',focus)
    assert experiment.title_focus_error('林园：只要人变机器人，我就买半导体',focus) is None  # lexical coverage is NOT factual approval
    assert title.forecast_copy_error('林园：除非变机器人，我少不了投消费',
        '除非变机器人，我少不了投消费','我只投消费。除非这个人不吃不喝了，变成机器人，那我也不行。如果他还是个人，他少不了。')


def test_treatment_uses_existing_strict_sentence_and_object_reader(monkeypatch):
    import produce_cn as producer
    monkeypatch.delenv('LINYUAN_TITLE_DRAFT_PROFILE',raising=False)
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','field-story-v1')
    assert producer.title_draft_profile('林园')=='answer_subject'
    assert producer._copy_style_identity('林园')['title_draft_profile']=='answer_subject'
    company=dict(kind='company_case')
    assert producer.title_draft_profile('林园',company)=='production'
    assert producer._copy_style_identity('林园',company)['title_draft_profile']=='production'
    assert producer.title_draft_profile('其他嘉宾')=='production'
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','baseline')
    assert producer.title_draft_profile('林园')=='production'
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','answer_focus')
    assert producer.title_draft_profile('林园')=='answer_focus'
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','unknown')
    with pytest.raises(ValueError):producer.title_draft_profile('林园')


def test_display_wraps_cannot_split_people_condition_or_question_context():
    raw=['那如果看好半导体行业周期复','苏这些，您怎么看？','他们都会，但是我这个人还比较专心。',
         '我只投消费。','除非这','个人不吃不喝了，变成机器人了，','那我也不行。']
    units=title.answer_reading_units(raw,'林园')
    assert ''.join(units)==''.join(raw)
    assert any('这个人不吃不喝' in unit for unit in units)
    assert any('周期复苏这些，您怎么看？' in unit for unit in units)
    blocked=title.explicit_host_cues(units)
    assert 0 in blocked
    assert next(i for i,u in enumerate(units) if '他们都会' in u) not in blocked


@pytest.mark.parametrize('prefix',['我只投消费，','对于消费我比较专心，'])
def test_sentence_reader_never_glues_first_person_guest_into_next_question(prefix):
    units=title.answer_reading_units([prefix,'您怎么看半导体？','我还要研究。'],'林园')
    assert units[0]==prefix and units[1]=='您怎么看半导体？'


def test_parallel_nonverbs_cannot_create_reason_coverage():
    import publishing_experiment as experiment
    focus=experiment.title_focus(dict(kind='personal_story',subject_quote='我只做我懂的。',
        event_quote='我先做研究。',detail_quote='不三不四的东西我不碰。'))
    assert not any(key.startswith('detail_verbs_') for key in focus)
