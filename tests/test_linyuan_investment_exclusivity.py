import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite


def test_published_copy_turns_selected_purchases_into_an_exclusive_rule():
    sample = json.loads((Path(__file__).parent / 'fixtures' /
                         'linyuan_published_investment_exclusivity.json').read_text())
    proof = sample['title_rewrite']
    candidate = dict(title=sample['title'], cover_title=sample['cover_title'],
                     subject=proof['subject'], evidence=proof['evidence'])
    assert '排他判断' in title_rewrite._candidate_error(
        candidate, sample['raw_text'], '林园', ())
    assert '排他判断' in title_rewrite.error(
        sample['title'], proof, sample['raw_text'])


@pytest.mark.parametrize('title,cover,source', [
    ('林园：只买白酒', '重点配置白酒', '我们重点配置白酒，也看其他消费品。'),
    ('林园：优先买医药', '仅买入医药', '比如买医药，我们也会比较其他行业。'),
    ('林园：仅配置龙头公司', '', '配置一部分龙头公司，有些资金也会买债券。'),
    ('林园：只投资银行股', '', '我只投消费股。他们主要买银行股。'),
])
def test_scope_is_checked_per_action_object_and_per_surface(title, cover, source):
    assert title_rewrite.forecast_copy_error(title, cover, source)


@pytest.mark.parametrize('title,cover,source', [
    ('林园：重点配置白酒', '也看其他消费品', '我们主要配置白酒，也看其他消费品。'),
    ('林园：只买白酒', '仅买入白酒', '我们主要讲选股原则，实际只投资白酒。'),
    ('林园：不只买白酒', '不只配置白酒', '我们优先买白酒，但也买食品。'),
    ('林园：只买了白酒', '', '我们原本优先配置消费，这笔只买了白酒。'),
    ('林园：仅买入了白酒', '只投资过白酒', '我们原本优先配置消费，这笔只买入了白酒。'),
    ('林园：只在熟悉的行业里运作', '', '我们主要投资消费，在熟悉的行业里运作。'),
])
def test_nonexclusive_copy_explicit_restriction_and_completed_trade(title, cover, source):
    assert title_rewrite.investment_exclusivity_error(title, cover, source) is None
