"""Narrow, source-bound corrections supported by the original interview record.

Raw hypotheses and alignment stay unchanged on disk. This is an auditable
editorial layer, not a text-model rewrite or a global single-character glossary.
"""
SOURCE_SHA='9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345'
SOURCE_RULES={SOURCE_SHA: dict(
    evidence_url='https://finance.sina.com.cn/money/smjj/smgd/2025-08-21/doc-infmtwff7851353.shtml',
    rules=[
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
    (1580,1585,'好我是不会参与这一次看不看','好我是不会参与这件事','原片字幕caption-006321.jpg清楚显示我是不会参与这件事；保留整短语原时间范围'),
    (1440,1445,'这个话题呢八年','这个话题九八年','原访谈报道确认此处回顾1998年已有的讨论'),
    (1254,1258,'真正爱的那些人','真正厉害的那些人','原片字幕caption-005020.jpg清楚显示真正厉害的那些人；保留整短语原时间范围'),
]),
'a7c6c8ccefd617c019f215f817c46379626008ec51da2f8ad215bfc217148b9b': dict(
    evidence_url='https://xueqiu.com/1127455234/359724421',
    rules=[
        (1023,1026,'大户人','大户','原访谈逐字稿为“我是个大户”；删除识别器多出的语气尾字'),
        (1026,1030,'万象前朝','万向钱潮','原访谈逐字稿明确提到上市公司万向钱潮'),
        (1034,1040,'挣起来了','争起来了','上下文是两人争论盘子大小；已核对样片字幕为“争起来了”'),
        (1039,1044,'它的原理','他的原理','上下文明确指前述投资总监；已核对样片字幕为“他的原理”'),
        (1059,1063,'违背违背常识','违背常识','原访谈逐字稿为“违背常识”；删除识别器重复词'),
        (1064,1066,'那个炒小白股','他就炒小盘股','上下文讨论股票盘子大小，原访谈逐字稿为“他就炒小盘股”'),
        (1082,1087,'每天每天收盘价都比这个现现在的这个盘子高','每天收盘价都是比开盘价高','原访谈逐字稿及已核对样片均为每天收盘价比开盘价高；保留原短语时间范围'),
        (1086,1090,'大红时期做','大户室去坐','原访谈逐字稿为到大户室去坐'),
        (1109,1113,'吃了碗肉面做了','吃了碗牛肉面不做了','已核对样片字幕为“吃了碗牛肉面，不做了”；逐字稿也确认牛肉面'),
        (1118,1128,'操纵市场的来的钱他是守不住的没有一个的这是什么时候清算的','操纵市场来的钱他是守不住的没有一个人能守住只是什么时候清算的事','原访谈逐字稿完整句确认没有一个人能守住，只是什么时候清算的事'),
        (1136,1140,'甚至还倾家荡产','甚至还要倾家荡产','原访谈逐字稿与已核对样片均包含关键助词“要”'),
        (1158,1166,'给我听我都懒是吧我没有评判的标准','给我说听我都懒得听我们有判断的标准','原访谈逐字稿确认两句原话；修复漏字及错误否定'),
        (1313,1317,'足力','逐利','原访谈逐字稿明确为资本是逐利的'),
    ]),
'40da16692854170b57b3ce38b20f4f8095d1f16ad2123bb68200a4864fed47dc': dict(
    evidence_url='https://caifuhao.eastmoney.com/news/20260128163342520840880',
    rules=[
        (912,918,'新智生产力','新质生产力','公开演讲整理稿及政策固定术语均为新质生产力'),
        (1051,1060,'铆定资产','锚定资产','原演讲标题及上下文固定术语为锚定资产'),
        (1062,1066,'铆定资产','锚定资产','同一完整观点第二次出现锚定资产'),
        (1067,1071,'铆定资产','锚定资产','同一完整观点第三次出现锚定资产'),
        (1113,1121,'炒小炒心','炒小炒新','公开演讲整理稿为炒小炒新'),
        (2502,2512,'时好时候','是好时候','公开演讲整理稿及完整句意为“是好时候”'),
        (2521,2529,'今天是有是投资的好时候','今天是投资的好时候','公开演讲整理稿核对；删除识别器重复字'),
        (2539,2544,'有创8%','有8%','后一句复述“有8%的股息”，两处语义相互校验'),
        (2568,2572,'炒小炒心','炒小炒新','公开演讲整理稿为炒小炒新'),
        (2951,2955,'115年16年','15年16年','上下文指2015年、2016年，公开整理稿亦为15年16年'),
    ]),
}


def apply_reviewed_corrections(words,source_sha):
    result=[dict(w) for w in words];changes=[]
    source=SOURCE_RULES.get(source_sha)
    if source is None:
        return result,changes
    for lo,hi,before,after,reason in source['rules']:
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
            evidence_url=source['evidence_url'],reason=reason))
    return result,changes
