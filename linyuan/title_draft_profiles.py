"""Isolated drafting intervention; source reading and independent review stay intact."""
import copy
import re


def source_choices_schema(schema):
    """Each alternative chooses and binds its own source claim before wording."""
    limited=source_limits_schema(schema)
    if limited is schema:return schema
    focus=limited['properties']['b_focus']
    copies=copy.deepcopy(limited['properties']['c_candidates'])
    fields={'a_focus':focus,**copies['items']['properties']}
    copies['items']['properties']=fields
    copies['items']['required']=list(fields)
    return dict(type='object',additionalProperties=False,required=['c_candidates'],
                properties={'c_candidates':copies})


def source_choices_messages(messages,schema):
    result=concise_messages(messages,schema)
    if result is messages:return messages
    marker='以下是可用于标题事实的嘉宾原话'
    content=result[0]['content'];source=marker+content.rsplit(marker,1)[1]
    attempt=re.match(r'第\d+轮重新拟稿[^\n]*\n',content)
    instruction='''你是访谈短视频编辑。读完下方嘉宾原话，分别选择三个有原文依据的看点，不必把三个标题都绑在同一句行业总结上。
第一个候选优先呈现嘉宾自己的实际选择或行动；第二个呈现原话中最鲜明的具体判断；第三个提出本段确实回答的具体问题。没有对应看点就另选完整判断，不编造个人选择、经历或反差。
每个候选只说清一件事。先在自己的a_focus中逐字摘出该观点必须保留的限定a_0_source_limits，再用a_claim写清完整判断，用b_evidence_ids指出依据及必要上下文，最后才写title和cover_title。
标题谈具体做法时，只写对象和实际动作也可以，不必把全段的理由、背景、数据都塞进去。写了理由或数字，就必须保留听说、是否研究、时间、统计对象、单位、前提和不确定性。
用本人自然说话的短句。保留原话大胆的语气和明确否定，不改成报告总结；也不增加原文没有的因果、收益承诺或谨慎建议。不能只复制转写的口吃、重复或不通顺词语；有疑点就选本段其他清楚的判断，不猜字。
title以“林园：”开头，正文12至52字；cover_title为8至18字的完整短句，不加姓名。标题和封面各自说明具体对象、同一个观点和必要限定，不截断。用字数更短的自然句子，不把词组硬拼起来。
只使用下方嘉宾原话，编号只能选择允许的原文编号。输出规定JSON。
'''
    result[0]['content']=(attempt.group(0) if attempt else '')+instruction+source
    return result


def spoken_focus_schema(schema):
    """Bounded source alternatives before selecting one angle, not free thinking."""
    result=source_limits_schema(schema)
    if result is schema:return schema
    focus=result['properties']['b_focus']
    ids=copy.deepcopy(focus['properties']['b_evidence_ids'])
    option=dict(type='object',additionalProperties=False,
        required=['a_angle','b_evidence_ids'],properties={
            'a_angle':dict(type='string',enum=['个人选择','明确判断','具体经历','真实反问']),
            'b_evidence_ids':ids})
    # JSON-schema grammars follow property insertion order, not `required`.
    # Appending this field made the real model write the claim before its hooks.
    focus['properties']={'a_00_hook_options':dict(type='array',minItems=1,maxItems=3,items=option),
                         **focus['properties']}
    focus['required']=['a_00_hook_options',*focus['required']]
    return result


def spoken_focus_messages(messages,schema):
    result=source_limits_messages(messages,schema)
    if result is messages:return messages
    instruction='''先用a_00_hook_options从嘉宾原话找1至3个可独立说清的看点，记录类型与原文编号；没有的类型不凑数。这是待选素材，不是把它们全部写进标题。
从中只选一个最鲜明、依据完整的判断。先在a_0_source_limits记录这一个判断所需的限定，再写入a_claim，并用b_evidence_ids保留该判断及必要上下文。
可直接表述清楚的个人选择，不要改写成含三个观点的行业总结。若一个判断有必要的正反两面，必须保留；若是同段另一件事，就不要用“且、并、同时”硬塞进来。
先写最自然的完整短句，再核对原文。第一人称、动作和态度只能来自嘉宾确实说过的话，不能替他编理由。标题不是审稿说明，不把“已核对、有限定”等编辑过程写给观众。
同一个观点写三个表达：直接说出选择或判断、保留原话语气、提出本段确实回答的具体问题。不强行凑反差，也不写三个同义词替换稿。
封面也写完整口语，宁可保留自然动词，不压成“企业会第一”“医药危机股”这样的词组。对象和必要条件不能为了短而丢失。
'''
    result[0]['content']=result[0]['content'].replace('先填写b_focus.a_0_source_limits',instruction+'先填写b_focus.a_0_source_limits',1)
    return result


def source_limits_schema(schema):
    """Brief source constraints before drafting; no unbounded thinking mode."""
    if 'c_candidates' not in (schema or {}).get('properties', {}):
        return schema
    result=copy.deepcopy(schema)
    focus=result['properties']['b_focus']
    focus['properties']={'a_0_source_limits':dict(type='array',minItems=0,maxItems=4,
        items=dict(type='string',maxLength=100)),**focus['properties']}
    focus['required']=['a_0_source_limits',*focus['required']]
    return result


def source_limits_messages(messages, schema):
    result=concise_messages(messages,schema)
    if result is messages:return messages
    content=result[0]['content']
    instruction='''先填写b_focus.a_0_source_limits，再填写a_claim和候选标题。
逐字摘出所选观点相关的原话限定，每项一句：谁说的、是否亲自研究、是否只是听说、统计的是谁或什么、哪段时间、有哪些前提。最多四条；没有相关限定就留空。
这些是编辑必须保留的原文事实，不是让你解释推理过程。不能把听来的说法写成已证实的事实；不能换统计对象，也不能省略统计时间后把历史变化写成没有期限的现状。
每个标题与封面各自保留所选判断所需的限定；如果限定太多难以写清，就改选同段另一条明确判断，不用一条断言包揽全段。
优先选嘉宾明确说过的个人选择或具体判断。只写选择时，不必塞进整段理由；写到理由时才完整保留该理由的限定。不能用“因、且、故”的报告句式把所有限定串成标题。
标题和封面必须各自说清具体对象，不能只摘“这些公司”“这些东西”。人口年龄、某个年龄群体与总人口是不同对象，不能在压缩时互换。
'''
    result[0]['content']=content.replace('先在b_focus.a_claim',instruction+'先在b_focus.a_claim',1)
    return result


def concise_messages(messages, schema):
    if 'c_candidates' not in (schema or {}).get('properties', {}):
        return messages
    if (not isinstance(messages, list) or len(messages) != 1
            or messages[0].get('role') != 'user' or not isinstance(messages[0].get('content'), str)):
        raise TypeError('Unexpected production title message shape')
    return [{**messages[0], 'content': concise_draft(messages[0]['content'], schema)}]


def concise_draft(prompt, schema):
    if 'c_candidates' not in (schema or {}).get('properties', {}):
        return prompt
    marker = '以下是可用于标题事实的嘉宾原话'
    if marker not in prompt:
        raise ValueError('Concise trial requires attributed guest statements')
    source = marker + prompt.rsplit(marker, 1)[1]
    # Keep the attempt marker so a deterministic retry is not the identical
    # prompt again. Do not reintroduce facts from previously rejected drafts.
    attempt=re.match(r'第\d+轮重新拟稿[^\n]*\n',prompt)
    retry=attempt.group(0) if attempt else ''
    return retry+'''你是访谈短视频编辑。只根据下面嘉宾的原话拟标题。
先在b_focus.a_claim写清一个有看点的完整判断及必要条件，用b_evidence_ids标出原文依据。
再在c_candidates写3个不同角度的自然标题：鲜明判断、具体选择、这段回答的问题。观点必须相同。
title以“林园：”开头，正文12至52字；cover_title用8至18字的完整短句。一个判断说完整就可以，不凑第二句。明确说出讨论对象，不截断。
保留原话里最有辨识度的动词和态度；原文明确说自己的做法时可用第一人称。不要写报告式总结。
不要添加原文没有的反差、因果、结论、收益承诺或口号。保留否定、条件、程度、对象范围与不确定性。
转写不通顺的词不要当作金句，也不要猜它本来是什么；可从同段明确完整的句子选看点。
封面要让人知道具体在说什么，和标题表达同一个判断，不比原话更绝对。只输出规定的JSON。
''' + source
