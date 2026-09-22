from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_question_cards as C


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
