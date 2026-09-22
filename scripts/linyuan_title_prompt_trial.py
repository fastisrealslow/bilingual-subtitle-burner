"""Isolated drafting intervention; source reading and independent review stay intact."""


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
    return '''你是访谈短视频编辑。只根据下面嘉宾的原话拟标题。
先在b_focus.a_claim写清一个有看点的完整判断及必要条件，用b_evidence_ids标出原文依据。
再在c_candidates写3个不同角度的自然标题：鲜明判断、具体选择、这段回答的问题。观点必须相同。
title以“林园：”开头，正文22至52字；cover_title用8至18字的完整短句。明确说出讨论对象，不凑字、不截断。
保留原话里最有辨识度的动词和态度；原文明确说自己的做法时可用第一人称。不要写报告式总结。
不要添加原文没有的反差、因果、结论、收益承诺或口号。保留否定、条件、程度、对象范围与不确定性。
转写不通顺的词不要当作金句，也不要猜它本来是什么；可从同段明确完整的句子选看点。
封面要让人知道具体在说什么，和标题表达同一个判断，不比原话更绝对。只输出规定的JSON。
''' + source
