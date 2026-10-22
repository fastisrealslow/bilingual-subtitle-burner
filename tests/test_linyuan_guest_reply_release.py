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
