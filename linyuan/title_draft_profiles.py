"""Isolated drafting intervention; source reading and independent review stay intact."""
import copy
import re


def source_limits_schema(schema):
    """Brief source constraints before drafting; no unbounded thinking mode."""
    if 'c_candidates' not in (schema or {}).get('properties', {}):
        return schema
    result=copy.deepcopy(schema)
    focus=result['properties']['b_focus']
    focus['properties']['a_0_source_limits']=dict(type='array',minItems=0,maxItems=4,
        items=dict(type='string',maxLength=100))
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
