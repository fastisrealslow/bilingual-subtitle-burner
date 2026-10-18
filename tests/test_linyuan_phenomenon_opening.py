"""A demonstrative relation cannot replace its omitted participants."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as selection
import produce_cn as production


def actual_cues():
    path = Path(__file__).parent / 'fixtures/linyuan_bare_phenomenon_opening.json'
    return json.loads(path.read_text())['cues']


def test_real_published_opening_is_rejected_without_editing_raw_source(monkeypatch):
    monkeypatch.setattr(selection.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(selection.editorial, 'MIN_SECONDS', 20)
    cues = actual_cues()
    before = json.dumps(cues, ensure_ascii=False)
    assert '具体对象' in selection.boundary_error(cues, dict(start=0, end=len(cues)-1))
    assert selection.select(cues, whole_source=True, limit=None) == []
    assert json.dumps(cues, ensure_ascii=False) == before


@pytest.mark.parametrize('question', [
    '您是怎么看待这个错配的现象的呢？',
    '那，您如何评价这种倒挂现象呢？',
    '你怎么看这个背离的现象？',
    '您怎么看这一现象？',
    '嗯，您是如何看待这样的情况的呢？',
    '您怎么看那种分化现象吗？',
])
def test_same_class_of_missing_participants_is_rejected(question):
    cues = [dict(start=0, end=4, text=question),
            dict(start=4, end=40, text='券商本身是好行业，但要看价格和风险。')]
    assert selection.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('question', [
    '您怎么看券商估值与利润错配的现象？',
    '您怎么看待这种乳制品行业竞争加剧的现象呢？',
    '您怎么看这种债券利率倒挂的情况？',
    '您是怎么看待券商的呢？',
])
def test_named_objects_are_not_bare_demonstratives(question):
    cues = [dict(start=0, end=4, text=question),
            dict(start=4, end=40, text='需求和价格都要研究，不能只凭情绪判断。')]
    assert selection.boundary_error(cues, dict(start=0, end=1)) is None


def test_retained_premise_and_guest_quotation_remain_eligible():
    for opening in ['券商盈利向上，但估值持续下降。',
                    '有人问我：您是怎么看待这个错配的现象的呢？']:
        cues = [dict(start=0, end=5, text=opening),
                dict(start=5, end=8, text='您是怎么看待这个错配的现象的呢？'),
                dict(start=8, end=40, text='钱流向了其他行业，不能由此否定券商。')]
        assert selection.boundary_error(cues, dict(start=0, end=2)) is None


def test_old_model_approval_cannot_bypass_opening_guard(monkeypatch, tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY', 'true')
    cues = actual_cues()
    (tmp_path / 'editorial_review.json').write_text(json.dumps(dict(
        standalone_opening=True, complete_argument=True,
        reasoning_present=True, natural_ending=True, requires_audio_review=False)))
    monkeypatch.setattr(production, 'llm', lambda *a, **k:
                        pytest.fail('missing context reached model inference'))
    with pytest.raises(production.VisualQualityError, match='具体对象'):
        production.review_complete_argument(cues,
            [dict(start=0, end=len(cues)-1)], '林园', '', tmp_path, '')


def test_cached_selection_is_checked_even_with_current_identity(monkeypatch, tmp_path):
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST', 'true')
    cues = actual_cues()
    identity = dict(editorial=production.editorial.plan_identity(cues, production.TARGET_SEC),
                    selector_version=selection.VERSION, source_first=True)
    (tmp_path / 'highlights.json').write_text(json.dumps(dict(identity=identity,
        picks=[dict(start=0, end=len(cues)-1)])))
    monkeypatch.setattr(production, 'llm', lambda *a, **k:
                        pytest.fail('invalid cache reached model inference'))
    assert production.pick_highlights(cues, '林园', '', tmp_path) == []
