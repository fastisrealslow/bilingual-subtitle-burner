"""Both independent models turned source13's hypothetical into a fact."""
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import title_rewrite as T


def source13():
    rows = json.loads((ROOT / 'linyuan/simulations/benchmark-20260921/all-current-title-corpus.json').read_text())
    return ''.join(c['text'] for r in rows if r['source_id'] == 13 for c in r['cues'])


@pytest.mark.parametrize('title,cover', [
    ('林园：中石油全世界就这一家，所以没买', '中石油全世界就这一家，所以没买'),
    ('林园：中石油只有一家却未买', '中石油为何不买入'),
    ('林园：中石油为什么没买', '中石油全球唯一，为何没买？'),
])
def test_actual_model_approvals_cannot_override_hypothetical(title, cover):
    source = source13()
    item = dict(title=title, cover_title=cover, subject='石油',
                evidence=['没买，因为我我觉得它不符合我的标准。',
                          '按我们的说法，如果是到石油，就它中石油全世界就这一家，它别的再没有了。'])
    proof = T._package(item, source, dict(method='cpu_text_review', appeal=5,
        reason='旧模型把假设当作事实，全部自评通过', **{k: True for k in T.CHECKS}), [])['title_rewrite']
    assert '假设' in T.error(title, proof, source)
    assert '假设' in T._candidate_error(item, source, '林园', [], check_layout=False)


def test_safe_angle_and_explicit_hypothesis_remain_available():
    assert T.hypothetical_exclusivity_error('林园：没买中石油，不符合我的标准', '没买中石油', source13()) is None
    assert T.hypothetical_exclusivity_error('林园：如果中石油全世界只有一家', '如果中石油全球唯一', source13()) is None


def test_actual_exclusivity_or_different_subject_is_not_rejected():
    assert T.hypothetical_exclusivity_error('林园：中石油全球唯一', '中石油全球唯一', '中石油全世界就这一家。') is None
    assert T.hypothetical_exclusivity_error('林园：可口可乐全球唯一', '可口可乐全世界就这一家', source13()) is None
