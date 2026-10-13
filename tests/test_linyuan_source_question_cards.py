from pathlib import Path
import json
import pytest
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_question_cards as C


def card(index,text='Q1：您怎么看医药行业？Q2：投资风险怎么控制？'):
    samples=[dict(time=1,text=text),dict(time=2,text=text)]
    return dict(next_cue=index,samples=samples,question=C.agreed_question(samples))


def test_multi_question_card_is_not_approved_as_one_structural_answer(monkeypatch):
    import source_selection as ss
    monkeypatch.setattr(ss.editorial,'CONTENT_POLICY','reference_v1')
    cues=[dict(start=10,end=40,text='医药行业需求持续增长。'),
          dict(start=40,end=65,text='但是股票投资仍有风险。')]
    observed=card(0)
    assert observed['question']['question_count']==2
    assert C.chapter_ranges(cues,[observed])==[]


def test_topic_discovery_preserves_every_original_cue_and_does_not_invent_approval(tmp_path,monkeypatch):
    import source_selection as ss
    monkeypatch.setattr(ss.editorial,'CONTENT_POLICY','reference_v1')
    cues=[dict(start=10,end=40,text='医药行业需求持续增长。'),
        dict(start=40,end=65,text='人口变化是长期需求的来源。'),
        dict(start=65,end=85,text='股票投资需要控制风险。'),
        dict(start=85,end=110,text='价格太高时不能盲目买入。')]
    before=json.dumps(cues,ensure_ascii=False)
    def call(prompt,schema):
        assert '不是嘉宾说过的话' in prompt
        assert schema['properties']['topics']['items']['properties']['start']['enum']==[0,1,2,3]
        return json.dumps(dict(topics=[dict(start=0,topic='医药需求'),dict(start=2,topic='投资风险')]))
    picks=C.discover_topics(cues,[card(0)],call,tmp_path,'a'*64)
    assert [(p['start'],p['end']) for p in picks]==[(0,1),(2,3)]
    assert all(p['editorial_approved'] is False and 'passed' not in p for p in picks)
    assert json.dumps(cues,ensure_ascii=False)==before
    proof=json.loads((tmp_path/'topic-discovery.json').read_text())
    assert not proof['editorial_approved'] and proof['source_sha256']=='a'*64


def test_unread_or_unexamined_next_card_remains_a_hard_stop(tmp_path,monkeypatch):
    import source_selection as ss
    monkeypatch.setattr(ss.editorial,'CONTENT_POLICY','reference_v1')
    cues=[dict(start=10,end=35,text='医药行业需求持续增长。'),
        dict(start=35,end=60,text='人口变化带来长期需求。'),
        dict(start=70,end=95,text='另一问题需要单独研究。')]
    seen=[]
    def call(prompt,schema):
        seen.append(prompt)
        return json.dumps(dict(topics=[dict(start=0,topic='医药需求')]))
    picks=C.discover_topics(cues,[card(0),dict(next_cue=2,question=None)],call,tmp_path,'a'*64)
    assert [(p['start'],p['end']) for p in picks]==[(0,1)]
    assert len(seen)==1 and '另一问题' not in seen[0]


def test_invalid_topic_boundary_is_not_an_empty_quality_verdict(tmp_path):
    cues=[dict(start=0,end=10,text='医药行业的'),dict(start=10,end=50,text='需求持续增长。')]
    def call(*a):return json.dumps(dict(topics=[dict(start=0,topic='甲'),dict(start=1,topic='乙')]))
    with pytest.raises(RuntimeError,match='服务未完成'):
        C.discover_topics(cues,[card(0)],call,tmp_path,'a'*64)
    proof=json.loads((tmp_path/'topic-discovery.json').read_text())
    assert proof['blocks'][0]['service_error'] and not proof['picks']


def test_later_model_outage_does_not_erase_earlier_valid_proposals(tmp_path,monkeypatch):
    import source_selection as ss
    monkeypatch.setattr(ss.editorial,'CONTENT_POLICY','reference_v1')
    cues=[dict(start=10,end=35,text='医药行业需求持续增长。'),
        dict(start=35,end=60,text='人口变化带来长期需求。'),
        dict(start=70,end=95,text='投资需要控制风险。'),
        dict(start=95,end=120,text='价格太高时不宜追涨。')]
    calls=[]
    def call(*a):
        calls.append(1)
        if len(calls)==2:raise TimeoutError('CPU unavailable')
        return json.dumps(dict(topics=[dict(start=0,topic='医药需求')]))
    picks=C.discover_topics(cues,[card(0),card(2)],call,tmp_path,'a'*64)
    assert [(p['start'],p['end']) for p in picks]==[(0,1)]
    assert json.loads((tmp_path/'topic-discovery.json').read_text())['blocks'][1]['service_error']


def test_only_repeated_real_questions_can_anchor_a_card():
    q='对于看好的投资标的，您在持仓方面是怎样把握的？'
    assert C.agreed_question([dict(time=1,text=q)]) is None
    assert C.agreed_question([dict(time=1,text=q),dict(time=1,text=q)]) is None
    assert C.agreed_question([dict(time=1,text=q),dict(time=2,text=q)])['text']==q
    assert C.agreed_question([dict(time=1,text='未来值得长期投资的行业'),dict(time=2,text='未来值得长期投资的行业')]) is None


def test_unread_next_card_still_closes_previous_answer(monkeypatch):
    import source_selection as ss
    monkeypatch.setattr(ss.editorial,'CONTENT_POLICY','reference_v1')
    cues=[dict(start=10,end=40,text='医药行业的需求随着老龄化增长。'),
          dict(start=40,end=65,text='但是投资仍有风险，不能只看过去。'),
          dict(start=73,end=100,text='科技行业要看企业的经营情况。')]
    cards=[dict(next_cue=0,question=dict(text='您怎么看医药行业？')),
           dict(next_cue=2,question=None)]
    before=[dict(c) for c in cues]
    picks=C.chapter_ranges(cues,cards)
    assert [(p['start'],p['end']) for p in picks]==[(0,1)]
    assert cues==before and picks[0]['editorial_approved'] is False
    assert '您怎么看' not in ''.join(c['text'] for c in cues)


def test_card_cannot_make_an_unanswered_why_complete(monkeypatch):
    import source_selection as ss
    monkeypatch.setattr(ss.editorial,'CONTENT_POLICY','reference_v1')
    cues=[dict(start=10,end=45,text='企业经营需要确定性，为什么？')]
    assert C.chapter_ranges(cues,[dict(next_cue=0,question=dict(text='您怎么看投资？'))])==[]
