"""CPU-only, source-traceable title candidates and independent cover copy."""
import re
import difflib

VERSION = 2026091301
TOPICS = ('片仔癀','茅台','股息','分红','医药','消费','科技股','机器人','老龄化','现金流','投资','企业')
QUESTION = re.compile(r'您|请问|想问|请教|聊聊|林总|分享一下|[？?]|你(?:是一直看好|还有哪|有持有|进入了|第一次|是怎么|怎么看|当时|集中投资|充分.{0,4}利用)|你.{0,24}(?:透露过|辞职|毕业之后)')
CONDITION = re.compile(r'如果|假如|除非|只有|虽然|即使|只要')
TAIL = re.compile(r'(?:因为|所以|如果|虽然|但是|以及|而且|关于|对于|我们说的买入|我本人学医的|我们认为|我们说的|能够|可以|需要|这些|那些|这个|那个|的话|是否|的|是|与|把|被|比|更|还|在|会)$')
VERB = re.compile(r'判断|考虑|布局|核心|买|卖|投|持有|涨|跌|赚|亏|值|回报|增长|增加|下降|风险|降价|涨价|机会|不|有|够|活|重要|便宜|贵|少|多|强|弱|完|老龄化|股息率')


def compact(text):
    return re.sub(r'[^0-9A-Za-z\u4e00-\u9fff%％.]','',str(text or ''))


def body(title, speaker='林园'):
    text=re.sub(rf'^(?:股神)?{re.escape(speaker)}[：:]\s*','',str(title or ''))
    return re.sub(rf'【重制试看】|[｜|]{re.escape(speaker)}$|\s+','',text).strip('，。；,; ')


def complete(text):
    # Standalone headlines must not depend on a missing antecedent or promote
    # hesitation/repair fragments just because they contain a finance keyword.
    if re.search(r'没办法|怎么办|(?:它|他|她)(?:只|就|都|也)|[，,](?:它|他|她|这个|那个)|呃|[啊哈呀][，,]|(?:做做|越越|人人口)', text):
        return False
    if re.search(r'(我|他|它|您|在|但|所以|那么|这个|就是|因为|还是)\1',text) or re.search(r'(?:^|[，,])(?:好|嗯|啊|呃|那么|比如说|对一些)(?:[，,]|$)',text):
        return False
    if re.match(r'^(?:它|他|她|这|那)(?!家企业|些企业)', text):
        return False
    # A source quote can be verbatim yet unreadable: do not promote a false
    # start, dangling bank clause or repeated filler into a permanent headline.
    if re.search(r'^(?:问题就是|有的甚至|基本上|啊|呃)|(?:这个){2}|(?:那么){2}|我我|他他|去去|不不|还不还|还还|是是|行业的行业',text):
        return False
    if text.count('就是') >= 2 or text.count('这个') >= 2:
        return False
    dangling = bool(TAIL.search(text)) and not re.search(r'(?:机会|社会|体会)$|(?:最厉害|最便宜|最重要|最有价值|可以入场|值得持有)的$',text)
    return bool(text and not QUESTION.search(text) and not dangling
        and not re.search(r'…|\.{3}|^(?:作为|关于|对于|至于|因为|所以|但是|那么|那个|这些|那些|就是|和|也看到|是因为)',text)
        and VERB.search(text))


def clean_quote(text):
    """Remove bounded verbal padding; never remove a condition or qualifier."""
    text=re.sub(r'^(?:(?:问题就是|所以就是|基本上|那么|就是)[，, ]*)+', '', text)
    for token in ('这个','那个','就是','我们','我'):
        text=re.sub(r'('+re.escape(token)+r'){2,}',token,text)
    return text


def quote_candidates(text, min_chars=10, max_chars=54):
    """Whole sentences/clauses only; never a character-budget prefix."""
    result=[]
    sentences=re.split(r'(?<=[。！？；!?;\n])',text)
    for index,sentence in enumerate(sentences):
        # Filter the entire interviewer sentence before splitting clauses;
        # otherwise a clause can shed its "您" and impersonate the answer.
        if QUESTION.search(sentence):continue
        if index+1<len(sentences) and QUESTION.search(sentences[index+1]):continue
        sentence=sentence.strip('，,：: 。！？；!?;\n')
        clauses=[p.strip() for p in re.split(r'[，,]+',sentence) if p.strip()]
        options=[sentence]
        # A condition is kept with its consequence, even if that means no short quote.
        if not CONDITION.search(sentence):
            options += clauses
            options += ['，'.join(clauses[i:i+2]) for i in range(len(clauses)-1)]
        for candidate in options:
            candidate=clean_quote(candidate)
            if (min_chars<=len(compact(candidate))<=max_chars and complete(candidate)
                    and candidate not in result):result.append(candidate)
    return result


def score(text):
    concrete=sum(word in text for word in TOPICS[:10])
    action=bool(re.search(r'买|卖|持有|分红|股息|回报|涨价|降价',text))
    vague=bool(re.search(r'机遇与挑战|投资哲学|投资逻辑|投资理念|核心逻辑|我们说的',text))
    # The reference account leads with an identifiable asset/industry and a
    # concrete judgment. Preserve both sides of a contrast, including 几乎/长期.
    judgment=bool(re.search(r'机会|风险|便宜|核心|不会|不卖|看好|最|历史|要知道',text))
    contrast=bool(re.search(r'但|却|而|长久|长期',text))
    anonymous=bool(re.match(r'他|它|这|那',text)) and not concrete
    padding=len(re.findall(r'这个|那个|就是|基本上|问题就是',text))
    return (6*concrete+4*action+4*judgment+3*contrast+2*bool(re.search(r'我|你',text))
            -8*anonymous-5*vague-3*padding-abs(len(compact(text))-30)/16)


def title_candidates(transcript, speaker='林园', existing_titles=None):
    rows=quote_candidates(body(transcript,speaker))
    rows.sort(key=score,reverse=True)
    result=[]
    for quote in rows:
        title=f'{speaker}：{quote}'
        if any(difflib.SequenceMatcher(None,compact(title),compact(old)).ratio()>=.84
               for old in [*(existing_titles or []),*result]):continue
        result.append(title)
        if len(result)==3:break
    return result


def cover_fits(text):
    """Choose copy that fits whole words before expensive video rendering."""
    from presentation import wrap_words
    clauses=[part for part in re.split(r'[，,。；;]',text) if part]
    if len(clauses)==2 and all(len(part)<=9 for part in clauses):return True
    try:
        wrap_words(''.join(clauses),9)
        return True
    except ValueError:
        return False


def cover_copy(title, transcript=None, speaker='林园'):
    original=body(title,speaker)
    if '完整访谈' in original:
        match=re.search(r'\d+分钟完整访谈',original)
        return {'text':match.group(0) if match else '完整访谈','kind':'format_label','evidence':title}
    source=body(transcript or original,speaker)
    if '机会' in original and '风险' in original:
        topic=next((word for word in TOPICS if word in original),None)
        if topic:
            focus='机会与长期风险' if re.search(r'长久|长期',original) else '机会与风险'
            label=topic+'，'+focus
            if cover_fits(label):
                return {'text':label,'kind':'topic_label','evidence':original,
                        'reason':'retain_both_sides_of_contrast'}
    # A few source-anchored headline extractions, with explicit evidence. These
    # do not apply when a condition, negation or uncertainty changes the claim.
    if not re.search(r'不|没|未|并非|如果|假如|可能|或许|只有|除非',original):
        for pattern in (
            r'(消费(?:和|与)?医药).{0,30}?(最值得的时候)',
            r'(企业).{0,8}?(投[的得]太多)',
            r'(股息率(?:有)?[0-9.]+%).{0,5}?(我买过)',
        ):
            match=re.search(pattern,original)
            if match:
                short='，'.join(match.groups())
                if len(compact(short))<=18 and cover_fits(short):
                    return {'text':short,'kind':'extractive_label','evidence':original,
                            'source_spans':[list(match.span(i)) for i in (1,2)]}
    if complete(original) and len(compact(original))<=18 and len(original)<=19 and cover_fits(original):
        return {'text':original,'kind':'quote','evidence':original}
    # Prefer a complete clause from the title; retain full conditional sentences.
    preferred=quote_candidates(original,6,18)
    for candidate in sorted(preferred,key=score,reverse=True):
        if len(candidate)<=19 and cover_fits(candidate):
            return {'text':candidate,'kind':'quote','evidence':original}
    related=[]
    title_terms={word for word in TOPICS if word in original}
    for candidate in quote_candidates(source,6,18):
        if len(candidate)>19 or not cover_fits(candidate):continue
        shared=sum(word in candidate for word in title_terms)
        # Source quotes must address the selected title's subject, not an arbitrary aside.
        if shared:related.append((shared*20+score(candidate),candidate))
    if related:
        candidate=max(related,key=lambda r:r[0])[1]
        return {'text':candidate,'kind':'quote','evidence':candidate}
    # A specific topic + focus is useful even when a qualified statement cannot
    # fit. These are labels, not shortened claims that discard "不/如果/几乎".
    topic=next((word for word in TOPICS if word in original and word in source),None)
    focuses=(('风险','风险怎么判断'),('股息','股息与买入条件'),
             ('价格','价格与买入条件'),('估值','估值与买入时机'),
             ('研发','研发投入'),('母亲','家人的使用经历'),
             ('老龄化','老龄化需求'),('买入','买入的条件'),
             ('回报','回报的判断'),('产品','产品与需求'))
    focus=next((label for word,label in focuses if word in original),None)
    if topic and focus and cover_fits(topic+'，'+focus):
        return {'text':topic+'，'+focus,'kind':'topic_label','evidence':original,
                'reason':'qualified_claim_retained_in_full_title'}
    # Keep the diagnostic result for callers, but never publish this placeholder.
    return {'text':(topic or '内容')+'待提炼','kind':'topic_label','evidence':original,
            'reason':'needs_editorial_copy'}


def attach_copy(result, transcript, speaker='林园', existing_titles=None, reviewed_cover=None):
    result=dict(result)
    from editorial_policy import publication_tags
    result['tags']=publication_tags(transcript,speaker,result.get('tags'),
                                    'full_interview' if '完整访谈' in result.get('tags',[]) else None)
    candidates=title_candidates(transcript,speaker,existing_titles)
    if result['title'] not in candidates:candidates.insert(0,result['title'])
    if result.get('title_rewrite'):
        from title_rewrite import error as rewrite_error
        error=rewrite_error(result['title'],result['title_rewrite'],transcript,speaker)
        if error:raise ValueError(error)
        cover=dict(text=result['title_rewrite']['cover'],kind='editorial_topic',evidence=transcript)
    else:
        cover=cover_copy(result['title'],transcript,speaker)
    if reviewed_cover is not None:
        if not cover_fits(reviewed_cover) or not 8<=len(compact(reviewed_cover))<=18:
            raise ValueError('编辑封面必须以完整词句排入两行')
        cover={'text':reviewed_cover,'kind':'reviewed_editorial','evidence':transcript}
    elif cover.get('reason')=='needs_editorial_copy' or cover['text'] in {'投资观点','投资逻辑','价值投资'}:
        raise ValueError('缺少具体封面文案，不能用投资观点等通用标签发布')
    result.update(cover_title=cover['text'],cover_copy=cover,title_candidates=candidates[:3],
                  packaging_version=VERSION,packaging_method=result.get('packaging_method','source_quotes_cpu'))
    return result
