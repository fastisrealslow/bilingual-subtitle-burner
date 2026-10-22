"""A bare agreement cannot replace its omitted terms."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as selection
import produce_cn as production


def actual_cues():
    path = Path(__file__).parent / 'fixtures/linyuan_bare_agreement_opening.json'
    return json.loads(path.read_text())['cues']


def test_real_published_opening_is_rejected_without_editing_raw_source(monkeypatch):
    monkeypatch.setattr(selection.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(selection.editorial, 'MIN_SECONDS', 20)
    cues = actual_cues()
    before = json.dumps(cues, ensure_ascii=False)
    assert '具体内容' in selection.boundary_error(cues, dict(start=0, end=len(cues)-1))
    assert selection.select(cues, whole_source=True, limit=None) == []
    assert json.dumps(cues, ensure_ascii=False) == before


@pytest.mark.parametrize('question', [
    '您是怎么看这个赌约的呢？',
    '那么，您如何评价那场争论呢？',
    '你怎么看这个承诺？',
    '嗯，您对那个约定是怎么看的呢？',
    '请问林总，您对这项承诺有什么看法？',
    '您怎么看当时的那个赌约？',
    '你对这份约定有哪些意见？',
])
def test_same_class_of_missing_terms_is_rejected(question):
    cues = [dict(start=0, end=4, text=question),
            dict(start=4, end=40, text='这是常识，优秀的企业有竞争优势。')]
    assert '具体内容' in selection.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('question', [
    '您怎么看两家企业约定以十年收益定输赢的赌约？',
    '您如何评价公司承诺三年不涨价？',
    '你对这项利润翻倍的承诺有什么看法？',
    '您怎么看这个赌约中比较收益的方法？',
    '有人问我：您是怎么看这个赌约的呢？',
    '您怎么看企业的财务指标？',
])
def test_explicit_terms_and_quotes_keep_normal_review(question):
    cues = [dict(start=0, end=4, text=question),
            dict(start=4, end=40, text='需求和价格都要研究，不能只凭情绪判断。')]
    assert selection.boundary_error(cues, dict(start=0, end=1)) is None


def test_retained_terms_are_not_rejected_but_dropped_terms_are():
    cues = [dict(start=0, end=5, text='两家企业约定比较未来十年的投资收益。'),
            dict(start=5, end=8, text='您是怎么看这个赌约的呢？'),
            dict(start=8, end=40, text='这需要看企业的长期盈利能力。')]
    assert selection.boundary_error(cues, dict(start=0, end=2)) is None
    assert '具体内容' in selection.boundary_error(cues, dict(start=1, end=2))


def test_question_split_across_original_cues_still_needs_terms():
    cues = [dict(start=0, end=2, text='那，您对这个'),
            dict(start=2, end=5, text='承诺有什么看法呢？'),
            dict(start=5, end=40, text='这需要看企业的长期盈利能力。')]
    assert '具体内容' in selection.boundary_error(cues, dict(start=0, end=2))


def test_old_model_approval_cannot_bypass_opening_guard(monkeypatch, tmp_path):
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY', 'true')
    cues = actual_cues()
    (tmp_path / 'editorial_review.json').write_text(json.dumps(dict(
        standalone_opening=True, complete_argument=True,
        reasoning_present=True, natural_ending=True, requires_audio_review=False)))
    monkeypatch.setattr(production, 'llm', lambda *a, **k:
                        pytest.fail('missing context reached model inference'))
    with pytest.raises(production.VisualQualityError, match='具体内容'):
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
