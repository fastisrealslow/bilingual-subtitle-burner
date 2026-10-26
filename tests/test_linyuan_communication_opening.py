"""Keep incomplete communication clauses out of automatic clip openings."""
import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p
import source_selection as s


def published_case():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_incomplete_communication_opening.json').read_text())


@pytest.fixture(autouse=True)
def reference_policy(monkeypatch):
    monkeypatch.setattr(s.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(s.editorial, 'MIN_SECONDS', 20)


def test_actual_published_opening_is_not_repaired_by_later_complete_sentences():
    case = published_case()
    cues = case['cues']
    original = json.dumps(cues, ensure_ascii=False)
    text = ''.join(c['text'] for c in cues)
    assert hashlib.sha256(text.encode()).hexdigest() == case['evidence']['raw_text_sha256']
    assert case['evidence']['model_standalone_opening'] is True
    assert s.incomplete_communication_opening(text)
    assert not s.speech_opening(text)
    assert s.boundary_error(cues, dict(start=0, end=len(cues)-1))
    assert not any(r['start'] == 0 for r in s.select(cues, whole_source=True, limit=None))
    assert json.dumps(cues, ensure_ascii=False) == original


@pytest.mark.parametrize('opening', [
    '把这个信息啊告诉，那么从去年开始我们组织了很多活动。',
    '先把这条消息转告，然后我们再研究市场的变化。',
    '我们想把这些观点传达，另外公司还有一些投资安排。',
    '那，把那个情况告知。',
    '把这件事情转达啊，接下来谈企业的需求。',
    '把这些想法告诉。',
])
def test_dangling_demonstrative_transfer_clause_variants(opening):
    cues = [dict(start=0, end=8, text=opening),
            dict(start=8, end=40, text='投资要看企业真实需求，不能忽略价格风险。')]
    assert s.incomplete_communication_opening(opening)
    assert s.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('opening', [
    '把这个信息告诉大家：投资要关注企业真实需求。',
    '先把这些想法传达给投资者，不能忽略价格风险。',
    '把这个问题说明白，企业需求才是关键。',
    '把这条消息转告给大家。',
    '把这个信息告诉大家后，我们再研究市场的变化。',
    '把企业的风险告诉大家，投资不能只看收益。',
    '我说：“把这个信息告诉”，他就继续讲了。',
    '比如把这个信息告诉，句子还没有说完。',
    '不要把这个消息告诉错误的人。',
    '市场需求正在变化，把这个信息告诉大家。',
    '把这个信息告诉：市场需求正在变化。',
    '公司要告诉大家，投资仍然有风险。',
])
def test_explicit_recipient_content_quotation_and_retained_premise_are_not_flagged(opening):
    assert not s.incomplete_communication_opening(opening)
    cues = [dict(start=0, end=12, text=opening),
            dict(start=12, end=40, text='投资要看企业真实需求，不能忽略价格风险。')]
    assert s.boundary_error(cues, dict(start=0, end=1)) is None


def test_split_asr_rows_do_not_hide_the_incomplete_opening():
    cues = [dict(start=0, end=1, text='把这'),
            dict(start=1, end=3, text='个信息啊告'),
            dict(start=3, end=7, text='诉，那么投资需要看企业需求。'),
            dict(start=7, end=40, text='市场价格和需求都要研究，不能忽略风险。')]
    assert s.boundary_error(cues, dict(start=0, end=3))
    # A premise retained before a later communication clause is not an opening.
    cues.insert(0, dict(start=-10, end=0, text='企业需求正在减少，市场价格仍然有风险。'))
    assert s.boundary_error(cues, dict(start=0, end=4)) is None


@pytest.mark.parametrize('cache_version', [44, 53])
def test_old_and_current_identity_caches_cannot_restore_bad_opening(tmp_path, monkeypatch, cache_version):
    cues = published_case()['cues']
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST', 'true')
    identity = dict(editorial=p.editorial.plan_identity(cues, p.TARGET_SEC),
                    selector_version=cache_version, source_first=True)
    cache = tmp_path / 'highlights.json'
    cache.write_text(json.dumps(dict(identity=identity,
        picks=[dict(start=0, end=len(cues)-1, score=9)])))
    with patch.object(s, 'select', return_value=[]) as select, \
            patch.object(p, 'llm', side_effect=AssertionError('No model needed')):
        assert p.pick_highlights(cues, '林园', '', tmp_path) == []
    select.assert_called_once()
    assert json.loads(cache.read_text())['picks'] == []


def test_model_candidate_and_final_automatic_review_share_the_guard(tmp_path, monkeypatch):
    cues = published_case()['cues']
    pick = dict(start=0, end=len(cues)-1, score=9, reason='model says complete')
    monkeypatch.delenv('SOURCE_EDITORIAL_FIRST', raising=False)
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY', 'true')
    with patch.object(p, 'llm', return_value=json.dumps(dict(picks=[pick]))), \
            patch.object(p, 'pick_argument_context', return_value=[]):
        assert p.pick_highlights(cues, '林园', '', tmp_path) == []
    with patch.object(p, 'llm', side_effect=AssertionError('Must fail before model')):
        with pytest.raises(p.VisualQualityError, match='残缺传达句'):
            p.review_complete_argument(cues, [pick], '林园', '', tmp_path, '')
