"""Real model acceptance must not turn illustrative time into a requirement."""
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import title_rewrite as T


@pytest.mark.parametrize('source,title,cover',[
    ('最后比如说通过二十年才能找到真正的龙头。','林园：现在看不清楚，龙头要等二十年','龙头要等二十年'),
    ('例如经过三个月才能回本。','林园：投资需要3个月才能回本','需要3个月才能回本'),
    ('比方说等待十天就能看到结果。','林园：需要十天才能看到结果','结果要等十天'),
    ('比如通过二十年才能找到龙头。','林园：比如二十年才能找到龙头','龙头要等20年'),
    ('比如通过二十年才能找到龙头。','林园：龙头要等20年？','等龙头需要二十年吗？'),
])
def test_illustrative_duration_is_not_a_fixed_wait(source,title,cover):
    assert T.example_duration_error(title,cover,source)


@pytest.mark.parametrize('source,title,cover',[
    ('比如说通过二十年才能找到龙头。','林园：龙头还没定，得慢慢看','龙头还没定，得慢慢看'),
    ('比如说通过二十年才能找到龙头。','林园：举例说，二十年才能找到龙头','举例：龙头等二十年'),
    ('我们用了二十年才找到龙头。','林园：我们等了二十年才找到龙头','等了二十年才找到龙头'),
    ('比如经过二十年才能找到龙头。最终用了二十年。','林园：最终用了二十年','最终用了二十年'),
    ('比如经过二十年才能找到龙头，合同需要三个月。','林园：合同需要三个月','合同需要三个月'),
])
def test_nonnumeric_summary_explicit_example_and_real_duration_remain_allowed(source,title,cover):
    assert not T.example_duration_error(title,cover,source)


def test_actual_cpu_accepted_title_is_rejected_by_source_semantics():
    case=json.loads((ROOT/'tests/fixtures/linyuan_title_example_duration.json').read_text())
    assert all(case['proof']['review'][k] is True for k in T.CHECKS)
    assert '期限只是举例' in T.error(case['title'],case['proof'],case['transcript'])


@pytest.mark.parametrize('source,title,cover', [
    (
        '那么假设未来十三年或者十二年，中国经济的增长是零，那么我们可以判断，'
        '就是综合平均来说的话，那就是股市的回报周期，就时间就是十二到十三年。',
        '林园：当前股市估值低估，零增长下回报周期仅十二三年',
        '股市低估，回报周期仅十二三年',
    ),
    ('假定业务没有增长，收回投资成本得四年。', '林园：四年回本', '业务不增长要四年回本'),
    ('如果增长率为0，本金回收期为八年。', '林园：零增长下本金回收期八年', '本金回收期八年'),
])
def test_timed_payback_copy_retains_flat_growth_premise(source, title, cover):
    assert '零增长假设' in T.source_payback_condition_error(title, cover, source)


@pytest.mark.parametrize('source,title,cover', [
    ('如果经济不增长，回报周期十二年。', '林园：经济没有增长时回报周期十二年', '零增长时回报周期十二年'),
    ('假设经济零增长，回报周期是十二年。', '林园：零增长下回报周期十二年', '测算回报周期的条件是什么？'),
    ('估值偏低。假设经济零增长，回报周期是十二年。', '林园：零增长时回报周期十二年', '股市估值偏低'),
    ('如果经济零增长，经营也不受影响。我们用了四年回本。', '林园：我们四年回本', '我们四年回本'),
    ('如果经济非零增长，回报周期是十二年。', '林园：回报周期十二年', '回报周期十二年'),
])
def test_flat_growth_payback_check_preserves_properly_scoped_copy(source, title, cover):
    assert T.source_payback_condition_error(title, cover, source) is None
