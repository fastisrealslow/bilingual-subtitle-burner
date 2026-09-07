"""Narrow, source-bound corrections supported by the original interview record.

Raw hypotheses and alignment stay unchanged on disk. This is an auditable
editorial layer, not a text-model rewrite or a global single-character glossary.
"""
SOURCE_SHA='9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345'
EVIDENCE_URL='https://finance.sina.com.cn/money/smjj/smgd/2025-08-21/doc-infmtwff7851353.shtml'
RULES=[
    (986,993,'渗透吸','肾透析','同母片独立CPU识别为肾透气/透析；原字幕990.75秒为透析；原访谈报道明确为肾脏问题与透析室'),
    (1055,1060,'拿气','拿血压计','实看同母片caption-004227.jpg清楚显示以前拿血压计；独立CPU识别亦含血压计'),
    (1062,1067,'大数据','到处','实看同母片caption-004256.jpg清楚显示到处都是；独立CPU识别同词'),
    (180,188,'钱是税出来的','钱是睡出来的','原访谈报道明确引用这项持有策略'),
    (685,696,'古老的房子','古老的方子','同时间原片字幕为方子，话题是中成药应用创新'),
    (685,696,'古老的一些房子','古老的一些方子','同时间原片字幕为方子，话题是中成药应用创新'),
    (1080,1088,'人有多大胆地有多大草','人有多大胆地有多大产','原访谈报道完整引用同一句话'),
    (1199,1205,'老老一话','老老龄化','原访谈报道确认此处讨论老龄化自然规律；保留口头重复'),
    (1543,1548,'只有股','绩优股','原访谈报道确认此处是市场主流绩优股'),
    (1499,1508,'未高的情绪','畏高的情绪','原片字幕caption-006017.jpg与原访谈报道均为畏高'),
    (1440,1445,'这个话题呢八年','这个话题九八年','原访谈报道确认此处回顾1998年已有的讨论'),
    (1254,1258,'真正爱的那些人','真正厉害的那些人','原片字幕caption-005020.jpg清楚显示真正厉害的那些人；保留整短语原时间范围'),
]


def apply_reviewed_corrections(words,source_sha):
    result=[dict(w) for w in words];changes=[]
    if source_sha!=SOURCE_SHA:
        return result,changes
    for lo,hi,before,after,reason in RULES:
        positions=[i for i,w in enumerate(result)
                   if lo<=w['start']<hi and w['text'].isalnum()]
        text=''.join(result[i]['text'] for i in positions)
        at=text.find(before)
        if at<0:
            continue
        if any(len(result[i]['text'])!=1 for i in positions):
            raise ValueError('Reviewed correction expects character-level alignment')
        selected=positions[at:at+len(before)]
        start,end=result[selected[0]]['start'],result[selected[-1]]['end']
        if len(before)==len(after):
            for i,char in zip(selected,after):
                result[i]['text']=char
        else:
            # A verified multi-character repair retains the original phrase
            # interval. Do not claim freshly forced-aligned character times.
            result[selected[0]:selected[-1]+1]=[dict(text=after,start=start,end=end)]
        changes.append(dict(source_sha256=source_sha,start=start,end=end,before=before,after=after,
            timing='original_character_intervals' if len(before)==len(after) else 'original_phrase_interval',
            evidence_url=EVIDENCE_URL,reason=reason))
    return result,changes
