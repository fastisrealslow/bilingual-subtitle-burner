"""Narrow, source-bound corrections supported by the original interview record.

Raw hypotheses and alignment stay unchanged on disk. This is an auditable
editorial layer, not a text-model rewrite or a global single-character glossary.
"""
SOURCE_SHA='9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345'
EVIDENCE_URL='https://finance.sina.com.cn/money/smjj/smgd/2025-08-21/doc-infmtwff7851353.shtml'
RULES=[
    (180,188,'钱是税出来的','钱是睡出来的','原访谈报道明确引用这项持有策略'),
    (685,696,'古老的房子','古老的方子','同时间原片字幕为方子，话题是中成药应用创新'),
    (685,696,'古老的一些房子','古老的一些方子','同时间原片字幕为方子，话题是中成药应用创新'),
    (1080,1088,'人有多大胆地有多大草','人有多大胆地有多大产','原访谈报道完整引用同一句话'),
    (1199,1205,'老老一话','老老龄化','原访谈报道确认此处讨论老龄化自然规律；保留口头重复'),
    (1543,1548,'只有股','绩优股','原访谈报道确认此处是市场主流绩优股'),
]


def apply_reviewed_corrections(words,source_sha):
    result=[dict(w) for w in words];changes=[]
    if source_sha!=SOURCE_SHA:
        return result,changes
    for lo,hi,before,after,reason in RULES:
        if len(before)!=len(after):
            raise ValueError('Reviewed homophone changes must preserve character timing')
        positions=[i for i,w in enumerate(result)
                   if lo<=w['start']<hi and w['text'].isalnum()]
        text=''.join(result[i]['text'] for i in positions)
        at=text.find(before)
        if at<0:
            continue
        if any(len(result[i]['text'])!=1 for i in positions):
            raise ValueError('Reviewed correction expects character-level alignment')
        selected=positions[at:at+len(before)]
        for i,char in zip(selected,after):
            result[i]['text']=char
        changes.append(dict(source_sha256=source_sha,start=result[selected[0]]['start'],
            end=result[selected[-1]]['end'],before=before,after=after,
            evidence_url=EVIDENCE_URL,reason=reason))
    return result,changes
