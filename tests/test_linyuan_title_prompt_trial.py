from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from linyuan_title_prompt_trial import concise_draft


def test_only_drafting_is_changed_and_guest_source_is_preserved():
    source = '以下是可用于标题事实的嘉宾原话：\n{"1":"如果生意能长期做下去，才是好买卖。"}'
    original = '风格示例含其他公司的事实。' + source
    rewritten = concise_draft(original, {'properties':{'c_candidates':{}}})
    assert rewritten.endswith(source)
    assert '其他公司的事实' not in rewritten
    for properties in ({'c_guest_spans':{}}, {'reviews':{}}):
        assert concise_draft(original, {'properties':properties}) == original
    with pytest.raises(ValueError):
        concise_draft('没有已归属的原文', {'properties':{'c_candidates':{}}})
