"""A matched visible face does not identify the active speaker of its audio."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import speaker_attribution as A
import source_selection as S
import title_rewrite as T


def test_actual_other_guest_answer_is_excluded_and_target_turn_remains():
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_multi_guest_source8.json').read_text())
    cues=data['cues'];texts=[c['text'] for c in cues]
    turns=A.named_handoffs(texts)
    assert [(t['cue'],t['addressee'],t['target']) for t in turns]==[(3,'郭',False),(57,'林',True)]
    blocked=A.other_guest_indices(texts)
    assert set(range(3,57))<=blocked
    assert not set(range(57,len(texts)))&blocked
    assert '其他嘉宾' in S.boundary_error(cues,dict(start=3,end=55))
    assert not A.selection_error(cues,dict(start=57,end=100))
    assert not set(T.guest_evidence_ids(texts,['guest']*len(texts)))&blocked
    with pytest.raises(ValueError,match='其他嘉宾'):
        T._extractive(''.join(texts[3:57]),'林园',())


def test_actual_target_selection_keeps_named_premise_and_stops_before_host_recap(monkeypatch):
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_multi_guest_source8.json').read_text())
    cues=data['cues'];monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    picks=S.select(cues,limit=12,whole_source=True)
    target=next(p for p in picks if cues[p['start']]['start']==378.76)
    assert cues[target['end']]['end']<=497.08
    text=''.join(c['text'] for c in cues[target['start']:target['end']+1])
    assert '四千五百' in text and '林总' in text
    assert '总结来看' not in text


def test_name_split_across_asr_cues_and_later_named_reset():
    texts=['我想请教一下郭','总，您怎么看？','我觉得市场不能过度乐观。',
           '那这个问题请教一下林总，','我的判断与上个回答不一样。']
    assert A.other_guest_indices(texts)=={0,1,2}
    assert T.guest_evidence_ids(texts,['guest']*5)==[4]


def test_single_cue_with_multiple_guests_is_not_assigned_to_target():
    text='我想请教一下郭总，您怎么看？医药我不买。请问林总，您呢？'
    assert A.other_guest_indices([text])=={0}
    item=dict(title='林园：医药公司我现在不买入',cover_title='医药公司我现在不买入')
    assert '其他嘉宾' in T._candidate_error(item,text,'林园',())


@pytest.mark.parametrize('texts', [
    ['林总，您怎么看？','我看好消费企业，但选择要谨慎。'],
    ['郭总之前讲过科技股，林总讲了消费。','我自己的观点与他们不同。'],
    ['他说：“请问郭总，您怎么看？”','我只是在举例，不是主持人转交提问。'],
    ['我以前问过一个朋友。他回答，买医药要看经营。'],
])
def test_mentions_quotes_and_target_questions_do_not_create_other_guest_turn(texts):
    assert not A.other_guest_indices(texts)


def test_explicit_named_invitation_can_end_other_guest_exclusion():
    texts=['请问陈老师，您怎么看？','我认为科技机会更多。','让林总回答一下这个问题。','我的选择是消费和医药。']
    assert A.other_guest_indices(texts)=={0,1}
