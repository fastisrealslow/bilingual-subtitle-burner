import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as s


def test_actual_published_recap_is_not_a_guest_ending(monkeypatch):
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_addressed_recap.json').read_text())
    cues=data['cues'];before=json.dumps(cues,ensure_ascii=False)
    assert s.boundary_error(cues,dict(start=0,end=len(cues)-1))
    units=s.sentence_units(cues)
    assert {7,8} <= s.host_bridge_indices(units)
    monkeypatch.setattr(s.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(s.editorial,'MIN_SECONDS',20)
    picks=s.select(cues,whole_source=True,limit=None)
    assert picks
    assert all(p['end'] < units[7]['start'] for p in picks)
    assert json.dumps(cues,ensure_ascii=False)==before


@pytest.mark.parametrize('text',[
    '您觉得现在是牛市的初期，那要做的就是大胆的买入并持有。',
    '您认为行业仍在调整，所以我们就应该保持耐心。',
    '那么您觉得需求还没有恢复，那么就需要继续观察。',
    '您的意思是企业还在成长，那就应该关注利润。',
])
def test_addressed_inference_variants(text):
    assert s.host_bridge_indices([{'text':text}])=={0}


@pytest.mark.parametrize('text',[
    '我觉得现在是牛市的初期，那要做的就是保持耐心。',
    '您觉得现在是牛市的初期，那要做的就是买入吗？',
    '比如有人说您认为行业还在调整，所以我们就应该等待。',
    '您认为行业还在调整。',
    '第一，我认为企业盈利最重要。',
    '那要做的就是了解企业。',
])
def test_normal_statements_quotes_and_questions(text):
    assert not s.host_bridge_indices([{'text':text}])


def test_unrelated_previous_sentence_is_not_removed():
    units=[{'text':'我们要关注企业的盈利。'},
           {'text':'您认为行业仍在调整，所以我们就应该保持耐心。'}]
    assert s.host_bridge_indices(units)=={1}


def test_answered_followup_remains_valid():
    texts=['我们要关注企业。','您认为行业还在调整，那就应该等待吗？','是的，要耐心等待盈利恢复。']
    cues=[dict(start=i*20,end=(i+1)*20,text=t) for i,t in enumerate(texts)]
    assert s.boundary_error(cues,dict(start=0,end=2)) is None
