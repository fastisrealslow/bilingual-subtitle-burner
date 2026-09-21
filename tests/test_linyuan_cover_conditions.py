"""A real source100 pass lost its income condition on the approved cover."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T


TITLE = '林园：投资最坏结果十二三年回本，前提是人均收入不减少'
SOURCE = '十二三年你今天的投资能回本，这是基于人均收入比今天不减少。'


@pytest.mark.parametrize('cover', [
    '投资回本需十二三年', '只要坚持十二三年就能回本',
    '前提是坚持投资十二三年',
])
def test_dropped_or_replaced_condition_rejected_after_positive_model_review(cover):
    item = dict(title=TITLE, cover_title=cover, subject='人均收入', evidence=[SOURCE])
    verdict = dict(method='cpu_text_review', appeal=5,
                   reason='模拟本轮模型将条件丢失的封面误判为通过',
                   **{k: True for k in T.CHECKS})
    package = T._package(item, SOURCE, verdict, [item])
    assert '前提' in T._candidate_error(item, SOURCE, '林园', (), check_layout=False)
    assert '前提' in T.error(TITLE, package['title_rewrite'], SOURCE)


@pytest.mark.parametrize('cover', [
    '人均收入不减少，十二三年回本',
    '十二三年回本有什么前提？',
])
def test_complete_condition_or_honest_question_remains_reviewable(cover):
    item = dict(title=TITLE, cover_title=cover, subject='人均收入', evidence=[SOURCE])
    assert T._candidate_error(item, SOURCE, '林园', (), check_layout=False) is None


def test_trailing_condition_and_unrelated_question():
    assert T.cover_qualifier_error('林园：十二三年回本，但收入不降是前提', '投资多久才能回本？')
    assert not T.cover_qualifier_error('林园：十二三年回本，但收入不降是前提', '收入不降，十二三年回本')
    assert not T.cover_qualifier_error('林园：租客半年没交房租，我先让他腾房', '半年没交房租，我先让他腾房')


def test_exact_quote_path_cannot_bypass_cover_condition_check():
    quote = TITLE.split('：', 1)[1]
    item = dict(title=TITLE, cover_title='投资回本需十二三年', subject=quote, evidence=[quote])
    package = T._package(item, quote, dict(method='source_quote', quote=quote), [item])
    assert '前提' in T.error(TITLE, package['title_rewrite'], quote)
