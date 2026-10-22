"""Replay real published boundaries without inventing a replacement edit."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_selection as selector
import produce_cn as producer

DATA = json.loads((Path(__file__).parent / 'fixtures' /
                   'linyuan_published_boundary_regression.json').read_text())['rows']


@pytest.mark.parametrize('row', DATA, ids=['missing_numbered_premise', 'host_handoff'])
def test_actual_published_source_boundaries_fail_before_model_approval(row, tmp_path, monkeypatch):
    cues = row['cues']
    original = json.dumps(cues, ensure_ascii=False)
    pick = dict(start=0, end=len(cues)-1)
    assert selector.boundary_error(cues, pick)
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY', 'true')
    # A saved positive model verdict cannot override observable boundaries.
    (tmp_path/'editorial_review.json').write_text(json.dumps(row['original_editorial_review']))
    with patch.object(producer, 'llm', side_effect=AssertionError('boundary must run first')):
        with pytest.raises(producer.VisualQualityError, match='开场回指|结束语'):
            producer.review_complete_argument(cues, [pick], '林园', '', tmp_path, '')
    assert json.dumps(cues, ensure_ascii=False) == original


@pytest.mark.parametrize('opening', [
    '嗯，您刚才不是说给两个吗？', '你之前提到三条呢？',
    '刚刚您说了两点。', '那，前面你讲过四种吧？',
])
def test_bare_backward_counts_require_the_missing_premise(opening):
    cues = [dict(start=0, end=5, text=opening),
            dict(start=5, end=45, text='年轻人创业应该保留自己的兴趣，不必接受过多束缚。')]
    assert '开场回指' in selector.boundary_error(cues, dict(start=0, end=1))


@pytest.mark.parametrize('opening', [
    '您刚才说给年轻人两个建议，分别是什么？',
    '你之前提到三条投资原则，能解释一下吗？',
    '刚刚您说了两个医药行业的机会。',
    '我给年轻人两个建议。', '年轻人创业不要给自己太多束缚。',
])
def test_named_objects_and_independent_numbered_lists_remain_eligible(opening):
    cues = [dict(start=0, end=5, text=opening),
            dict(start=5, end=45, text='投资要看实际的需求和现金流，也要考虑价格与风险。')]
    assert selector.boundary_error(cues, dict(start=0, end=1)) is None


@pytest.mark.parametrize('ending', [
    '好，谢谢。那我们下一位，好，王先生。',
    '那么咱们有请下一位，李老师。', '好，下一位。',
])
def test_event_handoffs_cannot_extend_the_guest_answer(ending, monkeypatch):
    monkeypatch.setattr(selector.editorial, 'CONTENT_POLICY', 'reference_v1')
    monkeypatch.setattr(selector.editorial, 'MIN_SECONDS', 20)
    cues = [dict(start=0, end=5, text='您对医药行业有什么判断？'),
            dict(start=5, end=45, text='医药需求长期存在，但投资也要考虑企业利润和买入价格。'),
            dict(start=45, end=50, text=ending)]
    assert '结束语' in selector.boundary_error(cues, dict(start=0, end=2))
    assert selector.boundary_error(cues, dict(start=0, end=1)) is None
    assert all(p['end'] < 2 for p in selector.select(cues, whole_source=True, limit=None))


@pytest.mark.parametrize('text', [
    '我们下一位客户买了医药公司的产品。', '那我们下一个问题，医药行业怎么看？',
    '当时主持人说“那我们下一位，王先生”，我才起身。',
])
def test_customer_examples_reported_speech_and_followup_questions_are_not_handoffs(text):
    assert not selector.OUTRO.search(text)
