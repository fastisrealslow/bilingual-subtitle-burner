"""Real publication evidence, linguistic variants, and complete counterexamples."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import editorial_policy as editorial
import source_selection as selection


@pytest.mark.parametrize('fixture,reason', [
    ('linyuan_published_personal_history_tail.json', '铺垫'),
    ('linyuan_published_bare_negation_tail.json', '残句'),
])
def test_actual_publication_boundaries_are_rechecked_without_changing_evidence(fixture, reason):
    data = json.loads((Path(__file__).parent / 'fixtures' / fixture).read_text())
    cues = data['cues']
    before = json.dumps(cues, ensure_ascii=False)
    assert reason in selection.boundary_error(cues, dict(start=0, end=len(cues)-1))
    assert json.dumps(cues, ensure_ascii=False) == before
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(editorial, 'CONTENT_POLICY', 'reference_v1')
        mp.setattr(editorial, 'MIN_SECONDS', 20)
        picks = selection.select(cues, whole_source=True, limit=None)
    if reason == '铺垫':
        assert picks
    else:
        # The prior complete sentence ends before the unchanged 20s floor.
        # Keep the refusal rather than pad it with the truncated example.
        assert not picks
    assert all(p['end'] < len(cues)-1 for p in picks)
    assert all(selection.boundary_error(cues, p) is None for p in picks)
    assert all(cues[p['end']]['end']-cues[p['start']]['start'] >= 20 for p in picks)
    assert json.dumps(cues, ensure_ascii=False) == before


@pytest.mark.parametrize('tail', [
    '那其实承接着刚才的话题，我知道您本人有一个特别的经历。',
    '那么我们听说你以前有过一个投资的故事。',
    '其实我了解您曾经有过类似的遭遇。',
])
def test_addressed_history_without_the_following_answer_is_not_a_conclusion(tail):
    cues = [dict(start=0, end=24, text='投资要研究企业的盈利能力。'),
            dict(start=24, end=30, text=tail)]
    assert '铺垫' in selection.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('text', [
    '那亏一次都是肯定是不。', '这个结果一定是不！', '它绝对是没。',
    '这个肯定是不', '最后要看它能不能。',
])
def test_incomplete_tail_is_rejected_even_if_a_model_or_cache_proposed_it(text):
    cues = [dict(start=0, end=24, text='投资要研究企业的盈利能力。'),
            dict(start=24, end=30, text=text)]
    assert '残句' in selection.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('text', [
    '错一次肯定不是方法。', '我们一定不会亏。', '绝对是没有价值。',
    '我肯定不。', '我一定不！',
    '最多是持有。', '我以前有一个投资的经历。',
    '我知道您的看法，企业仍然有竞争力。', '我知道中国以前有过这样的经历。',
    '他对我说：“我知道您有过这样的经历。”', '他说的答案肯定是“不”。',
])
def test_complete_negatives_self_accounts_and_quotes_remain_available(text):
    cues = [dict(start=0, end=24, text='投资要研究企业的盈利能力。'),
            dict(start=24, end=30, text=text)]
    assert selection.boundary_error(cues, dict(start=0, end=1)) is None


def test_addressed_history_with_a_complete_followup_answer_is_allowed():
    cues = [dict(start=0, end=24, text='投资要研究企业的盈利能力。'),
            dict(start=24, end=27, text='我知道您有过这样的经历。'),
            dict(start=27, end=30, text='您当时如何判断呢？'),
            dict(start=30, end=54, text='我先研究了企业的收入，确认需求后才买入。')]
    assert selection.boundary_error(cues, dict(start=0, end=3)) is None
