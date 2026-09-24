"""Real source17 repeatedly copied factual content from unrelated style samples."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p


def test_draft_style_does_not_supply_facts_from_other_videos():
    source='以下是可用于标题事实的嘉宾原话：光伏能源我没有特意去研究，所以我们没参与。'
    prompt='前文事实约束。最后在c_candidates写标题。每条标题必须明确说出讨论对象。'+source
    result=p._title_style_prompt(prompt, {'properties':{'c_candidates':{}}}, '林园')
    assert result.endswith(source) and result.startswith('前文事实约束。')
    for leaked in ('白酒', 'AI', 'PE', '分红', '科技', '医药', '消费'):
        assert leaked not in result
    assert '保留条件、否定、比较对象和不确定性' in result
    assert p._title_style_prompt(prompt, {'properties':{'c_guest_spans':{}}}, '林园') == prompt
