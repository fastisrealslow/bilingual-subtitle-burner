import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_selection as s


def test_actual_shareholder_statement_does_not_include_board_reply(monkeypatch):
    case=json.loads((Path(__file__).parent/'fixtures/linyuan_source67_floor_handover.json').read_text())
    cues=case['cues'];before=json.dumps(cues,ensure_ascii=False)
    monkeypatch.setattr(s.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(s.editorial,'MIN_SECONDS',20)
    picks=s.select(cues,whole_source=True,limit=None)
    recovered=[p for p in picks if p.get('selection_method')=='source_floor_handover_v1']
    assert [(p['start'],p['end']) for p in recovered]==[(4,76)]
    assert cues[4]['start']==14 and cues[76]['end']==242.44
    text=''.join(c['text'] for c in cues[4:77])
    assert '百分之八十以上' in text and '别去理财了' in text
    assert '谢谢林' not in text and '我们肯定会把酒做好' not in text
    assert json.dumps(cues,ensure_ascii=False)==before
    assert not s.select(cues,whole_source=False)


def test_missing_invitation_or_acknowledgement_cannot_close_a_turn():
    cues=[dict(start=0,end=2,text='有请。'),dict(start=2,end=120,text='分红比例应该提高。'),
          dict(start=120,end=130,text='谢谢林总，我们请下一位。')]
    assert s.floor_handover_ranges(cues)==[]
    cues.insert(0,dict(start=0,end=0,text='把话筒交给你。'))
    assert s.floor_handover_ranges(cues)==[(2,2)]
    assert s.floor_handover_ranges(cues[:-1])==[]
