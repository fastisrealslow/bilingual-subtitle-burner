"""Literal hypothetical premises must not become unconditional headlines."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite as T


def test_real_published_file_failed_both_generation_and_cached_proof_gates():
    case = json.loads((ROOT / 'tests/fixtures/linyuan_published_hypothesis.json').read_text())
    proof = case['proof']
    assert all(proof['review'][key] is True for key in T.CHECKS)
    assert '假设' in T.error(case['title'], proof, case['transcript'])
    item = dict(title=case['title'], cover_title=proof['cover'],
                subject=proof['subject'], evidence=proof['evidence'])
    assert '假设' in T._candidate_error(item, case['transcript'], '林园', (), False)


@pytest.mark.parametrize('marker', ['如果', '假如', '假设', '要是'])
@pytest.mark.parametrize('claim', ['整个行业需求持续增长', '这家企业现金流很充足', '市场价格一直往上涨'])
def test_same_failure_across_topics_and_condition_markers(marker, claim):
    source = f'{marker}说这个呃{claim}，我会重新评估。'
    assert T.hypothetical_clause_error('林园：' + claim, claim, source)
    assert T.hypothetical_clause_error('林园：如果' + claim, claim, source)
    assert T.hypothetical_clause_error('林园：' + claim, '如果' + claim, source)
    assert not T.hypothetical_clause_error('林园：如果' + claim, '如果' + claim, source)


@pytest.mark.parametrize('source,title,cover', [
    ('如果市场价格一直往上涨，我会评估。', '林园：企业现金流很充足', '企业现金流很充足'),
    ('市场价格一直往上涨。', '林园：市场价格一直往上涨', '市场价格一直往上涨'),
    ('如果市场价格一直往上涨，我会评估。市场价格一直往上涨。',
     '林园：市场价格一直往上涨', '市场价格一直往上涨'),
    ('如果市场价格一直往上涨，我会评估。', '林园：如果市场价格一直往上涨，我会评估', '如果市场价格一直往上涨'),
    ('如果市场价格一直往上涨，我会评估。', '林园：市场价格一直往上涨怎么办？', '价格上涨怎么办？'),
    ('如果价格涨，我会评估。', '林园：市场价格一直往上涨', '市场价格一直往上涨'),
])
def test_independent_assertions_other_topics_and_conditional_questions(source, title, cover):
    assert not T.hypothetical_clause_error(title, cover, source)


def test_unrelated_condition_does_not_launder_unconditional_second_clause():
    source = '如果市场价格一直往上涨，我会评估。'
    assert T.hypothetical_clause_error('林园：如果产能下降，市场价格一直往上涨', '市场价格一直往上涨', source)
