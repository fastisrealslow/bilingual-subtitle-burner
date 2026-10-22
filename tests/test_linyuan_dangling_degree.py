"""Actual published ASR full stops cannot make a bare modifier complete."""
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as production
import source_selection as selection


def published_case():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_published_dangling_degree.json').read_text())


def test_actual_published_tail_is_rejected_without_dropping_its_condition(monkeypatch):
    case = published_case()
    cues = case['cues']
    before = json.dumps(cues, ensure_ascii=False)
    assert hashlib.sha256(''.join(c['text'] for c in cues).encode()).hexdigest() == \
        case['provenance']['transcript_sha256']
    assert case['model_review']['natural_ending'] is True
    assert selection.boundary_error(cues, dict(start=0, end=len(cues)-1))
    monkeypatch.setattr(selection.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(selection.editorial, 'MIN_SECONDS', 20)
    # Do not manufacture a shorter version by deleting the incomplete condition.
    assert selection.select(cues, whole_source=True, limit=None) == []
    assert json.dumps(cues, ensure_ascii=False) == before


@pytest.mark.parametrize('tail', [
    '如果只看未来十年，最。', '但企业的竞争力，更加！',
    '我们看经营数据，较为。', '考虑需求变化；越来越。',
    '特别。', '企业未来的竞争力，比较', '企业未来的竞争力，\n最为。',
])
def test_bare_degree_clause_variants_are_rejected(tail):
    cues = [dict(start=0, end=25, text='行业需求长期存在，投资仍要看企业竞争力。'),
            dict(start=25, end=30, text=tail)]
    assert selection.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('tail', [
    '如果只看未来十年，最重要的是需求。', '企业的竞争力会更强。',
    '经营数据，比较稳定。', '考虑需求变化，越来越清楚。',
    '这个行业十分特别。', '这是世界之最。', '两家企业需要比较。',
    '政策已经发生变更。', '他强调的是“最”。', '最重要的是需求。',
])
def test_complete_comparisons_predicates_and_quoted_words_stay_allowed(tail):
    cues = [dict(start=0, end=25, text='行业需求长期存在，投资仍要看企业竞争力。'),
            dict(start=25, end=30, text=tail)]
    assert selection.boundary_error(cues, dict(start=0, end=1)) is None


def test_interior_pause_with_its_complement_retained_is_allowed():
    cues = [dict(start=0, end=25, text='长期需求是投资的基础。'),
            dict(start=25, end=27, text='在有限时间里，最。'),
            dict(start=27, end=32, text='重要的是企业的竞争力。')]
    assert selection.boundary_error(cues, dict(start=0, end=2)) is None


def test_model_and_same_identity_cache_cannot_bypass_new_boundary(tmp_path, monkeypatch):
    cues = published_case()['cues']
    monkeypatch.setattr(selection.editorial, 'MIN_SECONDS', 20)
    pick = dict(start=0, end=len(cues)-1, score=9, reason='model verdict')
    monkeypatch.delenv('SOURCE_EDITORIAL_FIRST', raising=False)
    identity = dict(editorial=production.editorial.plan_identity(cues, production.TARGET_SEC),
                    selector_version=selection.VERSION, source_first=False)
    (tmp_path / 'highlights.json').write_text(json.dumps(dict(identity=identity, picks=[pick])))
    bad = json.dumps(dict(picks=[pick]))
    with patch.object(production, 'llm', side_effect=[bad, '{"picks":[]}']) as model, \
            patch.object(production, 'pick_argument_context', return_value=[]):
        assert production.pick_highlights(cues, '林园', '', tmp_path, allow_empty=True) == []
        assert model.call_count == 2


def test_argument_review_rejects_before_asking_model(tmp_path, monkeypatch):
    cues = published_case()['cues']
    monkeypatch.setattr(production, 'automatic_only', lambda: True)
    with patch.object(production, 'llm', side_effect=AssertionError('must reject before model')):
        with pytest.raises(production.VisualQualityError, match='程度词'):
            production.review_complete_argument(cues, [dict(start=0, end=len(cues)-1)],
                                                '林园', '', tmp_path, '')
