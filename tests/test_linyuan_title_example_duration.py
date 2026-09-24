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
