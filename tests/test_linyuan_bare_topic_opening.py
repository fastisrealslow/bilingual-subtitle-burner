"""Keep an omitted topic out of automatic, model, and cached selections."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import editorial_policy as editorial
import produce_cn as production
import source_selection as selection


def actual():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_published_missing_topic_opening.json').read_text())


def test_actual_opening_is_rejected_despite_the_saved_model_approval(monkeypatch):
    data = actual()
    cues = data['cues']
    before = json.dumps(cues, ensure_ascii=False)
    assert data['original_editorial_review']['standalone_opening'] is True
    assert data['before_boundary_error'] is None
    assert '缺少实际对象' in selection.boundary_error(cues, dict(start=0, end=len(cues)-1))
    monkeypatch.setattr(editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(editorial, 'MIN_SECONDS', 20)
    assert selection.select(cues, whole_source=True, limit=None) == []
    assert json.dumps(cues, ensure_ascii=False) == before


@pytest.mark.parametrize('question', [
    '您怎么看待这个事情？', '那，你是如何评价那个问题的呢？',
    '嗯，您怎么看这件事？', '你如何看待那件事情呢？',
])
def test_other_bare_demonstrative_questions_are_not_independent_openings(question):
    cues = [dict(start=0, end=3, text=question),
            dict(start=3, end=30, text='经销商可以为企业提供服务。')]
    assert '缺少实际对象' in selection.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('question', [
    '你怎么看待经销商改革这件事情？', '您怎么看待茅台经销商的变化？',
    '您怎么看待这个行业的发展？',
    '我问过他：“你怎么看待这件事？”',
])
def test_named_topics_and_guest_quotations_remain_available(question):
    cues = [dict(start=0, end=3, text=question),
            dict(start=3, end=30, text='经销商可以为企业提供服务。')]
    assert selection.boundary_error(cues, dict(start=0, end=1)) is None


def test_retained_explicit_premise_supplies_the_object_without_rewriting():
    cues = [dict(start=0, end=4, text='茅台正在改革经销商的服务方式。'),
            dict(start=4, end=7, text='您怎么看待这件事情？'),
            dict(start=7, end=30, text='经销商可以为企业提供服务。')]
    assert selection.boundary_error(cues, dict(start=0, end=2)) is None
    assert '缺少实际对象' in selection.boundary_error(cues, dict(start=1, end=2))


@pytest.mark.parametrize('source_first', [True, False])
@pytest.mark.parametrize('cache_version', [None, 41, 45])
def test_cached_or_model_approval_cannot_reintroduce_actual_bad_opening(
        tmp_path, monkeypatch, source_first, cache_version):
    cues = actual()['cues']
    monkeypatch.setattr(editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(editorial, 'MIN_SECONDS', 20)
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST', 'true' if source_first else 'false')
    pick = dict(start=0, end=len(cues)-1, score=9, reason='model candidate')
    if cache_version is not None:
        identity = dict(editorial=editorial.plan_identity(cues, production.TARGET_SEC),
                        selector_version=cache_version, source_first=source_first)
        (tmp_path / 'highlights.json').write_text(json.dumps(dict(identity=identity, picks=[pick])))
    responses = [json.dumps(dict(picks=[pick])), '{"picks":[]}']
    with patch.object(production, 'llm', side_effect=responses) as model, \
            patch.object(production, 'pick_argument_context', return_value=[]):
        assert production.pick_highlights(cues, '林园', '', tmp_path, allow_empty=True) == []
        assert model.call_count == (0 if source_first else 2)

