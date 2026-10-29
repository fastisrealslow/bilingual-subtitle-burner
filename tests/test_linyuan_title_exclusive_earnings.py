"""Necessary conditions cannot become an exclusive earning strategy."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import title_rewrite as T


def actual():
    return json.loads((Path(__file__).parent / 'fixtures' /
                       'linyuan_exclusive_earnings_actual.json').read_text())


def test_actual_positive_review_cannot_license_changed_exclusive_scope():
    f = actual()
    assert all(f['title_rewrite']['review'][k] is True for k in T.CHECKS)
    assert T.exclusive_earnings_scope_error(f['title'], f['cover'], f['raw_text'])
    assert T.error(f['title'], f['title_rewrite'], f['raw_text'])
    item = dict(title=f['title'], cover_title=f['cover'],
                subject=f['title_rewrite']['subject'],
                evidence=f['title_rewrite']['evidence'])
    assert '唯一策略' in T._candidate_error(item, f['raw_text'], '林园', (), check_layout=False)
    # A safe title does not license a changed cover, and vice versa.
    for title, cover in [(f['title'], '投资是长期行为'),
                         ('林园：投资是长期行为', f['cover'])]:
        assert T.forecast_copy_error(title, cover, f['raw_text'])


@pytest.mark.parametrize('obj,verb,only,result', [
    ('泡沫', '赚', '只', '涨几千倍'),
    ('品牌', '挣', '仅', '增长十倍'),
    ('技术', '赚', '唯独', '翻十倍'),
    ('周期', '挣', '只', '赚大钱'),
])
def test_same_object_condition_does_not_license_an_exclusive_action(obj, verb, only, result):
    source = f'我们的经验是要{verb}{obj}的钱。只有{obj}才能{result}。'
    bad = f'林园：{only}{verb}{obj}的钱'
    assert T.exclusive_earnings_scope_error(bad, f'要{verb}{obj}的钱', source)
    assert T.exclusive_earnings_scope_error(f'林园：要{verb}{obj}的钱', bad, source)


@pytest.mark.parametrize('copy', ['只赚泡沫钱', '只赚泡沫的钱', '为什么只赚泡沫的钱？'])
def test_grammar_variants_and_presupposed_questions_still_change_scope(copy):
    source = '要赚泡沫的钱，因为只有泡沫才能涨几千倍。'
    assert T.exclusive_earnings_scope_error('林园：' + copy, copy, source)


@pytest.mark.parametrize('title,cover,source', [
    ('林园：要赚泡沫的钱', '只有泡沫才能涨几千倍', '要赚泡沫的钱，因为只有泡沫才能涨几千倍。'),
    ('林园：只赚泡沫的钱', '只赚泡沫的钱', '我们只赚泡沫的钱。只有泡沫才能涨几千倍。'),
    ('林园：仅赚品牌的钱', '只赚品牌的钱', '我们只赚品牌的钱。要赚品牌的钱。唯有品牌才会增长十倍。'),
    ('林园：不只赚泡沫的钱', '并非只赚泡沫的钱', '要赚泡沫的钱，因为只有泡沫才能涨几千倍。'),
    ('林园：只赚技术的钱', '只赚技术的钱', '要赚技术的钱。只有品牌才能涨几千倍。'),
    ('林园：只赚品牌的钱', '只赚品牌的钱', '只有品牌才能增长十倍。我们讨论泡沫，要赚泡沫的钱。'),
    ('林园：只赚工资的钱', '只赚工资的钱', '我们要赚工资的钱，工作带来收入。'),
    ('林园：只赚品牌的钱', '只赚品牌的钱', '投资并不容易，只赚品牌的钱。要赚品牌的钱。只有品牌才能增长十倍。'),
])
def test_preserved_conditions_explicit_strategy_and_unrelated_claims(title, cover, source):
    assert T.exclusive_earnings_scope_error(title, cover, source) is None


def test_unrelated_or_negated_source_exclusivity_does_not_license_the_action():
    source = '我们只赚品牌的钱。不是只赚泡沫的钱。要赚泡沫的钱，只有泡沫才能涨几千倍。'
    assert T.exclusive_earnings_scope_error('林园：只赚泡沫的钱', '只赚泡沫的钱', source)


def test_actual_source_is_not_rewritten_to_get_a_pass():
    f = actual()
    safe = '林园：要赚泡沫的钱，只有泡沫才能涨几千倍'
    assert T.exclusive_earnings_scope_error(safe, '投资是长期行为，要赚泡沫的钱', f['raw_text']) is None
    assert f['title_rewrite']['title'] == f['title']
    assert '因为只有泡沫才能涨几千倍、上万倍' in f['raw_text']
