from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from linyuan_title_prompt_trial import concise_draft, concise_messages


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


def test_actual_llm_message_list_reaches_the_model_with_new_draft():
    schema={'properties':{'c_candidates':{}}}
    original=[{'role':'user','content':'旧提示里的样例。以下是可用于标题事实的嘉宾原话：完整原话'}]
    changed=concise_messages(original,schema)
    assert changed[0]['role']=='user'
    assert changed[0]['content'].startswith('你是访谈短视频编辑')
    assert changed[0]['content'].endswith('嘉宾原话：完整原话')
    assert original[0]['content'].startswith('旧提示')
    assert concise_messages(original,{'properties':{'reviews':{}}}) is original
