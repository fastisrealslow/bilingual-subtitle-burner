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


def test_source_limits_trial_leaves_reader_reviewer_and_input_schema_unchanged():
    from linyuan_title_prompt_trial import source_limits_schema,source_limits_messages
    import copy
    schema={'properties':{'b_focus':{'required':['a_claim','b_evidence_ids'],
        'properties':{'a_claim':{'type':'string'},'b_evidence_ids':{'type':'array'}}},'c_candidates':{}}}
    before=copy.deepcopy(schema);changed=source_limits_schema(schema)
    assert schema==before
    assert changed['properties']['b_focus']['required'][0]=='a_0_source_limits'
    source='以下是可用于标题事实的嘉宾原话：{"3":"我好像有人给我说。"}'
    messages=[dict(role='user',content='旧风格'+source)]
    written=source_limits_messages(messages,changed)
    assert written[0]['content'].endswith(source)
    assert '不能把听来的说法写成已证实' in written[0]['content']
    for stage in ('reviews','c_guest_spans'):
        other={'properties':{stage:{}}}
        assert source_limits_schema(other) is other
        assert source_limits_messages(messages,other) is messages


def test_short_prompt_keeps_retry_identity_without_copying_rejected_claims():
    source='以下是可用于标题事实的嘉宾原话：{"2":"我们研究的公司。"}'
    prompt='第2轮重新拟稿；上轮未通过原文或文案检查。\n旧风格'+source
    result=concise_draft(prompt,{'properties':{'c_candidates':{}}})
    assert result.startswith('第2轮重新拟稿')
    assert result.endswith(source)
