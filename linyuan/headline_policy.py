"""CPU-only, source-traceable title candidates and independent cover copy."""
import re
import difflib

VERSION = 2026091001
TOPICS = ('片仔癀','茅台','股息','分红','医药','消费','科技股','机器人','老龄化','现金流','投资','企业')
QUESTION = re.compile(r'您|请问|想问|请教|聊聊|林总|分享一下|[？?]')
CONDITION = re.compile(r'如果|假如|除非|只有|虽然|即使|只要')
TAIL = re.compile(r'(?:因为|所以|如果|虽然|但是|以及|而且|关于|对于|我们说的买入|我本人学医的|我们认为|我们说的|能够|可以|需要|这些|那些|这个|那个|的话|是否|的|是|与|把|被|比|更|还|在|会)$')
VERB = re.compile(r'买|卖|投|持有|涨|跌|赚|亏|值|回报|增长|增加|下降|风险|降价|涨价|机会|不|有|够|活|重要|便宜|贵|少|多|强|弱|完|老龄化|股息率')


def compact(text):
    return re.sub(r'[^0-9A-Za-z\u4e00-\u9fff%％.]','',str(text or ''))


def body(title, speaker='林园'):
    text=re.sub(rf'^(?:股神)?{re.escape(speaker)}[：:]\s*','',str(title or ''))
    return re.sub(rf'【重制试看】|[｜|]{re.escape(speaker)}$|\s+','',text).strip('，。；,; ')


def complete(text):
    # A source quote can be verbatim yet unreadable: do not promote a false
    # start, dangling bank clause or repeated filler into a permanent headline.
    if re.search(r'^(?:有的甚至|基本上|啊|呃)|(?:这个){2}|(?:那么){2}|我我|他他|去去|行业的行业',text):
        return False
    return bool(text and not QUESTION.search(text) and not TAIL.search(text)
        and not re.search(r'…|\.{3}|^(?:作为|关于|对于|至于|因为|所以|但是|那么|那个|这些|那些|就是|和|也看到|是因为)',text)
        and VERB.search(text))


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
            if (min_chars<=len(compact(candidate))<=max_chars and complete(candidate)
                    and candidate not in result):result.append(candidate)
    return result


def score(text):
    concrete=sum(word in text for word in TOPICS[:10])
    action=bool(re.search(r'买|卖|持有|分红|股息|回报|涨价|降价',text))
    vague=bool(re.search(r'机遇与挑战|投资哲学|投资逻辑|投资理念|核心逻辑|我们说的',text))
    return 6*concrete+4*action+2*bool(re.search(r'我|你',text))-5*vague-abs(len(compact(text))-24)/12


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
    # Honest topic label when no safe short quotation exists. Never fake completeness.
    topic=next((word for word in TOPICS if word in original and word in source),'投资')
    return {'text':topic+'观点','kind':'topic_label','evidence':topic,'reason':'no_complete_short_quote'}


def attach_copy(result, transcript, speaker='林园', existing_titles=None):
    result=dict(result)
    candidates=title_candidates(transcript,speaker,existing_titles)
    if result['title'] not in candidates:candidates.insert(0,result['title'])
    cover=cover_copy(result['title'],transcript,speaker)
    result.update(cover_title=cover['text'],cover_copy=cover,title_candidates=candidates[:3],
                  packaging_version=VERSION,packaging_method='source_quotes_cpu')
    return result
