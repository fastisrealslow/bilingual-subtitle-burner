"""Thirty-day, fixed-slot editorial comparison; never a randomized CTR test.

Treatment means all three observable requirements actually passed, not merely
that a job requested the profile. Normal quality/rights/dedup gates still apply.
"""
import hashlib
import json
import math
import os
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone

VERSION = 2026101801
SELECTION_REVISION = 2026102201
EXPERIMENT_ID = 'field-story-30d-20261018'
BASELINE = 'baseline'
TREATMENT = 'field-story-v1'
START = '2026-10-18'
END_EXCLUSIVE = '2026-11-17'
HOURS = (10, 14, 16, 21)
MIN_MOTHER_SECONDS = 1200
CST = timezone(timedelta(hours=8))


def profile():
    value = os.environ.get('LINYUAN_EDITORIAL_PROFILE', BASELINE)
    if value not in (BASELINE, TREATMENT):
        raise ValueError('Unknown editorial experiment profile')
    return value


def assignment(now=None, makeup_slot=None):
    stamp = time.time() if now is None else now
    local = datetime.fromtimestamp(stamp, CST)
    slot = makeup_slot or local.strftime('%Y-%m-%d %H')
    try:
        parsed = datetime.strptime(slot, '%Y-%m-%d %H')
        if parsed.strftime('%Y-%m-%d %H') != slot or parsed.hour not in HOURS:
            return None
    except (ValueError, TypeError):
        return None
    if not START <= slot[:10] < END_EXCLUSIVE:
        return None
    ordinal = HOURS.index(parsed.hour) + 1
    return dict(experiment_id=EXPERIMENT_ID, version=VERSION, slot=slot,
                slot_number=ordinal, planned= TREATMENT if ordinal in (2, 4) else BASELINE)


def preparing(now=None):
    """Prepare stock one day early; do not alter the completed day's slots."""
    local = datetime.fromtimestamp(time.time() if now is None else now, CST).date()
    return date.fromisoformat(START)-timedelta(days=1) <= local < date.fromisoformat(END_EXCLUSIVE)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _substantive_quote(quote, minimum):
    plain=re.sub(r'[^\w\u4e00-\u9fff]','',quote)
    return (len(quote)>=minimum and len(plain)>=max(2,minimum-1)
        and not re.fullmatch(r'(?:嗯|啊|呃|哦|噢|好呀|好啊|好吧|好的|是吧|是的|对|对吧|对的|没错|谢谢您|谢谢|好|呀)+',plain))


def _selection_rows(cues,candidates):
    rows=[]
    for index, pick in enumerate(candidates[:12]):
        text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
        # Never truncate an answer's context to fit the classifier.
        if len(text) > 2400 or sum(len(r['text']) for r in rows)+len(text)>12000:
            continue
        # ASR display cues can split a word ("一定的" / "水平"). They are
        # NOT grammatical evidence boundaries. Rejoin the complete answer,
        # keep natural sentences, and only split >140-character sentences at
        # their real comma/semicolon boundaries. No words/context are dropped.
        units=[]
        for match in re.finditer(r'.+?(?:[。！？!?]+[”’」』\"]*|$)',text,re.S):
            value=match.group(0)
            units.extend(re.findall(r'.+?(?:[，,；;]+|$)',value,re.S) if len(value)>140 else [value])
        # ASR may join the actual past holding and a separate present choice
        # with a comma. Expose those intact punctuation clauses as evidence;
        # keep every original word, question and full candidate unchanged.
        if any(future_holding_subject(value) for value in units):
            expanded=[]
            for value in units:
                parts=(re.split(r'(?<=[，,；;])(?=我(?:们)?(?:也|还是|仍然|还|继续|一直|就|并)?'
                    r'(?:不会动|不动|一直拿|继续拿|继续持有)(?:[啊呀呢的了吧])?[。！!][”’」』\"]*$)',value)
                    if company_past_event_error(value) is None else [value])
                expanded.extend(parts)
            units=expanded
        substantive=[i for i,value in enumerate(units) if len(value)<=140
                     and _substantive_quote(value,4) and not re.search(r'[？?]',value)]
        if len(substantive)==1:
            # "我买了茅台，因为消费升级。" contains an event AND its reason.
            # Expose its actual punctuation clauses only when a single
            # assertion would otherwise force identical event/detail IDs.
            # These are evidence anchors, NOT subtitle display boundaries.
            at=substantive[0];clauses=re.findall(r'.+?(?:[，,；;]+|$)',units[at],re.S)
            if sum(_substantive_quote(v,4) for v in clauses)>=2:
                units[at:at+1]=clauses
        sentences=[dict(id=i,text=value) for i,value in enumerate(units)]
        if not any(len(s['text'])<=140 and _substantive_quote(s['text'],4)
                   and not re.search(r'[？?]',s['text']) for s in sentences):continue
        rows.append(dict(index=index,text=text,sentences=sentences))
    return rows


def selection_request(cues, candidates):
    rows=_selection_rows(cues,candidates)
    properties={}
    for row in rows:
        properties['candidate_'+str(row['index'])]=dict(type='object',additionalProperties=False,
            required=['kind','subject_sentence','event_sentence','detail_sentence'],properties={
                'kind':dict(type='string',enum=['skip','personal_story','company_case']),
                **{k:dict(type='integer',enum=[s['id'] for s in row['sentences'] if len(s['text'])<=140
                    and _substantive_quote(s['text'],2 if k=='subject_sentence' else 4)
                    and (k=='subject_sentence' or not re.search(r'[？?]',s['text']))])
                   for k in ('subject_sentence','event_sentence','detail_sentence')}})
    schema=dict(type='object',additionalProperties=False,required=list(properties),properties=properties)
    prompt=('逐个判断下面已经有自然起止的完整访谈段落，是否有具体看点。'
        '只能选嘉宾自己的真实经历/选择/判断失误，或点名公司的具体事例；'
        '泛泛谈认知、长期投资、行业趋势不能仅靠出现“我”或公司名入选。'
        'ETF、指数或基金的一般调仓机制不是具体公司案例，必须skip；公司案例的对象原句必须点名具体公司。'
        '“谢谢、林总、老师、网友”等致谢称呼不是公司名；“银行板块、医药行业”等也不是某家企业。'
        '嘉宾本人实际买入或退出某个行业，可按personal_story判断，但仍须有本人实际行为和另一条完整的原话理由；'
        '不能把行业硬标成company_case，也不能把未来建议改成已发生的个人经历。'
        '保留主持人有信息的问题，不改写、不拼接、不制造冲突。'
        '每个candidate_N只返回一次：kind为personal_story、company_case或skip。'
        '选择本段中的原句编号：subject_sentence交代具体对象；event_sentence是嘉宾'
        '说的具体事情或选择；detail_sentence是另一条原话原因/经过/细节，不能与event_sentence相同。'
        '不能从别的片段借事实，不能把主持人的假设当嘉宾结论。'
        'company_case也包括已经发生的具体公司投资选择及其理由：主持人明确询问过去为何选择某家已点名公司，'
        '嘉宾紧接着直接解释当时的理由、经过或所采取的标准，且没有否认该选择时，'
        '对象可取该问题原句，事件与细节分别取嘉宾两条不同的完整回答。'
        '这类回答不要求嘉宾再重复公司名或逐字说“我买了”；理由中的当时采取的标准可以交代这次选择。'
        '主持人只问未来可能如何，不能单凭问句认定过去发生过选择。'
        '但如果嘉宾明确回答自己已经买入、买入后持有至今、实际卖出等已经发生的行为，'
        '应按嘉宾回答判断公司案例，不能因为主持人问句含未来就忽略回答中的实际持仓。'
        '这时事件须取嘉宾明确叙述已发生行为的原句，细节仍须取另一条实质性回答。'
        '只有未来意愿、假设、嘉宾否认或未回答，不能据此认定已发生的公司案例。'
        'personal_story的事件必须是嘉宾讲出的具体行为或经历；company_case也可取对这次已发生公司选择的直接原因，'
        '不能把本次选择的直接回答误当成泛泛建议。“机会存在”等抽象总结不是事件。'
        '理论、原则、方法总结和应该怎么做的建议本身不是已经发生的个人经历；'
        'personal_story必须另有本人实际做过、没做过或明确采取某项选择的原句，不能只因为提到我们或过去经历就入选。'
        'personal_story的事件不要选比喻式劝告或“这样就行了”的建议结语；要选本人实际经历或选择的叙述原句，否则skip。'
        '“我今天是最多的时候”“我今天的盈利实力最好”是自我评价，不是具体行动；有别的真实行为原句则重选，否则skip。'
        '细节必须提供实质性原因、经过或后续选择；“嗯、好呀、是吧”等主持人应和不是细节。'
        '没有这三项就kind=skip，其余编号填任意允许值。不输出摘录文本。\n'
        +json.dumps([dict(index=r['index'],sentences=r['sentences']) for r in rows],ensure_ascii=False))
    return prompt, schema


def personal_event_error(event):
    """Selection and delivery must agree on an actual event, not advice."""
    plain=re.sub(r'[\s，,。！？!?；;]','',event)
    if re.match(r'^(?:(?:但是|不过|那么|那|所以)[，,\s]*)?'
                r'(?:(?:如果|假设|假如|要是|倘若)|(?:我|我们)(?:来|先|可以)?假设)',event):
        return 'A governing hypothetical is not an actual personal event; do not borrow a later recollection from the same sentence'
    if re.fullmatch(r'(?:嗯|啊|呃|所以|那么)*(?:我|我们)(?:自己|个人)?'
                    r'(?:觉得|认为|感觉|估计)(?:是|应该|肯定)?'
                    r'(?:(?:没|没有|不会有)(?:什么)?问题|可以|行)(?:啊|吧|嗯)*',plain):
        return 'A bare self-assessment is not a concrete personal event or actual choice'
    if re.fullmatch(r'(?:我|我们)(?:今天|现在|目前|当时|那时)?'
                    r'(?:的(?:盈利|赚钱|经营)(?:实力|能力|效率))?(?:是|处于)?'
                    r'最(?:多|好|强|高)(?:的)?(?:时候|阶段|状态|水平)?(?:了|啊|吧)*',plain):
        return 'A bare self-assessment or ranking is not a concrete personal event; select an actual source action'
    if re.search(r'(?:没有|不如|不像)您(?:这么|那么|这样|那样)',event):
        return 'An interviewer comparison addressed to the guest is not the guest personal event'
    normative_recap=re.search(r'(?:理论|原则|方法|总结)[^。！？!?]{0,90}(?:要|必须|应该|才能)',event)
    advice_conclusion=re.search(r'(?:就|才)(?:行|好|够|可以)了[啊吧。！!]*$',event)
    actual_action=re.search(r'(?:我|我们)[^。！？!?]{0,60}(?:买|卖|投|做|持有|参[与加])[^。！？!?]{0,6}(?:了|过)',event)
    prospective_choice=re.search(r'(?:我|我们)(?:今后|未来|以后|打算|计划|准备|想要|要|将|会){1,2}'
        r'[^，,。！？!?]{0,6}(?:抓住|买入|卖出|投资|退出|建仓|清仓|加仓|减仓|赚钱)',event)
    if prospective_choice and not actual_action:
        return 'A prospective personal plan is not an actual past event or completed choice'
    if (normative_recap or advice_conclusion) and not actual_action:
        return 'A normative method/theory recap or advice conclusion is not a concrete personal event or actual choice'
    return None


def personal_detail_error(detail):
    """An outcome's future timing alone is not a past story's reason/detail."""
    plain=re.sub(r'[\s，,。！？!?；;]','',detail)
    if re.fullmatch(r'(?:嗯|啊|这个|那个|那|其实|所以|反正)*'
            r'(?:(?:这个|那个)?时间(?:不|不会|也不|也不会|并不)?(?:太|很|算)?长)?'
            r'(?:大概|大约|可能|差不多|再过)?'
            r'[\d〇零一二三四五六七八九十百千万两]+(?:个)?(?:年|月|天)'
            r'(?:以后|之后|后)(?:就|才)?(?:能|会|可以)?'
            r'(?:见分晓|见结果|看到结果|见成效)(?:了|的|啊|吧|嘛)*',plain):
        return '未来多久见分晓的预测本身不是个人故事的真实原因或经历细节；请从原文重选实际理由/经历，否则skip'
    return None


def validate_choices(cues, candidates, response):
    response=json.loads(response) if isinstance(response,str) else response
    source_rows=_selection_rows(cues,candidates)
    if isinstance(response,dict) and 'choices' not in response:
        expected={'candidate_'+str(r['index']) for r in source_rows}
        if set(response)!=expected:raise ValueError('Story classifier omitted or invented a candidate')
        choices=[]
        for row in source_rows:
            verdict=response['candidate_'+str(row['index'])]
            if not isinstance(verdict,dict):raise ValueError('Invalid per-candidate story verdict')
            if verdict.get('kind')=='skip':continue
            units={s['id']:s['text'] for s in row['sentences'] if 2<=len(s['text'])<=140}
            choice=dict(index=row['index'],kind=verdict.get('kind'))
            for role in ('subject','event','detail'):
                index=verdict.get(role+'_sentence')
                if type(index) is not int or index not in units:
                    raise ValueError('Story sentence ID outside this candidate')
                choice[role+'_quote']=units[index]
            choices.append(choice)
        response=dict(choices=choices[:6])
    rows=response.get('choices') if isinstance(response,dict) else None
    if not isinstance(rows,list) or len(rows)>6:
        raise ValueError('Invalid story selection response')
    selected=[];seen=set();allowed={r['index'] for r in _selection_rows(cues,candidates)}
    for row in rows:
        if not isinstance(row,dict) or type(row.get('index')) is not int:
            raise ValueError('Story selection lacks an exact candidate index')
        i=row['index']
        if i in seen or i not in allowed:
            raise ValueError('Repeated/out-of-range story candidate')
        seen.add(i);pick=candidates[i]
        text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
        if row.get('kind') not in ('personal_story','company_case'):
            raise ValueError('Unknown story kind')
        for key,minimum in (('subject_quote',2),('event_quote',4),('detail_quote',4)):
            quote=row.get(key)
            if not isinstance(quote,str) or not minimum<=len(quote)<=140 or quote not in text:
                raise ValueError('Story evidence is not an intact quote in this candidate: '+key)
            if not _substantive_quote(quote,minimum):
                raise ValueError('Story evidence must contain a substantive source detail, not a filler')
            if key!='subject_quote' and re.search(r'[？?]',quote):
                raise ValueError('A question is context, not guest event/detail evidence')
        if row['event_quote']==row['detail_quote']:
            raise ValueError('A repeated claim is not a story detail')
        holding=future_holding_subject(row['subject_quote']) and not company_subject_error(
            dict(kind='company_case',subject_quote=row['subject_quote']))
        if holding:
            issue=('Named future holding questions need an actual company case' if row['kind']!='company_case' else None)
            issue=issue or company_holding_context_error(text) or company_past_event_error(row['event_quote'])
            issue=issue or company_holding_detail_error(row['detail_quote'])
            if (row['event_quote'] in row['detail_quote'] or row['detail_quote'] in row['event_quote']):
                issue=issue or 'Past holding event and later actual choice must be different nonoverlapping source clauses'
            if issue:raise ValueError(issue)
        if row['kind']=='personal_story':
            issue=personal_event_error(row['event_quote']) or personal_detail_error(row['detail_quote'])
            if issue:raise ValueError(issue)
        company_issue=company_subject_error(row)
        if company_issue:raise ValueError(company_issue)
        evidence={k:row[k] for k in ('kind','subject_quote','event_quote','detail_quote')}
        if holding:evidence['company_holding_evidence_version']=1
        selected.append({**pick,'story_selection':dict(version=VERSION,**evidence,
            transcript_sha256=digest(text))})
    return selected


def company_past_event_error(text):
    """Only explicit first-person past actions can enter holding recall."""
    if (not isinstance(text,str) or re.search(r'[？?]|如果|假如|假设|只要|将来|未来|以后|可能|打算|准备|计划|'
            r'没有|从没|从未|没买|没持有|没卖|不买|不持有|不卖|不是|未曾|未买|未持有|不曾|'
            r'考虑|想买|想过|希望|应该|也许|或许|会买|会持有|会投资|您|你们|林总|老师|听说|据说',text)
            or not re.search(r'我|我们|从买入|买入后|买入以来',text)
            or not re.search(r'已经|曾经|当时|当年|之前|过去|从买入|买入后|买入以来',text)
            or not re.search(r'买入|买了|持有|卖出|卖了|退出|投资',text)):
        return 'Company recall needs an explicit actual past action, not a question, intention, denial or hypothetical'
    return None


def company_holding_detail_error(text):
    if (not isinstance(text,str) or re.search(r'[？?]|如果|假如|假设|只要|未来|以后|将来|可能|应该|您|你们|听说|据说',text)
            or not re.search(r'我|我们',text)
            or not re.search(r'买入|买了|持有|卖出|卖了|退出|投资|不会动|不动|一直拿|继续拿',text)):
        return 'Company holding recall needs another explicit first-person holding/action statement'
    return None


def company_holding_question(text):
    # Keep this shared proof check lightweight: the publisher does not ship
    # the producer's complete source-selection module or its dependencies.
    return bool(isinstance(text,str) and re.fullmatch(
        r'(?:(?:请问|请教)?您|那(?:么)?[，,\s]*(?:这)?(?:未来|以后|今后)(?:还)?会继续'
        r'(?:看好|持有|投资))[^。！？?]{0,100}[？?]',text))


def future_holding_subject(text):
    return bool(company_holding_question(text) and re.search(r'未来|以后|今后',text))


def holding_evidence(selection):
    return any(type(selection.get(k)) is int and selection[k]==1 for k in
        ('company_holding_recall_version','company_holding_evidence_version'))


def company_holding_context_error(text):
    if not isinstance(text,str) or not text.strip():
        return 'Company holding recall requires the complete original transcript'
    if re.search(r'假设|假如|如果|(?:不是|并非|并不是).{0,12}(?:实际|真实).{0,8}(?:交易|买卖|持仓)',text):
        return 'Company holding recall cannot extract actual trades from a hypothetical or denied full context'
    return None


def company_holding_request(cues,pick):
    """A narrow second review; never offers the personal-story category.

    The original classifier is unchanged. Retry only a named-company question
    with an intact, explicit past holding/action sentence in its own answer.
    """
    context=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
    if company_holding_context_error(context):return None
    rows=_selection_rows(cues,[pick])
    if not rows:return None
    units=rows[0]['sentences']
    subjects=[s['id'] for s in units if len(s['text'])<=140 and company_holding_question(s['text'])
        and not company_subject_error(dict(kind='company_case',subject_quote=s['text']))]
    events=[s['id'] for s in units if 4<=len(s['text'])<=140 and not company_past_event_error(s['text'])]
    details=[s['id'] for s in units if 4<=len(s['text'])<=140
        and _substantive_quote(s['text'],4) and not company_holding_detail_error(s['text'])]
    if not subjects or not events or len(details)<2:return None
    # Evidence first ONLY for actual company holdings; personal-story advice
    # cannot be promoted by this schema. Skip remains an explicit option.
    props={**{k:dict(type='integer',enum=v) for k,v in (
        ('subject_sentence',subjects),('event_sentence',events),('detail_sentence',details))},
        'kind':dict(type='string',enum=['skip','company_case'])}
    schema=dict(type='object',additionalProperties=False,required=list(props),properties=props)
    prompt=('核对这一整段公司访谈。先从原句编号找：明确点名公司的问句、嘉宾本人实际发生过的买卖或持仓行为、'
        '另一条嘉宾本人明确叙述的持仓/买卖行为作为经过或后续选择。事件与细节必须是不同的完整陈述或原话分句，且事件确实针对该公司；'
        '编号分句保留原始标点，逗号结尾本身不表示事实残缺；须连读完整原段。'
        '已经实际持有了多久是已发生的经历，嘉宾明确说现在不动、不调整则是当前的后续选择；'
        '这两项交代过去持续时间与当前行为，不因同谈持有就当成同一项信息。'
        '但没有已发生的买卖或持仓，只有以后想持有多久，不能入选。'
        '嘉宾讲另一家公司的经历不能支持问句中的公司。不能用主持人的推断或一般投资原则。'
        '主持人可能在问以后怎么办；须看嘉宾是否明确回顾自己已经做过的事，不能把问句时态当回答时态。'
        '只有三项都成立才返回company_case；假设、未来打算、否认、没有回答、缺少另一条实质细节一律skip。'
        '本次不判断个人故事，不补词、不纠正识别、不借其他片段事实。'
        '先返回原句编号，最后返回kind；skip的编号可填任意允许值。\n'
        +json.dumps(units,ensure_ascii=False))
    return prompt,schema


def choose_stories(cues, candidates, call, path, *, attempts=2, budget_sec=1200):
    """Isolate candidate errors and retry only unreviewed/invalid decisions.

    Every accepted row still uses the strict source-bound validator. A bad
    neighbour, omitted row or service timeout cannot erase a valid decision.
    Raw requests/responses and unresolved rows survive partial success.
    """
    from pathlib import Path
    candidates=list(candidates)
    report=dict(revision=SELECTION_REVISION,requested_profile=TREATMENT,candidate_count=len(candidates),
        transcript_sha256=digest(cues),selected=[],rejected=[],unresolved=[],calls=[])
    def save():
        if path is not None:
            target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    # Two full answers per call keep CPU context bounded. Do not truncate any
    # answer or supply manually approved ranges/titles to repair model output.
    # The old whole-request cap (12 answers / 12k chars) is still appropriate
    # for selection_request, but NOT across separate two-answer calls. It
    # silently hid 11 of 23 real candidates while reporting completion.
    rows=[]
    for index,pick in enumerate(candidates):
        single=_selection_rows(cues,[pick])
        if single:
            rows.append({**single[0],'index':index})
        elif len(''.join(c['text'] for c in cues[pick['start']:pick['end']+1]))>2400:
            report['unresolved'].append(dict(index=index,error='complete_answer_exceeds_context_limit'))
        else:
            report['rejected'].append(dict(index=index,reason='no_substantive_answer_evidence'))
    report['eligible_candidates']=len(rows)
    deadline=time.monotonic()+budget_sec
    for start in range(0,len(rows),2):
        if time.monotonic()>=deadline:
            report['unresolved'].extend(dict(index=r['index'],error='selection_budget_exhausted') for r in rows[start:])
            break
        pending=[dict(index=r['index'],error=None) for r in rows[start:start+2]]
        for attempt in range(attempts):
            if time.monotonic()>=deadline:
                pending=[dict(index=r['index'],error='selection_budget_exhausted') for r in pending]
                break
            picks=[candidates[r['index']] for r in pending]
            prompt,schema=selection_request(cues,picks)
            errors=[dict(candidate=i,error=r['error']) for i,r in enumerate(pending) if r['error']]
            if errors:prompt+='\n仅重试以下未通过的候选；事件与细节必须选不同原句，否则skip。错误：'+json.dumps(errors,ensure_ascii=False)
            record=dict(indices=[r['index'] for r in pending],attempt=attempt+1,
                        prompt=prompt,schema=schema)
            report['calls'].append(record);save()
            try:
                raw=call(prompt,schema);record['response']=raw
                data=json.loads(raw) if isinstance(raw,str) else raw
                if not isinstance(data,dict) or set(data)-set(schema['properties']):
                    raise ValueError('Story classifier invented candidate keys')
            except (RuntimeError,ValueError,TypeError) as exc:
                record['error']=str(exc)
                pending=[dict(index=r['index'],error=str(exc)) for r in pending]
                save();continue
            again=[]
            for local,row in enumerate(pending):
                verdict=data.get('candidate_'+str(local))
                try:
                    # Remap one actual source candidate, not the whole batch.
                    selected=validate_choices(cues,[picks[local]],{'candidate_0':verdict})
                    if selected:report['selected'].extend(selected)
                    else:report['rejected'].append(dict(index=row['index'],reason='no_grounded_story'))
                except (ValueError,TypeError,KeyError,AttributeError) as exc:
                    again.append(dict(index=row['index'],error=str(exc)))
            pending=again;save()
            if not pending:break
        report['unresolved'].extend(pending);save()
    # The small production model repeatedly skipped an explicit past holding
    # solely because the host asked about the future. A second company-only
    # evidence review has stricter event requirements than the first review.
    reviews=[(row,'rejected') for row in report['rejected']
             if row['reason']=='no_grounded_story']
    # A primary response that borrowed the host's future inference is invalid,
    # not an editorial rejection. Give this exact role-proof failure the same
    # single, stricter review; transport/schema failures remain unresolved.
    reviews.extend((row,'unresolved') for row in report['unresolved'] if row['error']==
        'Company holding recall needs another explicit first-person holding/action statement')
    for rejected,bucket in reviews:
        index=rejected['index'];request=company_holding_request(cues,candidates[index])
        if request is None:
            if bucket=='unresolved':
                # This exact proof failure already has a valid named-company
                # past event. Without a second eligible guest statement the
                # stricter schema cannot exist: reject, never retry or accept.
                report[bucket].remove(rejected)
                report['rejected'].append(dict(index=index,reason='missing_guest_holding_detail'))
            continue
        if time.monotonic()>=deadline:
            report[bucket].remove(rejected)
            report['unresolved'].append(dict(index=index,error='company_holding_review_budget_exhausted'))
            continue
        prompt,schema=request
        record=dict(indices=[index],stage='company_holding_recall_v1',prompt=prompt,schema=schema)
        report['calls'].append(record);save()
        try:
            raw=call(prompt,schema);record['response']=raw
            verdict=json.loads(raw) if isinstance(raw,str) else raw
            if (not isinstance(verdict,dict) or set(verdict)!=set(schema['properties'])
                    or verdict.get('kind') not in ('skip','company_case')):
                raise ValueError('Invalid company holding verdict')
            chosen=validate_choices(cues,[candidates[index]],{'candidate_0':verdict})
            for item in chosen:
                issue=company_past_event_error(item['story_selection']['event_quote'])
                issue=issue or company_holding_detail_error(item['story_selection']['detail_quote'])
                if issue:raise ValueError(issue)
                item['story_selection']['company_holding_recall_version']=1
            report[bucket].remove(rejected)
            if chosen:report['selected'].extend(chosen)
            else:report['rejected'].append(dict(index=index,reason='no_grounded_story'))
        except (RuntimeError,ValueError,TypeError,KeyError,AttributeError) as exc:
            record['error']=str(exc);report[bucket].remove(rejected)
            report['unresolved'].append(dict(index=index,error=str(exc)))
        save()
    report['selection_completed']=not report['unresolved']
    report['retryable']=bool(report['unresolved'])
    # Keep positive backups: the renderer already stops at six ACCEPTED
    # videos, not six proposed stories. Bad ASR/pictures must not consume the
    # acceptance slots and hide later source-bound, independently gated picks.
    save()
    return report


def production_result(requested, metas, selection=None):
    actual=sum(actual_profile(m)==TREATMENT for m in metas)
    return dict(requested=requested,selection_revision=SELECTION_REVISION,
        experiment_accepted=actual,baseline_accepted=len(metas)-actual,
        experiment_complete=requested==TREATMENT and actual>0,
        fallback=requested==TREATMENT and bool(metas) and actual==0,
        retryable_selection=bool((selection or {}).get('unresolved')))


def operational_status(state, ready, now=None):
    """Receipt-defined execution health, not a playback-performance claim."""
    now=time.time() if now is None else now
    receipts={}
    for info in (state.get('published') or {}).values():
        for part in info.get('parts') or []:
            row=part.get('publishing_experiment')
            if (part.get('status')=='published' and part.get('bvid') and isinstance(row,dict)
                    and row.get('experiment_id')==EXPERIMENT_ID):
                receipts[row['slot']]=row
    planned=sorted((r for r in receipts.values() if r.get('planned')==TREATMENT),key=lambda r:r['slot'])
    streak=0
    for row in reversed(planned):
        if row.get('actual')==TREATMENT and row.get('compliant'):break
        streak+=1
    daily=[]
    for day in sorted({r['slot'][:10] for r in receipts.values()}):
        rows=[r for r in receipts.values() if r['slot'].startswith(day)]
        treatment=sum(r.get('planned')==TREATMENT and r.get('actual')==TREATMENT and r.get('compliant') for r in rows)
        controls=sum(r.get('planned')==BASELINE and r.get('actual')==BASELINE and r.get('compliant')
                     and r.get('content_type')!='full_interview' for r in rows)
        daily.append(dict(date=day,published=len(rows),treatment=treatment,controls=controls,
            fallback=sum(not r.get('compliant') for r in rows),valid_comparison=len(rows)==4 and treatment==2 and controls==2))
    fresh=ready is not None
    total=sum(ready.values()) if fresh else None
    alert=('unknown_stock' if not fresh else 'consecutive_fallbacks' if streak>=2 else
           'no_verified_treatment' if total==0 else
           'insufficient_treatment_by_layout' if int(ready.get('landscape') or 0)<2
               or total<4 else None)
    return dict(experiment_id=EXPERIMENT_ID,selection_revision=SELECTION_REVISION,
        active=preparing(now),verified_treatment=ready,consecutive_fallbacks=streak,
        actual_treatment_published=sum(r.get('compliant') and r.get('actual')==TREATMENT for r in planned),
        valid_comparison_days=sum(d['valid_comparison'] for d in daily),daily=daily,
        status='inactive' if not preparing(now) else 'needs_attention' if alert else 'ready',alert=alert)


def attach_proof(meta, cues, selection, mother_seconds):
    text=''.join(c['text'] for c in cues)
    if not isinstance(selection,dict) or selection.get('transcript_sha256')!=digest(text):
        raise ValueError('Story evidence does not match the actual retained source text')
    for key in ('subject_quote','event_quote','detail_quote'):
        if selection.get(key) not in text:
            raise ValueError('Story evidence was lost from the retained clip')
    proof=dict(version=VERSION,profile=TREATMENT,source_duration_sec=mother_seconds,
        selection=selection,title=meta.get('title'),source_sha256=meta.get('source_sha256'),
        video_sha256=(meta.get('fingerprints') or {}).get('sha256'),
        cover_sha256=(meta.get('cover_proof') or {}).get('sha256'))
    meta={**meta,'editorial_profile':TREATMENT,'experiment_proof':proof}
    issue=proof_error(meta)
    if issue:raise ValueError(issue)
    return meta


def story_title_prompt(prompt,schema,evidence):
    """Change the editorial angle, never speaker labels or factual approval.

    Normal title policy prefers a general central claim over an example. This
    experiment deliberately titles the actual example; merely appending style
    advice left the original central-point instruction in conflict.
    """
    fields=schema.get('properties',{})
    if not evidence or not any(k in fields for k in ('a_guest_answer','a_reading','c_candidates','reviews')):
        return prompt
    prompt=prompt.replace('不要把主持人的猜测或一处举例当成中心观点。',
        '不能把主持人的猜测当成嘉宾结论。本片选题就是下列具体事例，中心是嘉宾在这件事上的实际选择或判断及其原因。')
    prompt=prompt.replace('central_point抓住中心而不是举例或旁枝',
        'central_point抓住下列具体事例中嘉宾实际做了什么、为什么，不能改成别的话题或抽象自评')
    focus=title_focus(evidence)
    scope=('\n标题范围（仅为原文字词范围，不是事实批准）：'+json.dumps(dict(
        object_terms=focus['subjects'],named_reason_terms=focus['details'],
        body_only_background_terms=focus['background_terms']),ensure_ascii=False)+
        '。仅正文背景词不放入标题或封面；所有事实、主体和条件仍按完整原文核对。') if focus['background_terms'] else ''
    if evidence.get('kind')=='personal_story' and not focus['details']:
        scope+='\n细节中的泛指词不是标题关键词，不要为了出现这些词硬造一句话。仍须通读完整原文，说清本人实际选择及原话理由，不能借用提问前提。'
    if evidence.get('kind')=='personal_story' and focus['subjects']:
        scope+=('\n本人这次买卖/退出的原话对象：'+json.dumps(focus['subjects'],ensure_ascii=False)+
            '。标题前半句必须点明实际对象，不能只写“退出了、买了、持有了”让观众猜。'
            '保留原话的不确定性和理由；不要为了补对象删除“感觉、隐约、可能”等限定。')
    action_pairs=[v for k,v in focus.items() if k.startswith('detail_actions_')]
    if holding_evidence(evidence):
        scope+=('\n本次标题以嘉宾已经发生的买卖/持仓事实为中心，保留事件原句的时间或时长与近、约等范围：'
            +json.dumps(focus.get('past_holding_times',[]),ensure_ascii=False)+
            '。先讲已经持有多久，再讲本人当前选择；不要用主持人复述的最低未来年限代替过去经历。'
            '公司名可能只出现在紧接回答的明确问题中；问题可补足嘉宾“我们持有”的对象，'
            '但问题中的预测、未来最低年限和推测不能成为嘉宾事实。标题事实仍只引用嘉宾过去持仓和当前选择。'
            '事实证据编号须连贯交代实际持仓到现在的时长和当前不动的选择，不能用其他背景句替代。'
            '没有讲直接原因就不编原因；淡然看待行业消失不是卖出触发条件，不添加原话没有的除非/只要。'
            '涉及终身等远期说法必须保留可能、或许等限定。用完整自然口语，不把持有缩成单字持。')
    if action_pairs:
        scope+=('\n细节原话也包含完整的动作理由，不能只盯着其中的比喻名词：'
            +json.dumps(action_pairs,ensure_ascii=False)+
            '。可以用这组原词把理由说直白，不必硬塞比喻名词。先说本人实际选择，再接一条理由；'
            '同一个理由说一次就够，不要把“不做这两件事”和“否则要做这两件事”正反重复一遍。'
            '条件中的比喻也不是必然发生的时间节点，不要把假设写成将来的进程。'
            '只改善表达，不改变原句条件、否定或动作主体；不能把人的生活需要改成投资者必须投资。')
    return prompt+('\n本片只为下面的具体公司事例或亲身经历拟标题，不为此前的抽象自评拟标题。'
        '通读全部上下文，所有说话人和否定条件仍须独立核对，不删掉其他嘉宾回答。'
        '阅读概括先说清这件具体事情；拟稿时三个候选都从该事情的实际对象和选择开头，'
        '再表达本人理由；理由优先保留原话明确命名的理论或选择标准，不换成别的对象的增长或爆发。'
        '拟稿像本人随口讲清一件事：具体选择加一条最直接理由，正文优先16至26字，'
        '不为了字数截断、不省略必要条件。若事件或细节原句已明确命名理论或选择标准，'
        '直接沿用这个原词，不必把背景时点、程度描述和整条推理链都串进标题。'
        '已有明确命名的选择理论或标准时，标题只说具体选择和这一条命名理由；'
        '其他背景变量、时点和程度留在视频正文，不再压进标题。'
        '不要把未明确主语的“爆发”接到另一个名词上，制造原话没有明说的增长断言。'
        '三个候选换自然说法，不往同一句堆更多背景；避免连续“基于、前的、所导致”等书面连接。'
        '核对时事实支持、说话人归属和限定条件标准完全不变。'
        '下列线索是本段逐字原句，不是已批准的事实或预填标题；提问只提供上下文，不能当嘉宾断言。\n'
        +json.dumps(evidence,ensure_ascii=False)+scope)


def holding_time_anchors(text):
    from title_quantity_context import NUMBER,number
    rows=[]
    for m in re.finditer(rf'(接近|将近|近|大约|约)?({NUMBER})(年|个月|月)',str(text or '')):
        value=number(m[2])
        if value is None or value<=0:continue
        row=dict(value=value,unit=m[3].removeprefix('个'),approximate=bool(m[1]),raw=m[0])
        if row not in rows:rows.append(row)
    return rows


def title_focus(selection):
    """Literal source-word coverage, not an entailment or speaker approval."""
    import jieba.posseg
    generic={'公司','企业','投资','理论','原因','时候','当时','地方','产品','东西','事例','故事','选择','判断','水平','前期'}
    sectors={'银行','金融','医药','消费','科技','白酒','地产','证券'}
    categories={'ETF','指数','基金','指数基金','市场','股票','行业','板块','成份','成分','机制','规则','资金','差别','例子','成份股','成分股',
                '银行','金融','医药','消费','科技','白酒','地产','证券','机构','集团',
                '中国','美国','全球','国内','国外','国有','民营',
                '估值','股价','市值','利润','业绩','回报','走势','行情','风险','机会','盈利','财务','规模',
                '抄底','追涨','杀跌','加仓','减仓','建仓','清仓','买入','卖出','退出','持有',
                '谢谢','感谢','您好','你好','老师','先生','女士','博士','董事长','经理',
                '主持人','网友','观众','朋友','朋友们','大家','嘉宾','记者','股东'}
    def terms(text,proper=False):
        words=list(jieba.posseg.cut(text))
        tokens=[w.word for w in words if 2<=len(w.word)<=10 and w.word not in generic
                and not (selection.get('kind')=='personal_story' and w.word in {'个人','时间'})
                and (not proper or w.word.upper() not in categories)
                # Jieba can tag "谢谢" and "林总" as proper nouns. Literal
                # coverage must not turn a greeting/address into a company,
                # then force the title to say it. Never rewrite the source.
                and (not proper or not re.fullmatch(r'[\u4e00-\u9fff]{1,6}(?:总|老师|先生|女士|博士)',w.word))
                and (not proper or not re.fullmatch(r'(?:银行|金融|医药|消费|科技|白酒|地产|证券)'
                    r'(?:行业|板块|领域|产业)',w.word))
                and (w.flag.startswith('n') or (proper and w.flag in {'i','eng'})
                     or not proper and w.flag=='vn')]
        if proper:
            # Brand spellings can be idioms/English in the dictionary
            # (e.g. 可口可乐/NVIDIA). This is literal coverage, not NER or
            # factual approval; category exclusions and source review remain.
            names=[w.word for w in words if w.flag in {'nt','nz','nr','i','eng'} and w.word in tokens]
            return list(dict.fromkeys(names or tokens))[:6]
        # Prefer a named adjacent concept (e.g. two contiguous content words)
        # over a generic component. Never concatenate across source characters.
        compound=[a.word+b.word for a,b in zip(words,words[1:])
                  if a.word in tokens and b.word in tokens and len(a.word+b.word)<=10]
        return list(dict.fromkeys(compound or tokens))[:6]
    reason=selection.get('detail_quote','');named_reason=False
    if selection.get('kind')=='company_case':
        # A direct past-choice answer may supply either event or detail.
        # Prefer its explicitly named decision standard in either position;
        # do not force a background noun merely because the model swapped
        # two otherwise valid source quotes. Independent fact review remains.
        named_standard=r'(?:提出|采用|采取|按照|根据|遵循)[^。！？!?]{0,30}(?:理论|标准|策略|思路)|(?:理论|标准|策略|思路)[^。！？!?]{0,20}(?:是|叫|为)'
        for key in ('event_quote','detail_quote'):
            quote=selection.get(key,'')
            if re.search(named_standard,quote) and terms(quote):
                reason=quote
                named_reason=True
                break
    subjects=terms(selection.get('subject_quote',''),True) if selection.get('kind')=='company_case' else []
    if (selection.get('kind')=='personal_story'
            and re.search(r'退出|买入|卖出|持有|建仓|清仓|增持|减持|买了|卖了',selection.get('event_quote',''))):
        # A sector is NOT a named company, but it may be the literal object
        # of a genuine personal investment choice. Take exact event tokens,
        # never a company-name substring or the host's hypothetical premise.
        subjects=list(dict.fromkeys(w.word for w in jieba.posseg.cut(selection['event_quote'])
                                    if w.word in sectors))
    details=terms(reason)
    action_pairs=[]
    if selection.get('kind')=='personal_story':
        # A literal complete action phrase can be the reason; forcing only a
        # metaphor noun made real drafts repeat a counterfactual in three
        # clauses. Keep BOTH source actions. This is coverage, never entailment
        # or permission to drop negation/conditions in independent review.
        for phrase in re.findall(r'不[\u4e00-\u9fff]不[\u4e00-\u9fff]',reason):
            pair=[phrase[1],phrase[3]]
            if (pair[0]!=pair[1] and all(list(jieba.posseg.cut(word))[0].flag.startswith('v') for word in pair)):
                if pair not in action_pairs:action_pairs.append(pair)
                details=list(dict.fromkeys([phrase]+details))
    def background_terms(quote):
        # POS dictionaries tag some nominal variables as verbs (e.g. 的收入).
        # Recover only the local nominal construction, not opinion/qualifier
        # verbs whose removal could change uncertainty or speaker attribution.
        words=list(jieba.posseg.cut(quote))
        nominal=[w.word for i,w in enumerate(words) if i and words[i-1].word=='的'
            and w.flag in {'v','vn'} and 2<=len(w.word)<=10 and w.word not in generic]
        return list(dict.fromkeys(terms(quote)+nominal))
    background=list(dict.fromkeys(t for key in ('event_quote','detail_quote')
        if selection.get(key,'')!=reason for t in background_terms(selection.get(key,''))
        if t not in subjects and t not in details)) if named_reason else []
    result=dict(subjects=subjects,details=details,background_terms=background,
                **{'detail_actions_'+str(i):pair for i,pair in enumerate(action_pairs[:3])},
                source=[selection.get(k,'') for k in ('subject_quote','event_quote','detail_quote')])
    if holding_evidence(selection):
        result['past_holding_times']=[a['raw'] for a in holding_time_anchors(selection.get('event_quote',''))]
        result['holding_recall_source']=[selection['event_quote']]
        if future_holding_subject(selection.get('subject_quote')):
            result['holding_subject_context']=[selection['subject_quote']]
    return result


def title_focus_error(title,focus,speaker='林园',cover=None):
    if not isinstance(title,str):return '故事标题不是文本'
    body=title.removeprefix(speaker+'：')
    action_pairs=[v for k,v in focus.items() if k.startswith('detail_actions_')]
    if len(body)>28:return '故事标题把过多背景压进一句；请用具体选择加一条直接理由，不能截断或删必要条件'
    if focus.get('subjects') and not any(t in body[:12] for t in focus['subjects']):
        return '故事标题前半句必须写明本次具体对象：'+','.join(focus['subjects'])
    anchors=[a for quote in focus.get('past_holding_times',[]) for a in holding_time_anchors(quote)]
    presented=holding_time_anchors(body) if anchors else []
    if anchors and not any(a['value']==b['value'] and a['unit']==b['unit']
            and (not a['approximate'] or b['approximate']) for a in anchors for b in presented):
        return '本次标题须保留本人已发生持仓的原话时间/时长及近或约等限定，不能改成未来承诺：'+','.join(a['raw'] for a in anchors)
    if focus.get('holding_recall_source'):
        for copy in (body,str(cover or '')):
            if any(not any(a['value']==b['value'] and a['unit']==b['unit'] for a in anchors)
                   for b in holding_time_anchors(copy)):
                return '标题和封面不能在已发生持仓时长之外添加新的持有年限'
            if re.search(r'(?:至少|最少)[^。！？!?，,]{0,8}(?:年|个月|月)|(?:未来|以后|今后)[^。！？!?，,]{0,16}(?:年|个月|月)',copy):
                return '不能把主持人的未来最低持有年限当成嘉宾承诺；本次只写已发生持仓和当前选择'
            if re.search(r'除非|只要',copy) and not re.search(r'除非|只要',''.join(focus.get('source',[])[1:])):
                return '原话没有给出这个卖出触发条件，不能从平常心改写为除非或只要'
            for clause in re.split(r'[。！？!?；;，,]',copy):
                if re.search(r'终身|永久|永远|一辈子',clause) and not re.search(r'(?<!不)可能|或许|也许',clause):
                    return '这次是可能的远期持仓表态，标题和封面各自保留可能等限定，不能借主持人复述变成确定承诺'
    if (focus.get('details') and not any(t in body for t in focus['details'])
            and not any(all(term in body for term in pair) for pair in action_pairs)):
        return '故事标题须保留本次事例的实际细节或理由原词：'+','.join(focus['details'])
    for a,b in action_pairs:
        if re.search(r'除非[^。！？!?]*不'+re.escape(a)+'不'+re.escape(b)+
                     r'[^。！？!?]*否则[^。！？!?]*'+re.escape(a)+r'[^。！？!?]*'+re.escape(b),body):
            return '同一个动作理由不能正反重复：先说实际选择，再用一句完整理由；不删必要条件或改变主体'
    if any(t in body+'\n'+str(cover or '') for t in focus.get('background_terms',[])):
        return '本次已有明确命名的选择理由；标题和封面只保留具体选择及该理由，背景变量请留在正文：'+','.join(focus['background_terms'])
    # Real CPU drafts repeatedly collapsed an income-level background clause
    # into a new income-boom claim. Independent model reviews approved it.
    # Require an explicit original phrase for this stronger claim, not a word
    # from one clause concatenated with a growth metaphor from another.
    from title_rewrite import (income_boom_error,named_choice_boom_error,
                              lifetime_holding_hedge_error,rare_sale_scope_error)
    source=''.join(focus.get('source') or [])
    return (income_boom_error(title,cover,source) or named_choice_boom_error(title,cover,source)
            or lifetime_holding_hedge_error(title,cover,source) or rare_sale_scope_error(title,cover,source))


def company_subject_error(selection):
    """Addresses, sector labels and greetings cannot identify a company."""
    if not isinstance(selection,dict) or selection.get('kind') not in ('personal_story','company_case'):
        return 'Invalid grounded story kind'
    if selection['kind']=='company_case' and not title_focus(selection)['subjects']:
        return 'Named company subject required; greetings, people addresses and generic assets/rules are not a company case'
    return None


def proof_error(meta):
    if not isinstance(meta,dict):return 'Invalid treatment metadata'
    if meta.get('editorial_profile')!=TREATMENT:
        return 'No requested treatment profile'
    p=meta.get('experiment_proof') or {};cover=meta.get('cover_proof') or {}
    if not isinstance(p,dict) or not isinstance(cover,dict):return 'Invalid treatment proof'
    s=p.get('selection') or {}
    if not isinstance(s,dict):return 'Invalid story evidence'
    try:
        if (p.get('version')!=VERSION or p.get('profile')!=TREATMENT
                or not MIN_MOTHER_SECONDS<=float(p['source_duration_sec'])<=5400
                or s.get('version')!=VERSION or s.get('kind') not in ('personal_story','company_case')
                or not all(isinstance(s.get(k),str) and minimum<=len(s[k])<=140 and _substantive_quote(s[k],minimum)
                           and (k=='subject_quote' or not re.search(r'[？?]',s[k]))
                           for k,minimum in (('subject_quote',2),('event_quote',4),('detail_quote',4)))
                or not re.fullmatch('[a-f0-9]{64}',str(s.get('transcript_sha256') or ''))
                or s['event_quote']==s['detail_quote']):
            return 'Missing long-mother or grounded story evidence'
    except (TypeError,ValueError,KeyError):return 'Invalid story proof fields'
    if s['kind']=='personal_story':
        issue=personal_event_error(s['event_quote']) or personal_detail_error(s['detail_quote'])
        if issue:return issue
    focus=title_focus(s)
    company_issue=company_subject_error(s)
    if company_issue:return company_issue
    holding=future_holding_subject(s['subject_quote']) and s['kind']=='company_case'
    if holding and not holding_evidence(s):return 'Missing strict past-holding source evidence'
    if holding or 'company_holding_recall_version' in s or 'company_holding_evidence_version' in s:
        if (not holding_evidence(s) or any(type(s[k]) is not int or s[k]!=1 for k in
                ('company_holding_recall_version','company_holding_evidence_version') if k in s)
                or s['kind']!='company_case' or not company_holding_question(s['subject_quote'])):
            return 'Invalid company holding recall proof'
        issue=company_past_event_error(s['event_quote'])
        issue=issue or company_holding_detail_error(s['detail_quote'])
        if s['event_quote'] in s['detail_quote'] or s['detail_quote'] in s['event_quote']:
            issue=issue or 'Past holding evidence repeats the same source clause'
        if issue:return issue
    focus_issue=title_focus_error(meta.get('title'),focus,cover=meta.get('cover_title'))
    if focus_issue:return focus_issue
    for key,actual in (('title',meta.get('title')),('source_sha256',meta.get('source_sha256')),
                       ('video_sha256',(meta.get('fingerprints') or {}).get('sha256')),
                       ('cover_sha256',cover.get('sha256'))):
        if not actual or p.get(key)!=actual or (key!='title' and not re.fullmatch('[a-f0-9]{64}',str(actual))):
            return 'Story proof binding mismatch: '+key
    if (meta.get('cover_person_image_source')!='verified_source_frame'
            or meta.get('cover_person_image_verified') is not True
            or cover.get('style')!='scene' or cover.get('no_added_text') is not True
            or cover.get('feed_safe_face') is not True):
        return 'Treatment requires an approved natural scene cover, not a stock-portrait fallback'
    return None


def actual_profile(meta):
    return TREATMENT if not proof_error(meta) else BASELINE


def family_metadata(source_sha256,seconds):
    if not re.fullmatch('[a-f0-9]{64}',str(source_sha256 or '')) or not MIN_MOTHER_SECONDS<=seconds<=5400:
        raise ValueError('Invalid long-mother family evidence')
    return dict(version=VERSION,mother_sha256=source_sha256,duration_sec=seconds)


def full_clip_pair(current,previous,preflight=False):
    """Only the explicit long version and a contained story excerpt may pair.

    Never exempt two clips, two fulls, equal files, unknown historical ranges,
    changed mothers or merely similar titles. All media gates still run.
    """
    a=current.get('long_story_family');b=previous.get('long_story_family')
    if not isinstance(a,dict) or a!=b or a.get('version')!=VERSION:return False
    source=a.get('mother_sha256')
    if (not re.fullmatch('[a-f0-9]{64}',str(source or ''))
            or current.get('source_sha256')!=source or previous.get('source_sha256')!=source):return False
    current_hash=(current.get('fingerprints') or {}).get('sha256')
    previous_hash=(previous.get('fingerprints') or {}).get('sha256')
    if not re.fullmatch('[a-f0-9]{64}',str(previous_hash or '')):return False
    if not (preflight and not current_hash) and not re.fullmatch('[a-f0-9]{64}',str(current_hash or '')):return False
    if current_hash==previous_hash:return False
    fulls=[p for p in (current,previous) if p.get('content_type')=='full_interview']
    if len(fulls)!=1:return False
    full=fulls[0];clip=previous if full is current else current
    if clip.get('editorial_profile')!=TREATMENT:return False
    def span(p):
        rows=p.get('segments') or p.get('source_segments') or []
        if len(rows)!=1:raise ValueError()
        lo,hi=float(rows[0]['start']),float(rows[0]['end'])
        if not math.isfinite(lo+hi) or not 0<=lo<hi:raise ValueError()
        return lo,hi
    try:
        fa,fb=span(full);ca,cb=span(clip);duration=float(a['duration_sec'])
        return (MIN_MOTHER_SECONDS<=duration<=5400 and fb<=duration+1
                and MIN_MOTHER_SECONDS<=fb-fa and 20<=cb-ca<=330
                and fa<=ca and cb<=fb and fb-fa>=3*(cb-ca))
    except (KeyError,TypeError,ValueError):return False


def comparison_state(meta,state,preflight=False):
    """Keep all history except the exact authorized cross-format partner."""
    published={};changed=False
    for slug,info in (state.get('published') or {}).items():
        rows=info.get('parts') or ([info] if info.get('bvid') else [])
        kept=[p for p in rows if not full_clip_pair(meta,p,preflight=preflight)]
        if len(kept)==len(rows):published[slug]=info
        else:
            changed=True
            if kept:published[slug]={**info,'parts':kept}
    return {**state,'published':published} if changed else state


def rank(meta, plan):
    if not plan:return 0
    return int(actual_profile(meta)!=plan['planned'])


def workflow_input(entry):
    return {'editorial_profile':TREATMENT} if entry.get('editorial_profile')==TREATMENT else {}


def long_candidate(candidate):
    extra=candidate.get('extra') or {}
    if isinstance(extra,str):
        try:extra=json.loads(extra)
        except ValueError:return False
    try:return MIN_MOTHER_SECONDS<=float(extra.get('duration') or 0)<=5400
    except (ValueError,TypeError,AttributeError):return False


def production_deficits(inventory,state,active_runs,now=None):
    if not preparing(now) or 'experiment_ready' not in inventory:return dict(portrait=0,landscape=0)
    ready=inventory.get('experiment_ready') or {}
    wide=int(ready.get('landscape') or 0);portrait=int(ready.get('portrait') or 0)
    # Two days of real T: afternoon needs native wide; evening already accepts
    # native wide as well as portrait. Do not force a crop just to fill a label.
    needs=dict(landscape=max(0,2-wide))
    needs['portrait']=max(0,4-wide-portrait-needs['landscape'])
    latest={e.get('slug'):e for e in state.get('dispatched') or []}
    seen=set()
    for run in active_runs:
        slug=str(run.get('display_title') or '').split(' · ')[-1]
        entry=latest.get(slug) or {};layout=entry.get('output_layout')
        if (run.get('status') in ('queued','in_progress') and slug not in seen
                and entry.get('editorial_profile')==TREATMENT and layout in needs):
            consume_production_need(needs,layout);seen.add(slug)
    return needs


def consume_production_need(needs,layout):
    if layout=='landscape' and not needs.get('landscape'):
        layout='portrait'  # Flexible evening draft; still actual native-wide T.
    if layout in needs:needs[layout]=max(0,needs[layout]-1)


def receipt(meta, now=None, makeup_slot=None):
    plan=assignment(now,makeup_slot)
    if not plan:return None
    actual=actual_profile(meta)
    size=meta.get('resolution') or {}
    width,height=size.get('width'),size.get('height')
    layout=('landscape' if width>height else 'portrait') if (
        type(width) is int and type(height) is int and min(width,height)>0 and width!=height) else None
    return {**plan,'actual':actual,'compliant':actual==plan['planned'],
            'reason':None if actual==plan['planned'] else 'qualified_stock_fallback',
            'layout':layout,
            'layout_fallback':('native_treatment_for_evening' if actual==TREATMENT
                               and plan['slot_number']==4 and layout=='landscape' else None),
            'content_type':meta.get('content_type'),
            'source_duration_sec':(meta.get('experiment_proof') or {}).get('source_duration_sec'),
            'selection_kind':((meta.get('experiment_proof') or {}).get('selection') or {}).get('kind')}


def observations(items, previous, state, now=None):
    """Actual receipt-defined groups; missing views never become zero plays."""
    now=time.time() if now is None else now
    old=previous.get('publishing_experiment') or {}
    # Publication receipts are immutable observations. A temporary state-read
    # failure must not erase the month already collected by earlier runs.
    receipts={r['bvid']:dict(r) for r in old.get('receipts') or []
              if isinstance(r,dict) and r.get('bvid') and r.get('experiment_id')==EXPERIMENT_ID}
    for info in (state.get('published') or {}).values():
        for p in info.get('parts') or []:
            r=p.get('publishing_experiment')
            if p.get('bvid') and isinstance(r,dict) and r.get('experiment_id')==EXPERIMENT_ID:
                receipts[p['bvid']]={**r,'bvid':p['bvid'],'title':p.get('title'),
                    'published_at':p.get('ts'),'duration_sec':p.get('duration_sec'),
                    'source_url':info.get('source_url'),'source_sha256':p.get('source_sha256'),
                    'render_mode':p.get('render_mode'),'resolution':p.get('resolution')}
    samples={}
    for r in old.get('samples') or []:
        if r.get('bvid') in receipts and r.get('window') in (24,72,168):
            samples[(r['bvid'],r['window'])]=dict(r)
    for item in items:
        b=item.get('bvid');pub=item.get('pubdate');play=item.get('play')
        if b not in receipts or type(play) is not int or play<0 or type(pub) not in (int,float):continue
        age=(now-pub)/3600
        for window in (24,72,168):
            key=b,window
            if window<=age<window+12 and key not in samples:
                r=receipts[b]
                samples[key]=dict(bvid=b,window=window,observed_at=int(now),age_hours=round(age,3),
                    play=play,like=item.get('like'),danmaku=item.get('danmaku'),
                    planned=r['planned'],actual=r['actual'],compliant=r['compliant'],
                    slot_number=r['slot_number'],content_type=r.get('content_type'))
    groups=[]
    for planned in (BASELINE,TREATMENT):
        for window in (24,72,168):
            rows=[r for r in samples.values() if r['planned']==planned and r['window']==window
                  and r['compliant'] and r.get('content_type')!='full_interview']
            plays=[r['play'] for r in rows]
            groups.append(dict(planned=planned,window=window,count=len(rows),
                mean=round(statistics.mean(plays),2) if plays else None,
                median=statistics.median(plays) if plays else None))
    by_slot=[]
    for slot in (1,2,3,4):
        for window in (24,72,168):
            plays=[r['play'] for r in samples.values() if r['slot_number']==slot and r['window']==window
                   and r['compliant'] and r.get('content_type')!='full_interview']
            by_slot.append(dict(slot_number=slot,window=window,count=len(plays),
                mean=round(statistics.mean(plays),2) if plays else None,
                median=statistics.median(plays) if plays else None))
    local=datetime.fromtimestamp(now,CST).date().isoformat()
    days=max(0,min(30,(date.fromisoformat(local)-date.fromisoformat(START)).days))
    receipts_by_slot={r['slot']:r for r in receipts.values()}
    daily=[]
    for day in range(30):
        value=date.fromisoformat(START)+timedelta(days=day)
        due=[f'{value.isoformat()} {h:02d}' for h in HOURS
             if datetime(value.year,value.month,value.day,h,tzinfo=CST).timestamp()<=now]
        if not due:continue
        present=[receipts_by_slot[s] for s in due if s in receipts_by_slot]
        daily.append(dict(date=value.isoformat(),slots_due=len(due),published=len(present),
                          missing_slots=[s for s in due if s not in receipts_by_slot],
                          compliant_treatment=sum(r['planned']==TREATMENT and r['compliant'] for r in present),
                          fallback=sum(not r['compliant'] for r in present)))
    return dict(experiment_id=EXPERIMENT_ID,version=VERSION,start=START,end_exclusive=END_EXCLUSIVE,
        status='scheduled' if local<START else 'collecting' if local<END_EXCLUSIVE else 'followup',
        completed_calendar_days=days,review_due=local>=END_EXCLUSIVE,
        daily=daily,
        pending_uploads=[dict(slug=e.get('slug'),upload_intent_ts=e.get('uploading_ts'),
                             assignment=e['upload_experiment'])
                         for e in state.get('dispatched') or []
                         if e.get('uploading') and e.get('upload_experiment')],
        receipts=sorted(receipts.values(),key=lambda r:r['slot']),
        fallback_count=sum(not r['compliant'] for r in receipts.values()),
        samples=sorted(samples.values(),key=lambda r:(r['bvid'],r['window'])),groups=groups,by_slot=by_slot,
        scope='Fixed Beijing slots 1/3 baseline, 2/4 treatment; not randomized. '
              'Search-index cumulative views at ages 24–36h / 72–84h / 168–180h; '
              'not CTR, impressions, retention or causal uplift. Missing is null. '
              'Fallbacks and full interviews excluded from primary clip groups; '
              'slot/time, layout, topic, source and duration remain confounders.')
