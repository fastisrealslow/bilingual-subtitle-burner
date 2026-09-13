"""Source-grounded claim titles: propose angles, review them, never list keywords."""
import difflib
import hashlib
import json
import re

VERSION = 2026091303
CHECKS = ('source_supported', 'central_point', 'attribution_correct',
          'preserves_qualifiers', 'cover_consistent', 'readable')


def compact(text):
    return re.sub(r'[^0-9A-Za-z\u4e00-\u9fff%％.]', '', str(text or ''))


def summary_heading(title):
    body = re.sub(r'^[^：:]+[：:]', '', str(title or ''))
    return bool(re.match(r'(?:谈|浅谈|关于|聊聊|解读|漫谈).{0,50}(?:与|和|、|讨论|投资|需求|时机)', body)
                or re.search(r'公开讨论$|投资(?:逻辑|哲学|理念)与|机遇与挑战', body))


def _json(text):
    match = re.search(r'\{.*\}', text, re.S)
    return json.loads(match.group(0) if match else text)


def source_units(transcript):
    """Number exact source spans; the model selects evidence instead of retyping it."""
    spans = re.findall(r'.+?(?:[。！？!?；;\n]|$)', transcript, re.S)
    units, current = [], ''
    for span in spans:
        for start in range(0, len(span), 100):
            chunk = span[start:start + 100]
            if current and len(current) + len(chunk) > 120:
                units.append(current)
                current = ''
            current += chunk
            if len(compact(current)) >= 30:
                units.append(current)
                current = ''
    if current:
        if units and len(compact(current)) < 8:
            units[-1] += current
        else:
            units.append(current)
    if ''.join(units) != transcript:
        raise ValueError('原文证据分组改变了字幕')
    return units


def subject_catalog(units):
    """Offer exact source nouns, never model-invented compound evidence anchors."""
    from collections import Counter
    import jieba.posseg
    counts = Counter(word for word, tag in jieba.posseg.cut(''.join(units))
                     if tag.startswith(('n', 'vn')) and 2 <= len(compact(word)) <= 8)
    return {word:[i for i, unit in enumerate(units) if word in unit]
            for word, _ in counts.most_common(48)}


def proposal_schema(unit_count, subjects=None, guest_ids=None):
    # Resolve the speakers' meaning before selecting evidence IDs or writing
    # attractive copy. Runs 145/147 otherwise anchored on a host's hypothesis
    # and repeated it in all three drafts despite accurate rejection feedback.
    # Prefix dependent stages: schema serialization/grammar can sort map keys.
    # A 'focus' object sorts AFTER 'candidates', and 'reason' sorts after all
    # boolean review decisions. In run 150 this produced fluent but false
    # attribution even though both model-generated reviews said "passed".
    fields = {'a_claim':dict(type='string')}
    turn_fields=dict(a_start=dict(type='integer',minimum=0,maximum=max(0,unit_count-1)),
        b_end=dict(type='integer',minimum=0,maximum=max(0,unit_count-1)),
        c_role=dict(type='string',enum=['host','guest','unknown']))
    reading = dict(a_turns=dict(type='array',minItems=1,maxItems=min(120,unit_count),
        items=dict(type='object',additionalProperties=False,required=list(turn_fields),properties=turn_fields)),
        b_question_premise=dict(type='string'),c_guest_answer=dict(type='string'))
    fields['b_evidence_ids'] = dict(type='array', minItems=1, maxItems=4, uniqueItems=True,
        items=dict(type='integer', minimum=0, maximum=max(0, unit_count - 1)))
    # The local grammar padded minimum-length strings with spaces/newlines.
    # Check meaningful characters in Python and feed back the exact problem.
    copies=dict(title=dict(type='string'),cover_title=dict(type='string'))
    schema=dict(type='object', additionalProperties=False, required=['a_reading','b_focus','c_candidates'], properties={
        'a_reading':dict(type='object',additionalProperties=False,required=list(reading),properties=reading),
        'b_focus':dict(type='object',additionalProperties=False,required=list(fields),properties=fields),
        'c_candidates':dict(type='array', minItems=3, maxItems=3,
            items=dict(type='object', additionalProperties=False, required=list(copies), properties=copies))})
    if guest_ids is not None:
        schema['required'].remove('a_reading');schema['properties'].pop('a_reading')
        fields['b_evidence_ids']['items']=dict(type='integer',enum=list(guest_ids))
    return schema


def reading_schema(unit_count):
    turn=dict(a_start=dict(type='integer',minimum=0,maximum=unit_count-1),
              b_role=dict(type='string',enum=['host','guest','unknown']))
    fields=dict(a_turn_starts=dict(type='array',minItems=1,maxItems=unit_count,
        items=dict(type='object',additionalProperties=False,required=list(turn),properties=turn)),
        b_question_premise=dict(type='string'),c_guest_answer=dict(type='string'))
    return dict(type='object',additionalProperties=False,required=list(fields),properties=fields)


def bind_reading(reading, units):
    if (not isinstance(reading,dict)
            or len(compact(reading.get('c_guest_answer')))<12
            or len(compact(reading.get('b_question_premise')))<4):
        raise ValueError('先分别读清嘉宾实际回答与主持人的问题前提，不能直接凭关键词写标题')
    starts=reading.get('a_turn_starts')
    if not isinstance(starts,list) or not starts:
        raise ValueError('先标出真实说话人切换的位置')
    turns=[]
    for i,row in enumerate(starts):
        if not isinstance(row,dict):raise ValueError('说话人切换格式无效')
        start=row.get('a_start')
        end=starts[i+1].get('a_start') if i+1<len(starts) and isinstance(starts[i+1],dict) else len(units)
        if type(start) is not int or type(end) is not int:
            raise ValueError('说话人切换编号必须是整数')
        turns.append(dict(a_start=start,b_end=end-1,c_role=row.get('b_role')))
    return bind_turns(turns,units)


def guest_evidence_ids(units, roles):
    # The 818 real draft repeatedly selected short ASR cues, then failed the
    # unchanged eight-character evidence check. Do not offer impossible IDs to
    # the model; retain every short cue in the full reading/review context.
    return [i for i,role in enumerate(roles) if role=='guest' and len(compact(units[i]))>=8]


def review_schema(candidate_count):
    fields = {name:dict(type='boolean') for name in CHECKS}
    fields.update(index=dict(type='integer', minimum=0, maximum=candidate_count - 1),
                  appeal=dict(type='integer', minimum=1, maximum=5))
    return dict(type='object', additionalProperties=False, required=['reviews'], properties={
        'reviews':dict(type='array', minItems=candidate_count, maxItems=candidate_count,
            items=dict(type='object', additionalProperties=False, required=['a_analysis','b_verdict'], properties={
                'a_analysis':dict(type='object',additionalProperties=False,
                    required=['a_guest_answer','b_question_premise','c_reason'],properties={
                        k:dict(type='string') for k in ('a_guest_answer','b_question_premise','c_reason')}),
                'b_verdict':dict(type='object',additionalProperties=False,required=list(fields),properties=fields)}))})


def bind_evidence(item, units):
    if not isinstance(item, dict):
        raise ValueError('候选不是JSON对象')
    ids = item.get('evidence_ids')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= 4 or len(set(ids)) != len(ids)
            or any(type(i) is not int or not 0 <= i < len(units) for i in ids)):
        raise ValueError('原文证据编号无效')
    if 'evidence' in item:
        raise ValueError('证据必须从编号取回，不能由模型另写原文')
    return {**item, 'evidence':[units[i] for i in sorted(ids)]}


def bind_candidate(item, focus, units, subjects):
    title=re.sub(r'\s+',' ',str(item.get('title') or '')).strip()
    cover=re.sub(r'\s+',' ',str(item.get('cover_title') or '')).strip()
    bound=bind_evidence(dict(title=title,cover_title=cover,evidence_ids=focus.get('evidence_ids')),units)
    # Derive the exact shared anchor instead of asking the model to perform
    # literal string matching. The full claim still needs independent review.
    anchors=[word for word in subjects if word in title and any(word in q for q in bound['evidence'])]
    bound['subject']=max(anchors,key=len) if anchors else ''
    return bound


def _candidate_error(item, transcript, speaker, existing_titles, check_layout=True):
    title, cover = item.get('title'), item.get('cover_title')
    if not isinstance(title, str) or not title.startswith(speaker + '：'):
        return '标题缺少主讲人前缀'
    if summary_heading(title):
        return '标题必须呈现一个具体观点，不能是主题目录或残句'
    if not 12 <= len(compact(title)) <= 62:
        return f'标题有效字数为{len(compact(title))}，须12~62字；补全具体判断或问题，不用空格凑长度'
    if not isinstance(cover, str) or not 8 <= len(compact(cover)) <= 18:
        return f'封面有效字数为{len(compact(cover))}，须8~18字；写成完整问题或判断，不能用空格补长度'
    if check_layout:
        from headline_policy import cover_fits
        if not cover_fits(cover):
            return '封面短标题不能截断词语'
    if re.search(r'必涨|稳赚|翻倍秘籍|震惊|暴富|内幕曝光|不看后悔|http|@', title + cover):
        return '标题含收益诱导或空洞夸张'
    if any(difflib.SequenceMatcher(None, compact(title), compact(old)).ratio() >= .84 for old in existing_titles):
        return '标题与已有稿件重复'
    evidence = item.get('evidence')
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 4:
        return '必须提供支撑观点的完整原文句子'
    source = compact(transcript)
    if any(not isinstance(q, str) or len(compact(q)) < 8 or compact(q) not in source for q in evidence):
        return '观点证据不是这段真实原文'
    # An observed 4B-model false positive inferred "更安全" from position sizing.
    # Such financial claims need explicit evidence even if a reviewer says true.
    risk_claims=('更安全','更稳妥','更稳健','风险更低','风险小','降低风险','避险',
                 '收益更高','回报更高','更赚钱','最赚钱','稳赚','保证收益')
    stated=compact(''.join(evidence))
    if any(term in compact(title+cover) and term not in stated for term in risk_claims):
        return '标题新增了原文没有的安全性或收益比较结论'
    subject = item.get('subject')
    if not isinstance(subject, str) or not 2 <= len(compact(subject)) <= 12:
        return '缺少具体讨论对象'
    if compact(subject) not in compact(title) or not any(compact(subject) in compact(q) for q in evidence):
        return '标题对象与原文证据不对应'
    # The semantic review also checks written numbers, attribution and negation.
    for number in re.findall(r'\d+(?:\.\d+)?[%％]?', title + cover):
        if number not in re.findall(r'\d+(?:\.\d+)?[%％]?', transcript):
            return '标题或封面添加了原文没有的数字'
    return None


def _binding(title, cover, subject, evidence):
    return hashlib.sha256(json.dumps([title, cover, subject, evidence], ensure_ascii=False).encode()).hexdigest()


def _package(item, transcript, review, candidates):
    title, cover = item['title'], item['cover_title']
    review = {**review, 'copy_sha256':_binding(title, cover, item['subject'], item['evidence'])}
    proof = dict(version=VERSION, kind='editorial_claim', title=title, cover=cover,
                 subject=item['subject'], evidence=item['evidence'], review=review,
                 source_sha256=hashlib.sha256(compact(transcript).encode()).hexdigest())
    return dict(title=title, cover_title=cover, title_rewrite=proof,
                title_candidates=[c['title'] for c in candidates], title_quality_verified=True,
                packaging_method='source_claim_editor', desc='本段讨论：' + title.split('：', 1)[1])


def _extractive(transcript, speaker, existing_titles, preferred=None):
    from headline_policy import title_candidates, body, cover_copy, complete
    titles = title_candidates(transcript, speaker, existing_titles)
    if preferred and preferred not in titles:
        titles.append(preferred)
    for title in titles:
        quote = body(title, speaker)
        if not complete(quote) or compact(quote) not in compact(transcript) or summary_heading(title):
            continue
        cover = cover_copy(title, transcript, speaker)
        if cover.get('reason') == 'needs_editorial_copy':
            continue
        # Whole source claims are the fallback; not a list of detected subjects.
        item = dict(title=title, cover_title=cover['text'], evidence=[quote], subject=quote)
        if not 12 <= len(compact(title)) <= 62:
            continue
        return _package(item, transcript, dict(method='source_quote', quote=quote), [item])
    raise ValueError('未提炼出有原文支撑的完整观点标题；保留素材和转写，等待标题重试')


def bind_turns(turns, units):
    """Keep every original cue and reject overlaps, omissions, or invented roles."""
    if not isinstance(turns,list) or not turns:
        raise ValueError('先按原始字幕边界区分完整问答轮次')
    roles=[]
    for turn in turns:
        start,end=turn.get('a_start'),turn.get('b_end');role=turn.get('c_role')
        if (type(start) is not int or type(end) is not int or start!=len(roles)
                or end<start or end>=len(units) or role not in ('host','guest','unknown')):
            raise ValueError('问答轮次必须按顺序完整覆盖字幕，不能遗漏或交叠')
        roles.extend([role]*(end-start+1))
    if len(roles)!=len(units) or 'guest' not in roles:
        raise ValueError('问答轮次未覆盖全部字幕或没有明确嘉宾回答')
    return roles


def generate(transcript, speaker='林园', existing_titles=(), model=None, preferred=None,
             structured_model=None, source_cues=None):
    if model is None and structured_model is None:
        return _extractive(transcript, speaker, existing_titles, preferred)
    units = list(source_cues) if source_cues else source_units(transcript)
    if any(not isinstance(u,str) for u in units) or ''.join(units)!=transcript:
        raise ValueError('标题原始字幕边界与完整原文不一致')
    subjects = subject_catalog(units) if structured_model else {}
    def call(prompt, schema):
        return structured_model(prompt, schema) if structured_model else model(prompt)
    reader_prompt=f'''只做访谈原文阅读，不拟标题，不比较吸引力。主讲嘉宾是{speaker}。
先通读全部字幕，找主持人的完整问题和嘉宾实际回复。主持人提问前的背景、假设、举例仍属于主持人；嘉宾短答不等于确认问题全部前提。
同一人的连续讲话是一个轮次，不能因为换了一条字幕或出现问号就换说话人。字幕标点可能不准，必须连贯理解上下文。
在a_turn_starts只写真正换说话人的起始编号：从0开始，严格递增；a_start是这一轮开始的字幕编号，b_role是host、guest或unknown。
下一轮开始前的字幕自动全部属于当前轮次，最后一轮持续到全文末尾；不要计算结束编号，不要逐句交替标host和guest。
“您觉得”“你怎么看”通常是主持人的提问；“我听懂了”“我来总结”通常是主持人复述，不能当嘉宾原话。
再用b_question_premise概括主持人问题和未确认假设，用c_guest_answer概括嘉宾明确回答的主要判断及限定条件。
没有主持人时写“无主持人提问”。必须阅读所有字幕，不因某段提问较长就把它当嘉宾观点。只输出JSON。
按原顺序编号的完整字幕：{json.dumps(dict(enumerate(units)),ensure_ascii=False)}'''
    reading=None;roles=None
    prompt = f'''你是B站视频编辑，要写自然、有看点、忠于访谈的中文标题。
先分清主持人的提问、猜测与嘉宾已经回答的内容。中心观点按嘉宾回答的信息量选择，不能按主持人的发言长度或关键词频率选择。
嘉宾没有确认的新品表现、未来变化和问题前提，不能写成嘉宾的观点。优先写嘉宾明确表达的观察和判断，保留转折后的限定条件。
问答轮次已由单独的原文阅读步骤划分，不能为了写标题而改动说话人。只能根据列出的嘉宾原话选择中心观点。
再在b_focus.a_claim用一句完整的话写出上述嘉宾回答里信息最充分的核心判断、做法及限定条件，
用b_focus.b_evidence_ids选1~4条支撑它的guest原文编号；不能选host或unknown。不要把主持人的猜测或一处举例当成中心观点。
最后在c_candidates为同一观点写3个不同角度的标题，可突出具体选择、反常识判断或这段确实回答的问题。
每条title以“{speaker}：”开头，正文15~30个汉字；cover_title为8~18个汉字，不加姓名。
封面建议写12~16个汉字的完整问题或判断，避免只有六七个字的短标签。
每条标题必须明确说出讨论对象，并包含至少一个嘉宾原文对象词：__TITLE_SUBJECTS__。
保留这些词本身及其关系，不把原文对象换成“潜力股”等含义不同的金融标签。
用日常说话的完整句子。禁止“谈A、B与C”等关键词目录，禁止术语堆砌、换行、空格和无意义尾巴凑字数。
例如原文说“利润涨了但货款收不回，暂时不买”，标题可以问“利润在增长，为什么还要先看回款？”
这个例子只说明文风，不能套用它的事实。不得夸大收益、安全性、因果或删掉否定和不确定性。
只输出JSON。下面是按原顺序编号的完整字幕，未删改：
{json.dumps(dict(enumerate(units)), ensure_ascii=False)}'''
    last_error = ''
    for attempt in range(3):
        try:
            # Finish with the source, not three repetitions of a rejected claim.
            # In run 147 the retry feedback outweighed the actual guest answer
            # and the same host hypothesis returned in every round.
            retry_note = (f'第{attempt + 1}轮重新阅读；以下是已退回的错误稿，不能当作原文事实：'
                          + last_error + '\n\n请回到下面完整原文重新判断：\n' if last_error else '')
            if structured_model and reading is None:
                proposed_reading=_json(call(retry_note+reader_prompt,reading_schema(len(units))))
                roles=bind_reading(proposed_reading,units)
                reading=proposed_reading
            guest_ids=guest_evidence_ids(units,roles) if structured_model else []
            if structured_model and not guest_ids:
                reading=None
                raise ValueError('嘉宾回答中没有达到原文证据长度的条目，不能选主持人或短语凑证据')
            if structured_model:
                subjects={word:[i for i in ids if i in guest_ids] for word,ids in subject_catalog(units).items()}
                subjects={word:ids for word,ids in subjects.items() if ids}
            draft_prompt=prompt.replace('__TITLE_SUBJECTS__',json.dumps(list(subjects),ensure_ascii=False) if subjects else '用原文中的讨论对象')
            if structured_model:
                draft_prompt+='\n已单独读清的嘉宾回答：'+reading['c_guest_answer']
                draft_prompt+='\n主持人问题背景（不可当作嘉宾判断）：'+reading['b_question_premise']
                draft_prompt+='\n唯一可选的嘉宾原话及原始编号：'+json.dumps({i:units[i] for i in guest_ids},ensure_ascii=False)
            proposal = _json(call(retry_note + draft_prompt,
                                  proposal_schema(len(units), subjects,guest_ids if structured_model else None)))
            candidates = proposal.get('c_candidates' if structured_model else 'candidates')
            if not isinstance(candidates, list) or len(candidates) != 3:
                raise ValueError('必须提供三个不同角度的候选标题')
            # A structured production call never accepts model-authored evidence.
            if structured_model:
                raw_focus=proposal.get('b_focus') or {}
                focus=dict(claim=raw_focus.get('a_claim'),evidence_ids=raw_focus.get('b_evidence_ids'))
                if not isinstance(focus.get('claim'),str) or len(compact(focus['claim']))<12:
                    raise ValueError('先写清有原文依据的中心判断和限定条件，再写标题')
                focus_ids=focus.get('evidence_ids') or []
                if any(type(i) is not int or not 0<=i<len(units) or roles[i]!='guest' for i in focus_ids):
                    raise ValueError('标题证据选中了主持人提问或未知归属，必须回到嘉宾实际回答重写')
                candidates = [bind_candidate(c,focus,units,subjects) for c in candidates]
            errors = [(_candidate_error(c, transcript, speaker, existing_titles)
                       if isinstance(c, dict) else '候选不是JSON对象') for c in candidates]
            valid = [c for c, issue in zip(candidates, errors) if not issue]
            if len(valid) != 3:
                issues=[f"{c.get('title','')} / {c.get('cover_title','')}"
                        f"（对象={c.get('subject')},证据编号={c.get('evidence_ids')}）：{issue}"
                        for c,issue in zip(candidates,errors) if issue]
                raise ValueError('三个角度均须合格再比较；需修正：' + '；'.join(issues))
            if len({compact(c['title']) for c in valid})!=3:
                raise ValueError('三个标题必须有不同看点，不能重复同一句话')
            dialogue_context = (json.dumps([dict(id=i,role=roles[i],text=u) for i,u in enumerate(units)],
                                ensure_ascii=False) if structured_model else transcript)
            judge = f'''独立核对这些视频标题与完整字幕，只评价下方实际候选的标题和封面，不重做选段或字幕审核。
先完成a_analysis：a_guest_answer只概括嘉宾实际回答，b_question_premise列出主持人的问题前提；
然后c_reason引用本候选实际出现的短语，与原文中对应的嘉宾回答比较。最后才填b_verdict中的各个判定。
不得指出候选没有写过的词，不能空填全部true。原文出现某个词不能证明是嘉宾的观点，主持人长段提问中的假设也不算嘉宾确认。
区分主持人提问中的猜测和嘉宾明确给出的回答，标题不能把前者归为嘉宾观点。
不合格时说明原文实际的做法或判断，再指出标题偏差，供下一轮修正中心观点和措辞。
严格寻找实际标题/封面新增的比较、因果、收益和安全性判断。只有候选确实写出了新增判断，才能以此判source_supported=false。
保留原文中的否定、程度、转折和不确定性，不把有限的肯定扩大成整体乐观，不改变讨论对象间的关系。
拗口的术语堆砌、主体关系错误、把有限的肯定扩大成整体乐观，分别判readable、source_supported、preserves_qualifiers=false。
逐条检查：source_supported原文支持；central_point抓住中心而不是举例或旁枝；
attribution_correct没有把主持人的猜测归为嘉宾断言；preserves_qualifiers保留条件否定和不确定性；
cover_consistent封面和标题同一观点且没有更强断言；readable自然好懂。
appeal按具体看点和想点开的程度评1~5，空泛目录只能1分。严格输出布尔值，不因文字流畅而放过编造。
返回JSON的reviews数组，每项先a_analysis（a_guest_answer、b_question_premise、c_reason），
再b_verdict（index、source_supported、central_point、attribution_correct、preserves_qualifiers、cover_consistent、readable、appeal）。
候选及从原文直接取回的依据：{json.dumps([dict(title=c['title'],cover_title=c['cover_title'],evidence=c['evidence']) for c in valid], ensure_ascii=False)}
原文编号只帮助定位，依据中也可能含主持人的问题，必须与上下文分清说话人。若嘉宾确实说出了某个判断，不能仅因主持人也提到它就判归属错误。
下方保留每条原始字幕的边界及文本推断的说话轮次。先独立检查轮次归属是否合理，发现归属错误判attribution_correct=false。
只用guest真正说出的内容支撑标题事实；host只提供问题背景，不能把未被回答确认的假设写进标题。
完整字幕：{dialogue_context}'''
            reviews = _json(call(judge, review_schema(len(valid)))).get('reviews', [])
            if structured_model:
                normalized=[]
                for row in reviews:
                    analysis=row.get('a_analysis') or {}; verdict=row.get('b_verdict') or {}
                    if (not isinstance(analysis,dict) or not isinstance(verdict,dict)
                            or len(compact(analysis.get('a_guest_answer')))<12
                            or len(compact(analysis.get('b_question_premise')))<4
                            or len(compact(analysis.get('c_reason')))<12):
                        raise ValueError('独立复核必须先说明嘉宾回答、主持人问题和本候选的原文依据')
                    normalized.append({**verdict,'reason':analysis['c_reason'],'source_reading':analysis})
                reviews=normalized
            accepted = []
            for row in reviews:
                if (isinstance(row, dict) and type(row.get('index')) is int and 0 <= row['index'] < len(valid)
                        and all(row.get(k) is True for k in CHECKS)
                        and isinstance(row.get('reason'),str) and len(compact(row['reason'])) >= 12
                        and type(row.get('appeal')) is int and 3 <= row['appeal'] <= 5):
                    accepted.append(row)
            if not accepted:
                if structured_model and any(row.get('attribution_correct') is not True for row in reviews if isinstance(row,dict)):
                    reading=None
                feedback=[]
                for row in reviews:
                    if not isinstance(row,dict):continue
                    i=row.get('index')
                    if type(i) is not int or not 0<=i<len(valid):continue
                    feedback.append(dict(title=valid[i]['title'],cover_title=valid[i]['cover_title'],
                        reason=row.get('reason'),
                        failed_checks=[k for k in CHECKS if row.get(k) is not True],
                        appeal=row.get('appeal')))
                raise ValueError('独立复核退回：先按原文修正中心观点，再重写三个角度；具体意见：'
                                 +json.dumps(feedback,ensure_ascii=False))
            winner = max(accepted, key=lambda r:r['appeal'])
            item = valid[winner['index']]
            return _package(item, transcript, dict(method='cpu_text_review', **winner), valid)
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
            if getattr(exc,'retryable_service',False):
                # A timed-out model is not editorial feedback. Do not enqueue
                # three full-transcript requests behind the still-busy server.
                # Preserve the existing complete-source-quote fallback; it must
                # pass its own evidence/readability checks before use.
                try:
                    return _extractive(transcript,speaker,existing_titles,preferred)
                except ValueError:
                    raise exc
            last_error = str(exc)
            print(f'[标题观点] 第{attempt + 1}次生成待修正：{last_error}', flush=True)
    return _extractive(transcript, speaker, existing_titles, preferred)


def error(title, proof, transcript=None, speaker='林园'):
    if (not isinstance(proof, dict) or proof.get('version') != VERSION
            or proof.get('kind') != 'editorial_claim' or summary_heading(title)):
        return '标题需重新提炼具体观点，不能使用旧的关键词拼盘'
    if title != proof.get('title') or not isinstance(proof.get('cover'), str):
        return '标题或封面与观点证明不一致'
    evidence = proof.get('evidence')
    if not isinstance(evidence, list) or not evidence or any(not isinstance(q, str) for q in evidence):
        return '标题缺少完整原文证据'
    if transcript is not None and any(compact(q) not in compact(transcript) for q in evidence):
        return '标题观点不能回溯真实字幕'
    review = proof.get('review') or {}
    if review.get('copy_sha256') != _binding(title, proof['cover'], proof.get('subject'), evidence):
        return '核验后标题、封面或证据发生变化'
    if review.get('method') == 'source_quote':
        from headline_policy import body, complete
        quote = review.get('quote') or ''
        if (body(title, speaker) != quote or evidence != [quote] or not complete(quote)):
            return '原话标题的观点或限定条件已变化'
    elif review.get('method') == 'cpu_text_review':
        if not all(review.get(k) is True for k in CHECKS) or type(review.get('appeal')) is not int or not 3 <= review['appeal'] <= 5:
            return '标题尚未通过独立原文核验'
        if not isinstance(review.get('reason'),str) or len(compact(review['reason'])) < 12:
            return '标题审核缺少原文依据说明'
        item = dict(title=title, cover_title=proof['cover'], subject=proof.get('subject'), evidence=evidence)
        issue = _candidate_error(item, transcript or ''.join(evidence), speaker, (), check_layout=False)
        if issue:
            return issue
    else:
        return '标题审核方法缺失'
    return None
