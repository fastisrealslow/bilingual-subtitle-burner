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
    assert list(changed['properties']['b_focus']['properties'])[0]=='a_0_source_limits'
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


def test_runtime_profile_is_bound_to_copy_cache_identity(monkeypatch):
    import produce_cn as p
    monkeypatch.delenv('LINYUAN_TITLE_DRAFT_PROFILE',raising=False)
    before=p._copy_style_identity('林园')
    assert before['title_draft_profile']=='production'
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','source_limits')
    changed=p._copy_style_identity('林园')
    assert changed!=before and changed['title_draft_profile']=='source_limits'
    assert len(changed['title_draft_profile_sha256'])==64
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','unknown')
    with pytest.raises(ValueError,match='未知标题'):
        p._copy_style_identity('林园')


def test_spoken_focus_keeps_exact_guest_ids_and_isolated_reader_reviewer(monkeypatch):
    import copy
    import title_rewrite as t
    import produce_cn as p
    from title_draft_profiles import spoken_focus_schema,spoken_focus_messages
    schema=t.proposal_schema(6,guest_ids=[1,2,5]);before=copy.deepcopy(schema)
    changed=spoken_focus_schema(schema)
    assert schema==before
    options=changed['properties']['b_focus']['properties']['a_00_hook_options']
    assert list(changed['properties']['b_focus']['properties'])==[
        'a_00_hook_options','a_0_source_limits','a_claim','b_evidence_ids']
    assert options['maxItems']==3
    assert options['items']['properties']['b_evidence_ids']['items']['enum']==[1,2,5]
    source='以下是可用于标题事实的嘉宾原话：{"1":"我没研究过光伏。","2":"我也没参与。"}'
    messages=[dict(role='user',content='旧提示的其他公司观点。'+source)]
    result=spoken_focus_messages(messages,changed)
    assert result[0]['content'].endswith(source)
    assert '其他公司观点' not in result[0]['content']
    for stage in ('reviews','c_guest_spans'):
        other={'properties':{stage:{}}}
        assert spoken_focus_schema(other) is other
        assert spoken_focus_messages(messages,other) is messages
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','source_limits')
    old=p._copy_style_identity('林园')
    monkeypatch.setenv('LINYUAN_TITLE_DRAFT_PROFILE','spoken_focus')
    assert p._copy_style_identity('林园')!=old


def test_independent_source_choices_have_separate_claims_and_exact_allowed_evidence():
    import title_rewrite as t
    from title_draft_profiles import source_choices_schema,source_choices_messages
    schema=source_choices_schema(t.proposal_schema(8,guest_ids=[1,2,6,7]))
    assert list(schema['properties'])==['c_candidates']
    fields=schema['properties']['c_candidates']['items']['properties']
    assert list(fields)==['a_focus','title','cover_title']
    assert list(fields['a_focus']['properties'])==['a_0_source_limits','a_claim','b_evidence_ids']
    assert fields['a_focus']['properties']['b_evidence_ids']['items']['enum']==[1,2,6,7]
    source='以下是可用于标题事实的嘉宾原话：{"1":"光伏我没研究过，也没有参与。"}'
    result=source_choices_messages([dict(role='user',content='旧风格'+source)],schema)
    assert result[0]['content'].endswith(source)
    assert '不必把三个标题都绑在同一句行业总结' in result[0]['content']
    focused=source_choices_messages([dict(role='user',content='旧风格'+source)],schema,same_answer=True)[0]['content']
    assert focused.endswith(source)
    assert '围绕这一个判断' in focused
    assert '分别选择三个' not in focused
    assert '第一个候选优先呈现嘉宾自己的实际选择' not in focused
    assert '不必把三个标题都绑在同一句' not in focused
    units=['主持人问题假设。','光伏能源我没研究过，也没有参与。','别人告诉我光伏可能污染环境。']
    item=dict(a_focus=dict(a_claim='光伏能源我没研究过，也没有参与。',b_evidence_ids=[1]),
              title='林园：光伏能源我没研究过，也没有参与',cover_title='光伏我没研究过也没参与')
    bound=t.bind_guest_candidate(item,{},units,{'光伏能源':[1]},[1,2])
    assert bound['evidence']==[units[1]]
    with pytest.raises(ValueError,match='主持人'):
        t.bind_guest_candidate({**item,'a_focus':{**item['a_focus'],'b_evidence_ids':[0]}},{},units,{},[1,2])
    repeated=t.bind_guest_candidate({**item,'a_focus':{**item['a_focus'],'b_evidence_ids':[1,1]}},{},units,{},[1,2])
    assert repeated['evidence']==[units[1]]
    assert repeated['evidence_id_normalization']==dict(raw=[1,1],unique=[1])
    with pytest.raises(ValueError,match='主持人'):
        t.bind_guest_candidate({**item,'a_focus':{**item['a_focus'],'b_evidence_ids':[1,0,1]}},{},units,{},[1,2])
