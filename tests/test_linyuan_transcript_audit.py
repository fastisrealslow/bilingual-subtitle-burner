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
    response=dict(passed=True,issues=[dict(sentence_id=1,kind='unintelligible_term',reason='原词影响理解')],explanation='需复核原音')
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
        return json.dumps(dict(issues=[dict(sentence_id=9,kind='broken_syntax',reason='句意不明')],explanation='有疑点'))
    with pytest.raises(ValueError):A.review('一条真实原句。','嘉宾','model',call,tmp_path/'audit.json')
    assert len(calls)==2 and not (tmp_path/'audit.json').exists()
