"""Source-bound recall of an actual recorded company judgment, never a forecast.

The fixed company-case category already includes concrete past judgments.
This second review only runs when the full answer independently contains a
named company, the guest's actual writing/speaking, and a historical record
of its outcome. It does not infer a trade, profit or present-day prediction.
"""
import json
import re

VERSION=1


def context_excluded(text):
    return bool(re.search(r'(?:并非|不是)(?:我|我们|您|你)(?:自己)?的(?:文章|著作|判断|经历)|'
        r'(?:我|我们|本人|嘉宾)[^。！？!?]{0,16}(?:没有|从未|没)[^。！？!?]{0,8}(?:发表|写过|说过)|'
        r'(?:引用|朗读|念|读)[^。！？!?]{0,20}(?:别人|他人|其他人|那位作者)[^。！？!?]{0,8}(?:文章|原话|经历)',text))


def named_company(text):
    import jieba.posseg
    from publishing_experiment import company_subject_error
    if company_subject_error(dict(kind='company_case',subject_quote=text)):return False
    # The general title tokenizer also returns common nouns such as "文章".
    # A narrow recall must independently require a proper organization/brand
    # token rather than promoting every book or historical record to a company.
    return any(w.flag in {'nt','nz','eng'} and 2<=len(w.word)<=20
        and not re.fullmatch(r'(?:文章|那本书|书籍|结果|公司|集团|银行|中国|美国|ETF)',w.word,re.I)
        for w in jieba.posseg.cut(text))


def past_record(text):
    if re.search(r'(?:如果|假设|假如|要是|打算|准备|计划|将来|以后|未来|明天|下次|没有|没写|没说|从未)',text):
        return False
    return bool(re.search(r'我(?:们)?[^。！？!?]{0,50}(?:写(?:的|过|了)?(?:这[个篇本])?(?:文章|书)|'
        r'(?:当时|当年|那年|以前|曾经)[^。！？!?]{0,25}(?:说过|说|判断|认为))',text))


def historical_outcome(text):
    if re.search(r'(?:如果|假设|假如|要是|以后|未来|明天|下次|将来|打算|准备|计划|没有|并未)',text):
        return False
    return bool(re.search(r'(?:记录|记载|后来|最终)[^。！？!?]{0,100}(?:结果|发生|涨|跌|年)',text)
        and re.search(r'(?:20\d{2}|[二两][零〇一二三四五六七八九]{3})年?',text))


def selection_error(selection):
    if selection.get('company_documentary_recall_version')!=VERSION:
        return 'Unknown documented company recall version'
    if selection.get('kind')!='company_case' or not past_record(selection.get('event_quote','')):
        return 'Documented company recall requires actual past writing or speaking'
    if not named_company(selection.get('subject_quote','')):
        return 'Documented company recall requires a named organization or brand'
    if not historical_outcome(selection.get('detail_quote','')):
        return 'Documented company recall requires a distinct historical outcome'
    event=selection['event_quote'];detail=selection['detail_quote']
    if event in detail or detail in event:
        return 'Documented event and outcome must be distinct source statements'
    return None


def request(cues,pick):
    from publishing_experiment import _selection_rows,company_subject_error
    rows=_selection_rows(cues,[pick])
    if not rows:return None
    if context_excluded(rows[0]['text']):return None
    units=rows[0]['sentences']
    subjects=[s['id'] for s in units if len(s['text'])<=140 and named_company(s['text'])]
    events=[s['id'] for s in units if 4<=len(s['text'])<=140 and past_record(s['text'])]
    details=[s['id'] for s in units if 4<=len(s['text'])<=140 and historical_outcome(s['text'])]
    if not subjects or not events or not details:return None
    evidence={k:dict(type='integer',enum=v) for k,v in (
        ('subject_sentence',subjects),('event_sentence',events),('detail_sentence',details))}
    # Nested evidence sorts before the verdict even on runtimes that sort
    # schema properties alphabetically. The model must read evidence first.
    checks={k:dict(type='boolean') for k in ('named_company','guest_actually_published','same_historical_record')}
    checks['reason']=dict(type='string',maxLength=160)
    props=dict(a_evidence=dict(type='object',properties=evidence,required=list(evidence),additionalProperties=False),
        b_assessment=dict(type='object',properties=checks,required=list(checks),additionalProperties=False),
        z_kind=dict(type='string',enum=['skip','company_case']))
    schema=dict(type='object',properties=props,required=list(props),additionalProperties=False)
    prompt=('核对这一完整访谈中的历史公司个案。先选择三条原句编号，再判断是否入选：'
        '明确点名的具体公司；嘉宾明确回顾自己确实写过文章、书或当时说过的判断；'
        '另一条原话给出有年份的历史记录或后续结果。三条必须讲同一件往事。'
        '本题的已发生行为是嘉宾当时确实发表过公司判断，不要求买卖持仓。'
        '历史记录可交代当时的判断、争议及记录年份，不要求证明预测兑现；'
        '书名、买卖记录和盈亏不是入选必要条件。只核对发表过这件事，不评估判断正确与否。'
        '文章里当时对未来的判断，可以作为已经写过的历史事件，但不能说成现在的预测，'
        '不能由写过文章推断已经买入、卖出、盈利或判断正确。'
        '单纯未来预测、一般投资原则、自我评价、主持人假设、别人的文章、未发生的计划、'
        '否认写过、没有具体后续记录，一律skip。'
        '只用本段原句，不改字、不补事实、不从其他段借证据。'
        '即使三个编号都有可选值，也须独立判断是否同一历史公司个案，不能为凑数接受。'
        '返回a_evidence中的编号，再在b_assessment核对是否点名具体公司、是否确实由嘉宾发表过、'
        '是否同一段历史记录，并用一句话说明依据。三项全部成立时z_kind返回company_case，'
        '任一项不成立返回skip。不要把“没有实际买卖”作为未发表过判断的依据。完整原句：\n'
        +json.dumps(units,ensure_ascii=False))
    return prompt,schema


def normalize(verdict):
    if not isinstance(verdict,dict) or set(verdict)!={'a_evidence','b_assessment','z_kind'}:
        raise ValueError('Invalid documented company verdict')
    evidence=verdict['a_evidence']
    assessment=verdict['b_assessment']
    fields=('named_company','guest_actually_published','same_historical_record')
    if (not isinstance(assessment,dict) or set(assessment)!={*fields,'reason'}
            or any(type(assessment[k]) is not bool for k in fields)
            or not isinstance(assessment['reason'],str) or not assessment['reason'].strip()
            or len(assessment['reason'])>160):
        raise ValueError('Invalid documented company assessment')
    if (not isinstance(evidence,dict) or set(evidence)!={'subject_sentence','event_sentence','detail_sentence'}
            or any(type(v) is not int for v in evidence.values())
            or verdict['z_kind'] not in ('skip','company_case')):
        raise ValueError('Invalid documented company evidence')
    if (verdict['z_kind']=='company_case')!=all(assessment[k] for k in fields):
        raise ValueError('Documented company verdict contradicts its independent evidence assessment')
    return dict(evidence,kind=verdict['z_kind'])
