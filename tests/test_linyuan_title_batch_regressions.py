"""Real Sep19 copy failures: gate both rewritten copy and quote fallback."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import headline_policy as H
import title_rewrite as T

@pytest.mark.parametrize('title',[
    '林园：是应该是也有规则，人家告诉你',
    '林园：你首先买买的，我用我的价值观，我觉得这个有风险',
    '林园：一个是当下你的买买入的成本',
    '林园：都是直接给我们带来的是一些回报',
    '林园：炒股是钱拿来炒，是对人人体的磨练。',
])
def test_real_fragments_cannot_pass_either_copy_path(title):
    assert not H.complete(H.body(title))
    assert T.copy_fragment(title)
    source=H.body(title)
    proof=T._package(dict(title=title,cover_title=source,evidence=[source],subject=source),
        source,dict(method='source_quote',quote=source),[])['title_rewrite']
    assert T.error(title,proof,source)

@pytest.mark.parametrize('title',[
    '林园：认知不够的行业，再好我也不碰',
    '林园：医药股经营不好，我就不买',
    '林园：买入股票之前先看经营情况',
    '林园：不卖，一股都不卖',
    '林园：人人都要为自己的投资负责',
    '林园：大家都知道分红重要',
])
def test_complete_conversational_judgments_remain_allowed(title):
    assert not H.verbal_fragment(title)
    assert not T.copy_fragment(title)
