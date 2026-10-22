"""Missing stance objects must survive neither model nor cached approval."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as S
import produce_cn as P


def real_case():
    return json.loads((Path(__file__).parent / 'fixtures' /
                      'linyuan_bare_stance_change_opening.json').read_text())


def test_published_raw_opening_is_rejected_without_changing_evidence():
    case = real_case()
    original = json.dumps(case['cues'], ensure_ascii=False)
    assert '缺少对象' in S.boundary_error(case['cues'], case['pick'])
    assert json.dumps(case['cues'], ensure_ascii=False) == original


@pytest.mark.parametrize('opening', [
    '现在您的想法变了吗？', '您现在的观点是否改变了？',
    '那么，你的看法有没有变化？', '目前您的判断调整了吗？',
    '你们的态度发生变化了吗？', '您的想法变了没有？',
])
def test_bare_stance_change_variants_require_original_context(opening):
    cues = [dict(start=0, end=3, text=opening),
            dict(start=3, end=30, text='券商不是主攻方向，配置比例还要考虑竞争。')]
    assert '缺少对象' in S.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('opening', [
    '现在您对券商的想法变了吗？',
    '您此前认为券商竞争太多，现在您的想法变了吗？',
    '我们刚才讨论券商的竞争。现在您的想法变了吗？',
    '有人问我：“现在您的想法变了吗？”我的回答是投资要看企业。',
    '现在我的想法变了，投资要看企业竞争。',
    '您认为券商行业发生变化了吗？',
])
def test_named_objects_retained_premises_and_guest_examples_are_allowed(opening):
    cues = [dict(start=0, end=8, text=opening),
            dict(start=8, end=30, text='券商不是主攻方向，配置比例还要考虑竞争。')]
    assert S.boundary_error(cues, dict(start=0, end=1)) is None


def test_source_candidates_and_automatic_model_review_recheck_real_opening(monkeypatch, tmp_path):
    monkeypatch.setattr(S.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(S.editorial, 'MIN_SECONDS', 20)
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY', 'true')
    case = real_case()
    assert all(p['start'] != 0 for p in S.select(case['cues'], whole_source=True, limit=None))
    with patch.object(P, 'automatic_only', return_value=True), \
         patch.object(P, 'llm', side_effect=AssertionError('invalid boundary must fail before model')):
        with pytest.raises(P.VisualQualityError, match='缺少对象'):
            P.review_complete_argument(case['cues'], [case['pick']], '林园', '', tmp_path, '')


def test_current_version_cache_cannot_skip_boundary_recheck(monkeypatch, tmp_path):
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST', 'true')
    monkeypatch.setattr(S.editorial, 'MIN_SECONDS', 20)
    case = real_case()
    identity = dict(editorial=P.editorial.plan_identity(case['cues'], P.TARGET_SEC),
                    selector_version=S.VERSION, source_first=True)
    cache = tmp_path / 'highlights.json'
    cache.write_text(json.dumps(dict(identity=identity, picks=[case['pick']])))
    with patch.object(S, 'select', return_value=[]) as select:
        assert P.pick_highlights(case['cues'], '林园', '', tmp_path) == []
        select.assert_called_once()
    assert json.loads(cache.read_text())['picks'] == []
