"""Published raw failure, related reactions, and ordinary guest counterexamples."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_selection as S


def cues(texts):
    return [dict(start=i*12,end=(i+1)*12,text=t) for i,t in enumerate(texts)]


def test_published_reaction_is_removed_at_original_answer_boundary(monkeypatch):
    record=json.loads((Path(__file__).parent/'fixtures/linyuan_host_reaction_boundary.json').read_text())
    rows=record['cues'];original=deepcopy(rows)
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    assert '集体感想' in S.boundary_error(rows,dict(start=0,end=len(rows)-1))
    picks=S.select(rows,limit=None,whole_source=True)
    assert picks
    assert all(p['end']<16 for p in picks)
    assert any(p['end']==15 for p in picks)
    assert rows==original


@pytest.mark.parametrize('ack,reaction',[
    ('嗯嗯嗯，明白，明白。','好，那我们对对对这个A股这波行情也是有更多的一个信心了。'),
    ('了解了。','那么咱们对企业经营有了更深的认识。'),
    ('啊，明白了。','好的，那我们对未来的行业发展更有信心了。'),
])
def test_listener_reaction_variants(ack,reaction):
    rows=cues(['您如何看待公司的经营？','我看重的是长期需求，这是研究公司的基础。',ack,reaction])
    assert S.listener_reaction_indices(S.sentence_units(rows))==[2]
    assert S.boundary_error(rows,dict(start=0,end=3))
    assert S.boundary_error(rows,dict(start=0,end=1)) is None


@pytest.mark.parametrize('texts',[
    ['我对公司的长期经营有更多的信心。','因为它的需求和盈利能力都在改善。'],
    ['我们对这个行业更有信心了。','这是长期研究的结果。'],
    ['明白行业需求很重要。','那我们对企业有了更深的认识。'],
    ['明白。','我对公司的盈利有信心。'],
    ['明白。','那我们对企业有哪些不同的认识？','我看重持续需求。'],
    ['明白。','有人说：“那我们对市场有更多信心了。”','我不会这样判断。'],
    ['明白。','那我们对企业有更多的认识空间，要继续研究。'],
])
def test_ordinary_guest_statements_and_quoted_reactions_stay(texts):
    assert S.listener_reaction_indices(S.sentence_units(cues(texts)))==[]


def test_reaction_does_not_discard_a_later_complete_question(monkeypatch):
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    rows=cues(['您怎么看公司需求？','我看好长期需求，企业的经营能力很重要。',
        '明白。','那我们对企业有更多的认识了。',
        '您怎么看消费行业？','我看重消费行业长期的需求，这是我研究公司的依据。'])
    picks=S.select(rows,limit=None,whole_source=True)
    assert any(p['start']==4 and p['end']==5 for p in picks)
    assert all(not (p['start']<=2<=p['end']) for p in picks)
