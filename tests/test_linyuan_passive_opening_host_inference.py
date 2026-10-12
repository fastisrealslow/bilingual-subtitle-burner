import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as s

CASE = json.loads((Path(__file__).parent / 'fixtures' /
                   'linyuan_passive_opening_host_inference.json').read_text())


def test_actual_published_candidate_rejects_both_independent_boundary_defects(monkeypatch):
    cues = CASE['cues']
    before = copy.deepcopy(cues)
    monkeypatch.setattr(s.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(s.editorial, 'MIN_SECONDS', 20)
    assert '被动回指' in s.boundary_error(cues, dict(start=0, end=19))
    assert s.boundary_error(cues, dict(start=3, end=19))
    assert s.boundary_error(cues, dict(start=3, end=17)) is None
    assert s.select(cues, whole_source=True, limit=None) == []
    assert cues == before


@pytest.mark.parametrize('opening', [
    '然后后面也被很多科技超越，你如何看待它的走势呢？',
    '后来它又被其他公司超过，您怎么看它的股价？',
    '嗯，随后被新技术替代，您对它有什么看法？',
    '它被市场淘汰，你怎么看它的未来呢？',
])
def test_unbound_passive_questions_are_not_independent_openings(opening):
    cues = [dict(start=0, end=10, text=opening),
            dict(start=10, end=35, text='企业的需求和盈利能力才是判断的基础。')]
    assert '被动回指' in s.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('opening', [
    '后来茅台也被其他公司超过，您怎么看它的股价？',
    '这家公司被市场淘汰，你怎么看它的未来呢？',
    '你怎么看医药公司的风险？',
    '被动投资需要分散风险。',
    '被市场淘汰的企业，您怎么看？',
    '被风吹走的是旧包装。',
    '我说过：它被市场淘汰，要认真研究企业。',
])
def test_named_objects_and_complete_statements_remain_allowed(opening):
    cues = [dict(start=0, end=10, text=opening),
            dict(start=10, end=35, text='企业的需求和盈利能力才是判断的基础。')]
    assert s.boundary_error(cues, dict(start=0, end=1)) is None


@pytest.mark.parametrize('tail', [
    '那看得出来，就是王老师可能会比较看好这个行业嘛。',
    '可以看出，陈先生认为企业盈利最重要。',
    '听得出来，您还是支持长期持有。',
])
def test_observer_stance_recap_ends_before_host_narration(monkeypatch, tail):
    monkeypatch.setattr(s.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(s.editorial, 'MIN_SECONDS', 20)
    cues = [dict(start=0, end=5, text='您怎么看企业长期的发展？'),
            dict(start=5, end=35, text='企业必须回到生意的本质，盈利和需求都要看。'),
            dict(start=35, end=40, text=tail)]
    assert s.boundary_error(cues, dict(start=0, end=2))
    assert [(p['start'], p['end']) for p in s.select(cues, whole_source=True)] == [(0, 1)]


@pytest.mark.parametrize('tail', [
    '可以看出，这家公司有持续的需求。',
    '我认为这个行业有长期机会。',
    '王老师认为企业盈利重要，我也赞同。',
    '有人说：看得出来，王老师看好这个行业。',
    '听得出来，您还是支持长期持有，那么估值过高怎么办？',
])
def test_guest_conclusions_quotes_and_answered_followups_remain_allowed(tail):
    cues = [dict(start=0, end=5, text='您怎么看企业长期的发展？'),
            dict(start=5, end=35, text='企业必须回到生意的本质，盈利和需求都要看。'),
            dict(start=35, end=40, text=tail)]
    if tail.endswith('？'):
        cues.append(dict(start=40, end=60, text='估值过高就等待合理价格，不能只看需求。'))
    assert s.boundary_error(cues, dict(start=0, end=len(cues)-1)) is None


def test_real_cues_with_synthetic_complete_question_trim_only_host_sentence(monkeypatch):
    monkeypatch.setattr(s.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(s.editorial, 'MIN_SECONDS', 20)
    cues = copy.deepcopy(CASE['cues'])
    # A labelled variant, not a rewritten production transcript or approval.
    cues[0]['text'] = '茅台现在的股价，您是如何看'
    picks = s.select(cues, whole_source=True, limit=None)
    assert [(p['start'], p['end']) for p in picks] == [(0, 17)]
    assert cues[17]['end'] == CASE['cues'][17]['end']
    assert s.boundary_error(cues, picks[0]) is None
