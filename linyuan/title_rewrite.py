"""Source-grounded claim titles: propose angles, review them, never list keywords."""
import difflib
import hashlib
import json
import re
from headline_policy import copy_length_ok

VERSION = 2026091303
CHECKS = ('source_supported', 'central_point', 'attribution_correct',
          'preserves_qualifiers', 'cover_consistent', 'readable')
ABSTRACT_ANCHORS = frozenset({'原因', '标准', '问题', '情况', '结果', '时候', '方面', '东西'})

# These are factual invariants, outside the replaceable style block.
COPY_FACT_CONSTRAINTS = """原话明确作出的判断也必须保留其语气，不能为了显得审慎而替嘉宾添加不确定性或观察建议。内容声明与嘉宾观点是两件事，不把编辑的态度写成嘉宾的话。
保留“再、追加、利润扩大”等范围：经营上不必追加投资，不等于企业赚钱不需成本，也不等于投资者不用本金。不能把企业生意的描述改成股价收益承诺。选方向“不会错”不能在封面压缩成“不亏、保本”；保留原话实际表达的判断。
如果标题含“前提是”“条件是”，封面也必须保留该完整条件，不能仅留下结果；字数不足时可询问“有什么前提”，不把条件藏掉或换成其他条件。
数字的范围不能压成一个端点：“十二三年”不能写成“十二年”，“两三倍”不能写成“两倍”；标题和封面都逐字核对数字与单位。
“我要赚够一万倍”是本人目标，不能改写为公司“能赚到一万倍”；标题和封面各自保留目标或意愿，不把愿望当已经验证的回报。
“十二个月”是时长，不是“十二月”；点位时间的“可能性很大、不好预测”不能在封面省掉。
标题和封面须各自说清对象，不写未解释的“这个点、此点、这三种病”。原文没有点位数值就不补数值，可改写为原文明确的预测边界。
压缩时保留真正被评价的对象：“某公司的现金流很好”评价的是现金流，不能写成“某公司是很好的现金流”；不能把对象的属性变成对象本身。
标题和封面必须写完对象、动作和宾语，不能以“真正的”“可能成为龙头的”等半句结束。封面写完整短句，不截取长标题的前18个字。
时间概率必须说明什么事件可能发生，不能仅删除不明点位，留下“十二个月可能性大”。
“不是A，而是B”须保留实际对象B；不能前半句仍把A当看好对象，后半句加上B就算修正。
讨论投资相关产品时，标题和封面均保留产品对象；不能把看好某类产品缩成看好疾病，也不能改成诊疗建议。"""


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
    # Jieba tags some concrete multiword subjects (e.g. 并发症) as "l"
    # (fixed expression), not "n". Excluding them forced real source34 drafts
    # to anchor on 空间/药物 while dropping the actual answer's subject.
    # Real source66: jieba labels 生意/买卖 as verbs, excluding two ordinary
    # business nouns. This rejected a complete source-backed draft while
    # allowing the ASR error 折远 (tagged as a person's name) to anchor copy.
    # These lexical exceptions still require literal guest-source evidence;
    # accepting every verb would also admit context-free actions as subjects.
    business_nouns = {'生意', '买卖'}
    counts = Counter(word for word, tag in jieba.posseg.cut(''.join(units))
                     if (tag.startswith(('n', 'vn')) or tag in ('l', 'j') or word in business_nouns)
                     and 2 <= len(compact(word)) <= 8 and word not in ABSTRACT_ANCHORS)
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
    # A grammar maxLength stops generation mid-phrase (real run 166 ended
    # the cover at "找到真正的"). Validate/derive complete copy afterwards.
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


def main_answer_quotes(units):
    # Copy exact sentence spans mechanically. The model chooses meaning; it
    # must not recreate an ASR quotation from memory (run 35846866976 added
    # “目前/还是” and dropped fillers, so an otherwise correct answer failed).
    # Do not force arbitrary length cuts or merge across sentence boundaries.
    return list(dict.fromkeys(s for s in re.findall(r'[^。！？!?；;]+[。！？!?；;]*',''.join(units))
                              if 4<=len(compact(s))<=160 and len(s)<=200
                              and not answer_setup(s)))


def answer_setup(text):
    """A promise to explain a question is not the explanation itself."""
    return bool(re.match(
        r'^(?:那么|那|所以|现在|今天|接下来|啊|呃)*(?:我|我们)(?:就|再|先|要|来|详细|给大家|跟大家)*'
        r'(?:讲一下|讲一讲|讲讲|说一下|说一说|解释一下)(?:就是|是|关于|这个|一下)*'
        r'(?:为什么|怎么|如何|什么)',compact(text)))


def answer_reading_units(cues, speaker='林园'):
    """Join display-line continuations, preserving all text and excluded turns."""
    from speaker_attribution import other_guest_indices, named_handoffs
    host=explicit_host_cues(cues)
    other=other_guest_indices(cues,speaker)
    handoffs={r['cue'] for r in named_handoffs(cues,speaker)}
    units=[];current='';previous=None
    for i,cue in enumerate(cues):
        boundary=(i in host,i in other)
        # Do not glue an excluded question/other speaker to an eligible reply.
        # If punctuation is absent, keep the original cue boundary at 200
        # characters rather than cutting an arbitrary word or inventing a stop.
        if current and (boundary!=previous or i in handoffs or len(current)+len(cue)>200):
            units.append(current);current=''
        previous=boundary
        start=0
        # One displayed cue can contain the end of a host's question and
        # the start of the guest's reply (actual 311). Split every original
        # sentence stop, including stops inside a cue, before joining tails.
        for stop in re.finditer(r'[。！？!?；;]+[”’」』\"]?',cue):
            current+=cue[start:stop.end()]
            units.append(current);current='';start=stop.end()
        current+=cue[start:]
    if current:units.append(current)
    if ''.join(units)!=''.join(cues):
        raise ValueError('阅读分句改变了原始字幕')
    return units


def reading_schema(unit_count, answer_focus=False, units=None, answer_subject=False):
    turn=dict(a_start=dict(type='integer',minimum=0,maximum=unit_count-1),
              b_end=dict(type='integer',minimum=0,maximum=unit_count-1))
    # Run 158 assigned alternating roles before understanding the dialogue,
    # then copied almost the full interview into its answer summary. JSON
    # grammar order must put the actual meaning before the boundary bookkeeping.
    fields=dict(a_guest_answer=dict(type='string',maxLength=240),
        b_question_premise=dict(type='string',maxLength=240),
        c_guest_spans=dict(type='array',minItems=1,maxItems=unit_count,
        items=dict(type='object',additionalProperties=False,required=list(turn),properties=turn)))
    if answer_focus:
        # A short span list repeatedly omitted the antecedent of “这两个行业”
        # (real run 35850815645). Account for every complete sentence, without
        # silently turning unlabelled text into guest speech.
        fields.pop('c_guest_spans')
        sentence_roles={f'u{i:04d}':dict(type='string',enum=['host','guest','unknown'])
                        for i in range(unit_count)}
        fields['c_sentence_roles']=dict(type='object',additionalProperties=False,
            required=list(sentence_roles),properties=sentence_roles)
        # Run 35844419694 confused a list of main-answer IDs with the first
        # cue of every paragraph, including incomplete tails. Ask for one
        # actual claim in words; bind it back to cues ourselves, before drafts.
        fields['d_main_answer_quote']=dict(type='string',minLength=4,maxLength=200)
        if units is not None:
            quotes=main_answer_quotes(units)
            if not quotes:
                raise ValueError('没有可逐字绑定的完整长度原句，保留素材等待核对')
            fields['d_main_answer_quote']['enum']=quotes
        if answer_subject:
            fields['e_subject_name']=dict(type='string',minLength=2,maxLength=24)
    return dict(type='object',additionalProperties=False,required=list(fields),properties=fields)


def bind_answer_focus(reading, units, roles, speaker='林园'):
    quote=reading.get('d_main_answer_quote')
    if not isinstance(quote,str) or not 4<=len(compact(quote))<=160:
        raise ValueError('主要回答必须是一段完整的连续原话，不能列段落起点或自行概括')
    if answer_setup(quote):
        raise ValueError('讲一下为什么只是开场预告，主要回答须选择实际判断或行动，不能把预告当结论')
    from speaker_attribution import other_guest_indices
    blocked=explicit_host_cues(units) | other_guest_indices(units,speaker)
    # Preserve short continuation/negation cues. Length is a writer-evidence
    # constraint, not a speaker identity test. Never bridge unknown/host cues.
    passage='';owners=[];needle=compact(quote)
    for i,unit in enumerate(units):
        if roles[i]!='guest' or i in blocked:
            passage='';owners=[]
            continue
        text=compact(unit);passage+=text;owners.extend([i]*len(text))
        start=passage.find(needle)
        if start>=0:
            ids=list(dict.fromkeys(owners[start:start+len(needle)]))
            if set(ids)&set(guest_evidence_ids(units,roles,speaker)):
                return ids
    raise ValueError('主要回答原话未匹配到连续的已确认嘉宾回答；不能改写、拼接或借用主持人/未知句子')


def require_answer_focus(candidate, main_ids):
    if not set(candidate.get('evidence_ids',[])) & set(main_ids):
        raise ValueError('标题只引用旁枝解释，未引用独立阅读选出的主要回答')
    return candidate


def bind_answer_subject(reading, units, roles, speaker='林园'):
    """Carry a source-grounded object across the reader/writer boundary.

    This verifies provenance, not coreference semantics. The independent
    full-dialogue reviewer must still check that it is this answer's object.
    """
    name=reading.get('e_subject_name')
    if (not isinstance(name,str) or not 2<=len(name)<=24 or name!=name.strip()
            or not re.fullmatch(r'[\w\u4e00-\u9fff]+',name)
            or re.fullmatch(r'(?:这|那|该|本|当前|目前|上述|前述)(?:个|些|类|种)?(?:行业|赛道|市场|公司|企业|产品)',name)
            or not subject_catalog([name])):
        raise ValueError('主要回答对象必须是原文中的具体名称，不能用代词、标准或原因代替')
    # Actual 8B identified 中石油 but supplied ID 1 (only 它); 9B gave
    # [1,5,6,16] although only 5 contains that name. Locating a literal name
    # is deterministic bookkeeping, not another semantic task for the model.
    ids=[i for i in guest_evidence_ids(units,roles,speaker) if name in units[i]]
    if not ids:
        raise ValueError('主要回答对象没有逐字匹配已确认嘉宾原句；主持人假设不能充当对象证据')
    return dict(name=name,evidence_ids=ids,exact_source=[units[i] for i in ids])


def require_answer_subject(candidate, subject):
    if (not set(candidate.get('evidence_ids',[])) & set(subject['evidence_ids'])
            or any(subject['name'] not in candidate.get(k,'') for k in ('title','cover_title'))):
        raise ValueError('标题与封面都须写明阅读阶段确认的具体对象，并引用该对象对应的嘉宾原句')
    return candidate


def bind_reading(reading, units):
    if (not isinstance(reading,dict)
            or not 12<=len(compact(reading.get('a_guest_answer')))<=240
            or len(compact(reading.get('b_question_premise')))<4):
        raise ValueError('先分别读清嘉宾实际回答与主持人的问题前提，不能直接凭关键词写标题')
    if 'c_sentence_roles' in reading:
        mapping=reading['c_sentence_roles']
        expected=[f'u{i:04d}' for i in range(len(units))]
        if (not isinstance(mapping,dict) or set(mapping)!=set(expected)
                or any(mapping[k] not in ('host','guest','unknown') for k in expected)):
            raise ValueError('完整句子的说话人必须逐句核对；不能遗漏、猜补或改变编号')
        roles=[mapping[k] for k in expected]
        if 'guest' not in roles:
            raise ValueError('完整阅读未确认任何嘉宾回答')
        return roles
    spans=reading.get('c_guest_spans')
    if not isinstance(spans,list) or not spans:
        raise ValueError('必须找出能够明确归属嘉宾的实际回答，不能只列主持人的提问')
    # Classify only explicit guest replies. All other original cues remain in
    # full-source review as unknown, never discarded or silently made guest.
    # Run 161 emitted four host question starts and no guest reply at all.
    roles=['unknown']*len(units);last_end=-1
    for row in spans:
        if not isinstance(row,dict):raise ValueError('嘉宾回答区间格式无效')
        start,end=row.get('a_start'),row.get('b_end')
        if (type(start) is not int or type(end) is not int or start<=last_end
                or start<0 or end<start or end>=len(units)):
            raise ValueError('嘉宾回答区间必须顺序、不重叠且位于完整原文内')
        roles[start:end+1]=['guest']*(end-start+1);last_end=end
    return roles


def evidence_usable(text):
    if not isinstance(text,str):return False
    n=len(compact(text))
    if n>=8:return True
    if n<4:return False
    # Source8's main answer “还没有进入牛市” is seven characters, whereas
    # the rejected 818 fragment “还有一个” is four. A fixed eight-character
    # floor made the writer choose longer secondary explanations. Short cues
    # must themselves be complete predicates, never dangling enumerations.
    from headline_policy import complete
    return complete(text.strip('。！？!?；;')) and not re.search(
        r'(?:[一二两三四五几]个|首先|其次|另外|还有|就是说)[。！？!?；;]*$',text)


def guest_evidence_ids(units, roles, speaker='林园'):
    from speaker_attribution import other_guest_indices
    blocked=explicit_host_cues(units) | other_guest_indices(units,speaker)
    return [i for i,role in enumerate(roles)
            if role=='guest' and i not in blocked and evidence_usable(units[i])]


def explicit_host_cues(units):
    """Exclude explicit questions/summaries; this never certifies a guest cue."""
    markers=re.compile(r'(?:您|你)(?:觉得|认为|怎么看|看好哪个)|你们.*(?:可能|会|如何|怎么)'
        r'|我可以这么理解|(?:林总|林园总|林远总).*(?:理解|请问|如何|怎么看)'
        r'|您的(?:过往|观点|意思)|我(?:也|大概|简单|来|再|先|这么|那我)*(?:听懂|听明白|理解|总结)'
        r'|这样理解|(?:接着|再).*问|话题.*告一段')
    from speaker_attribution import named_handoffs
    blocked={turn['cue'] for turn in named_handoffs(units)};host=False;recap=False
    for i,text in enumerate(units):
        body=compact(text)
        # Actual library314: this host recap was selected as guest evidence.
        # Exclude through its original full stop, then let the independent
        # reader identify the next reply; do not swallow the guest's rebuttal.
        if recap or re.match(r'^(?:所以)?(?:你|您)还是主张',body):
            blocked.add(i)
            recap=not bool(re.search(r'[。！？!?][”’」』\"]?\s*$',text))
            continue
        if markers.search(body):host=True
        elif host:
            # A completed question followed by an explicit topical reply can
            # end the deterministic exclusion. A '?' alone is insufficient:
            # real interviewers also continue with premises after a question.
            # Source17: “光伏能源你怎么看呢？” was followed by “光伏能源是
            # 这样的，就是我没有…”, but the sticky flag hid that whole answer
            # until “我们没参与”. Release it to the independent reader; this
            # does not automatically relabel the next cue as guest.
            reply=re.match(r'^([\u4e00-\u9fffA-Za-z]{2,12})是这样的(?:就是)?我',body)
            if (i and reply and reply[1] in compact(units[i-1])
                    and re.search(r'[？?][”’」』\"]?\s*$',units[i-1])):
                host=False
            # Source8 answers the completed market question directly with
            # “这个位置应该是不高”, without an 我 opening. The sticky host
            # flag otherwise hid it and “还没有进入牛市”, leaving only later
            # 市值 explanations available to the writer. Release this explicit
            # topical answer for independent attribution, never label it guest.
            if (i and re.search(r'[？?][”’」』\"]?\s*$',units[i-1])
                    and re.search(r'位置|估值|大盘|牛市|市场',''.join(units[max(0,i-8):i]))
                    and re.match(r'^(?:这个|目前的?|现在的?)?(?:位置|估值)(?:应该|目前|现在|确实|还是)*(?:是)?(?:不高|不低|高|低|便宜|贵)',body)
                    and not re.search(r'[？?]|吗|呢',text)):
                host=False
            opening=re.sub(r'^(?:嗯|啊|哎|呃|那个|这个|那么|现在|所以|就是|好|那)*','',body)
            if re.match(r'^(?:我觉得|我认为|我个人|我们|对了对|总的来说|总体来说)',opening) or '我老林' in body:
                host=False
        if host:blocked.add(i)
    return blocked


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


def bind_guest_candidate(item, raw_focus, units, subjects, guest_ids):
    raw=item.get('a_focus',raw_focus)
    if not isinstance(raw,dict):raise ValueError('候选缺少自己的原文判断')
    claim=raw.get('a_claim');ids=raw.get('b_evidence_ids')
    if not isinstance(claim,str) or not copy_length_ok(claim,500):
        raise ValueError('先写清有原文依据的完整判断和限定，再写标题')
    if not isinstance(ids,list) or any(type(i) is not int or i not in guest_ids for i in ids):
        raise ValueError('候选证据属于主持人或未知归属，须回到嘉宾原话')
    if not 1<=len(ids)<=4:raise ValueError('原文证据编号数量无效')
    # JSON-schema uniqueItems is not enforced by every local grammar. Real
    # 8B drafts repeated 27/30 and discarded otherwise reviewable copy. Exact
    # duplicate IDs add no evidence: deduplicate only after guest attribution
    # and type/range checks, then retrieve the original bytes as before.
    unique=list(dict.fromkeys(ids))
    result=bind_candidate(item,dict(claim=claim,evidence_ids=unique),units,subjects)
    if unique!=ids:result['evidence_id_normalization']=dict(raw=ids,unique=unique)
    return result


def bind_candidate(item, focus, units, subjects):
    title=re.sub(r'\s+',' ',str(item.get('title') or '')).strip()
    cover=re.sub(r'\s+',' ',str(item.get('cover_title') or '')).strip()
    speaker=title.split('：',1)[0] if '：' in title else ''
    if speaker and cover.startswith(speaker+'：'):
        cover=cover[len(speaker)+1:].strip()
    elif speaker and re.match(re.escape(speaker)+r'(?:听说|认为|表示|重点选择|选择|看好|不看好|没参与|没买|没有买|不买|不会买|买入|卖出)',cover):
        # Actual 8B drafts repeated the speaker label without a colon. Remove
        # only this attributed reporting prefix, before length/meaning review;
        # never strip names inside entities such as 林园投资公司.
        cover=cover[len(speaker):].strip()
    bound=bind_evidence(dict(title=title,cover_title=cover,evidence_ids=focus.get('evidence_ids')),units)
    # Derive the exact shared anchor instead of asking the model to perform
    # literal string matching. The full claim still needs independent review.
    anchors=[word for word in subjects if word in title and any(word in q for q in bound['evidence'])]
    bound['subject']=max(anchors,key=len) if anchors else ''
    source_text=''.join(units)
    scope_lost_on_cover=(research_scope_error(title,cover,source_text)
                         and not research_scope_error(title,title,source_text))
    hearsay_lost=(reported_claim_error(title,cover,source_text)
                 and not reported_claim_error(title,title,source_text))
    if (copy_fragment(cover) or summary_heading(cover.removeprefix(speaker)) or scope_lost_on_cover or hearsay_lost
            or not copy_length_ok(cover,18)):
        # An exact complete title clause can serve as the cover without a
        # second factual rewrite. The independent reviewer sees this final
        # clause and all original source context before approving anything.
        from headline_policy import cover_fits
        clauses=re.split(r'[，,。；;：:！？!?]',title.split('：',1)[-1])
        complete_spans=clauses+['，'.join(clauses[i:i+2]) for i in range(len(clauses)-1)]
        choices=[c.strip() for c in complete_spans if copy_length_ok(c,18)
                 and not copy_fragment(c) and bound['subject'] and bound['subject'] in c
                 and not summary_heading(c)
                 and not research_scope_error(title,c,source_text)
                 and not reported_claim_error(title,c,source_text)
                 and cover_fits(c)]
        if choices:
            bound['cover_title']=min(choices,key=lambda c:abs(len(compact(c))-13))
            print('[标题观点] 封面采用完整标题子句：'+bound['cover_title'],flush=True)
    return bound


def copy_fragment(text):
    from headline_policy import verbal_fragment
    if verbal_fragment(text):
        return True
    from headline_policy import dangling_tail
    text=str(text or '').strip(' ，,。；;！？!?')
    # Actual fixed100 / 99 ended its cover with “别急着加”: the object
    # 杠杆 disappeared although it remained in the title. Keep a full clause
    # available for bind_candidate's existing pre-review cover repair.
    if re.search(r'(?:别|不要)(?:急着|急于)加$',text):
        return True
    if re.search(r'(?:才|就)是好$',text):
        return True
    # Production deployment 35946591309: the reviewer approved a cover
    # ending “未来可能成为” and a title ending “成为龙头的要时间看”.
    # Neither supplies a complete, naturally ordered clause.
    if re.search(r'成为$|成为[^，,。；;！？!?]{1,12}的要时间(?:找|看)$',text):
        return True
    return dangling_tail(text)


def relation_error(title, cover, source):
    # The real 165 CPU review approved the opposite of "没有龙头" even
    # with correctly attributed guest evidence. Preserve this explicit phase
    # distinction; an already chosen leader is not an emerging future leader.
    body=compact(source)
    if re.search(r'喜欢危机|倾向于买危机',body) and not re.search(r'不是危机|并非危机',body):
        if any(re.search(r'不是危机|并非危机',copy) for copy in (title,cover)):
            return '原话明确喜欢或买危机，不能为制造反差改写成不是危机；可保留本人原话和实际对象'
    if re.search(r'没有龙头|龙头还?没(?:有)?(?:走|跑|分)出来|尚未.{0,4}龙头',body):
        qualifier=r'没有|还没|尚未|未定|未出|未来|以后|最终|最后|真正|成为|成长|形成|走出|跑出|分出|等|可能'
        for candidate in (title,cover):
            if '龙头' in candidate and not re.search(qualifier,candidate):
                return '原文龙头尚未形成，标题不能反转成选择现成龙头；须保留原有阶段和限定'
    return None


def cover_qualifier_error(title, cover):
    """Catch explicit conditions lost in compression, even after model approval.

    This deliberately checks only unambiguous condition constructions. It is
    not a substitute for reviewing negation, attribution, or the full source.
    A question asking for the condition makes no unconditional promise.
    """
    # Actual all-output case28 kept a future belief in the title but turned
    # it into an accomplished fact on the cover, despite two positive reviews.
    if (re.search(r'我(?:相信|觉得|判断|认为).{0,12}(?:未来|将来|今后).{0,20}增长', title)
            and '增长' in cover
            and not re.search(r'我(?:相信|觉得|判断|认为)|预计|有望|可能|能否|会不会|是否|[？?]', cover)):
        return '封面把个人的未来增长判断写成事实；保留我相信等原有判断语气，或提出完整问题'
    # Actual 14B replay kept MY standards in the title but made the cover
    # sound like an objective company qualification. Do not require a reason
    # when a cover only states the speaker's sourced choice (e.g. 没买中石油).
    if (re.search(r'不符合(?:我|我们)(?:自己)?的?(?:投资)?标准', compact(title))
            and re.search(r'不符合.{0,8}标准', compact(cover))
            and not re.search(r'不符合(?:我|我们)(?:自己)?的?(?:投资)?标准', compact(cover))):
        return '封面遗漏个人标准的范围；保留我的标准，或仅写有原文依据的本人选择'
    conditions = re.findall(r'(?:前提是|前提为|条件是|只要)([^，。；！？,;!?]+)', title)
    conditions += re.findall(
        r'(?:^|[，,；;])(?:但)?([^，。；！？,;!?]+?)(?:才是|是)前提', title)
    if not conditions:
        return None
    asks_condition = re.search(
        r'(?:什么|哪些|怎样的|何种)(?:前提|条件)|(?:前提|条件)(?:是什么|有哪些)', cover)
    if not asks_condition and any(compact(c) not in compact(cover) for c in conditions):
        return '封面遗漏标题的明确前提；保留完整条件，或改成询问该条件的完整问题，不能直接承诺结果'
    return None


def review_source_quote_error(reason, transcript):
    """A judge's purported verbatim source quotes must exist in the source.

    Candidate quotes and ordinary paraphrases remain allowed. Only quoted
    spans explicitly attributed to the original text are checked here.
    """
    if not isinstance(reason,str) or not transcript:
        return None
    quote = r'''[“‘「『"']([^”’」』"'\n]{2,160})[”’」』"']'''
    attributed = (r'(?:原文|原话|字幕)(?:中|里)?(?:的|提到|写道|说道|说|是|为)?'
                  r'\s*[：:]?\s*' + quote +
                  r'(?:\s*(?:和|、|以及|及|与)\s*' + quote + r')*')
    source=compact(transcript)
    for match in re.finditer(attributed,reason):
        for literal in re.findall(quote,match.group(0)):
            if compact(literal) not in source:
                return '复核说明引用了原文中不存在的话：'+literal+'；须重新核对原文，不能用模型自评代替证据'
    return None


def subject_attribute_error(title, cover, transcript):
    """Keep the source's evaluated attribute when compressing a noun phrase.

    Ground this narrow identity check in an explicit X的Y source phrase.
    It is not a list of countries, companies, or individual bad titles.
    """
    attributes = r'经济|现金流|利润|收入|负债|股价|估值|业绩|需求|竞争力|增长率|股息率|价格|质量'
    source = compact(transcript)
    for copy in (title, cover):
        clauses = re.split(r'[：:，,。；;！？!?]', str(copy or ''))
        for clause in clauses:
            match = re.fullmatch(rf'(.{{2,16}}?)是.{{0,18}}?({attributes})', clause.strip())
            if not match:
                continue
            owner, attribute = match.groups()
            if compact(owner + '的' + attribute) in source:
                return ('标题或封面压缩时丢失被评价的对象：原文讨论的是'
                        + owner + '的' + attribute + '，不能把' + owner + '本身写成' + attribute)
    return None


def source_payback_condition_error(title, cover, transcript):
    """Source7 can omit the income premise from BOTH title and evidence.

    Inspect the complete transcript, not just model-selected snippets. This
    narrow observed construction does not claim general entailment checking.
    """
    source=compact(transcript)
    if not re.search(r'(?:基于|前提).{0,65}人均收入.{0,14}不(?:减少|下降|降低)',source):
        return None
    for copy in (title,cover):
        if '回本' not in copy:
            continue
        question=re.search(r'(?:什么|哪些|怎样的|何种)(?:前提|条件)|(?:前提|条件)(?:是什么|有哪些)',copy)
        income_floor=re.search(r'(?:人均)?收入.{0,8}(?:不减|不降|持平|至少不低)',compact(copy))
        if not question and not income_floor:
            return '回本判断遗漏完整原文中的收入前提；标题与封面不能一起省掉条件，证据选句也不能避开限定'
    return None


def _quantity_intervals(text):
    """Conservative reading of small Chinese quantities, including 十二三年.

    Unrecognized numerals are left to semantic review. Parse whole tokens so
    a year such as 二零零三年 cannot accidentally become 三年.
    """
    digits = {c: n for n, c in enumerate('零一二三四五六七八九')}
    def integer(raw):
        raw = raw.replace('两', '二').replace('〇', '零')
        if raw.isdigit():
            return int(raw)
        if raw in digits:
            return digits[raw]
        match = re.fullmatch(r'([一二三四五六七八九]?)十([一二三四五六七八九]?)', raw)
        if match:
            return (digits.get(match[1], 1) * 10) + digits.get(match[2], 0)
        return None
    number = r'(?:[零〇一二两三四五六七八九十百千万亿]+|[0-9]+)'
    pattern = rf'(?<![零〇一二两三四五六七八九十百千万亿0-9])({number})(?:(?:到|至|[~～—-])({number}))?(年|倍|个月|月|天)'
    rows = []
    for match in re.finditer(pattern, text):
        a, b, unit = match.groups()
        low, high = integer(a), integer(b) if b else integer(a)
        if not b and low is None:
            approx = re.fullmatch(r'([一二三四五六七八九]?十)?([一二两三四五六七八九])([一二三四五六七八九])', a)
            if approx:
                prefix, first, second = approx.groups()
                base = integer(prefix) if prefix else 0
                low = base + integer(first)
                high = base + integer(second)
                if high != low + 1:
                    continue
        if low is not None and high is not None and low <= high:
            rows.append((unit, low, high, match[0]))
    return rows


def quantity_range_error(title, cover, source):
    source_rows = _quantity_intervals(source)
    # This catches an observed model-approved precision change, not all
    # possible numerical errors or whether a quantity supports the claim.
    for text in (title, cover):
        for unit, low, high, raw in _quantity_intervals(text):
            if low != high or any(u == unit and a == b == low for u, a, b, _ in source_rows):
                continue
            enclosing = next((q for u, a, b, q in source_rows if u == unit and a < b and a <= low <= b), None)
            if enclosing:
                return f'原文“{enclosing}”是范围，不能在标题或封面中缩成确定的“{raw}”'
    return None


def earnings_intent_error(title, cover, transcript):
    """Do not turn an explicit numerical earnings goal into a return claim.

    This catches the actual source46 full-run regression. It is deliberately
    confined to the same literal multiple; general intent/entailment and
    alternative number spellings still require the independent reviewer.
    """
    number = r'[零〇一二两三四五六七八九十百千万亿0-9]+'
    targets = re.findall(rf'(?:我|我们)(?:就是|一定|还)?(?:要|想|希望)(?:能)?赚(?:到|够)?(?:这)?({number}倍)', transcript)
    for target in targets:
        literal = re.escape(target)
        # A genuine completed-result statement elsewhere is a different
        # source claim, not automatically negated by the stated future goal.
        if re.search(rf'(?:已经|曾经|过去)[^。！？!?]{{0,10}}赚(?:了|到|过|够)?{literal}|赚(?:(?:到|够)?了|过){literal}', transcript):
            continue
        for copy in (title, cover):
            if not re.search(rf'赚(?:到|够|得)?(?:这)?{literal}', copy):
                continue
            if not re.search(r'目标|希望|想赚|(?:我|我们)(?:就|还)?要赚|能否|能不能|[？?]', copy):
                return '原话的收益倍数是本人目标；标题和封面不能把我要赚改成企业能赚或确定回报'
    return None


def hypothetical_exclusivity_error(title, cover, transcript):
    """Preserve source13's hypothetical sole supplier, including in questions.

    This bounded check covers the observed 'only one company' construction;
    it is not a general verifier of counterfactual reasoning or causality.
    """
    exclusive = r'(?:就(?:这|那)?一|只有一|仅有一|只此一)家|(?:全球|全世界)唯一'
    condition = r'如果|假如|假设|要是'
    sentences = re.split(r'[。！？!?；;]', transcript)
    hypothetical = [s for s in sentences if re.search(condition, s) and re.search(exclusive, s)]
    if not hypothetical:
        return None
    # Do not let a hypothetical about one company block a different subject.
    subjects = {name for s in hypothetical for name in subject_catalog([s])
                if name not in {'全世界', '全球', '世界', '公司', '企业', '说法'}}
    for copy in (title, cover):
        if not re.search(exclusive, copy) or re.search(condition, copy):
            continue
        named = [name for name in subjects if name in copy]
        if not named:
            continue
        if any(name in s and re.search(exclusive, s) and not re.search(condition, s)
               for name in named for s in sentences):
            continue
        return '全球唯一只是原文假设，标题和封面须保留如果；不能把假设当事实或已确认的没买原因，问号也不能消除这个错误前提'
    return None


def example_duration_error(title, cover, transcript):
    """An explicitly illustrative waiting period is not a definite deadline."""
    from title_quantity_context import number
    duration = r'([0-9零〇一二两三四五六七八九十百千万]+)(个?月|年|天)'
    examples = re.findall(r'(?:比如|例如|比方)(?:说)?[，,\s]*(?:通过|经过|等待|等|花|用|需要)' + duration, transcript)
    illustrated = {(number(n), unit.removeprefix('个')) for n, unit in examples if number(n) is not None}
    if not illustrated:
        return None
    realized = re.findall(r'(?:实际|确实|已经|最终|当时)(?:等了|花了|用了|经历了)' + duration, transcript)
    illustrated -= {(number(n), unit.removeprefix('个')) for n, unit in realized}
    for copy in (title, cover):
        for clause in re.split(r'[。！？!?；;]', str(copy or '')):
            if re.search(r'比如|例如|比方|举例|假设', clause):
                continue
            if not re.search(r'要|需要|得|等|才|年后|月后|天后', clause):
                continue
            if any((number(n), unit.removeprefix('个')) in illustrated for n, unit in re.findall(duration, clause)):
                return '原文期限只是举例，标题和封面不能写成确定等待期限；保留举例语气，或不用这个具体数字'
    return None


def forecast_copy_error(title, cover, transcript):
    """Preserve duration units and uncertainty in the observed point forecast.

    This narrow rule catches the actual source58 failure. It neither invents a
    missing index level/date nor claims to solve general semantic entailment.
    """
    from title_market_impression import impression_error
    example_issue = example_duration_error(title, cover, transcript)
    if example_issue:
        return example_issue
    impression_issue = impression_error(title, cover, transcript)
    if impression_issue:
        return impression_issue
    intent_issue = earnings_intent_error(title, cover, transcript)
    if intent_issue:
        return intent_issue
    hypothetical_issue = hypothetical_exclusivity_error(title, cover, transcript)
    if hypothetical_issue:
        return hypothetical_issue
    # Actual source99 explicitly says the bull market has not arrived yet.
    # A rhetorical question mark after an earnings multiple is no qualifier.
    if re.search(r'牛市[，,\s]*(?:还)?没来之前', transcript):
        for copy in (title, cover):
            if (re.search(r'牛市(?:已经)?来了', copy)
                    and re.search(r'[挣赚][^。！？!?]{0,12}倍', copy)
                    and not re.search(r'(?:等(?:到)?|到|如果|假如|若|一旦)牛市|牛市来了(?:之后|以后|时|后)', copy)):
                return '原文说牛市没来之前说不清楚；标题和封面不能删去到、等或如果，把未来情境中的收益倍数写成已发生的行情'
    # Real source28 lost 我相信 in BOTH fields after the cover-only repair.
    # Match the same distinctive growth claim in the source, rather than
    # treating every nearby personal opinion as a qualifier for all claims.
    beliefs=re.findall(r'我(?:相信|觉得|判断|认为)[^。！？!?]{0,16}(?:未来|将来|今后)[^。！？!?]{0,24}(爆发性增长)',transcript)
    for claim in beliefs:
        for copy in (title,cover):
            if claim in copy and not re.search(r'我(?:相信|觉得|判断|认为)|预计|有望|可能|能否|会不会|是否|[？?]',copy):
                return '个人的未来增长判断丢失原有判断语气；标题和封面都须保留我相信等限定，不能一起改成事实'
    # Actual source34 says 空间应该在一百倍到五百倍之间. Both model
    # reviews approved a cover asserting the range as established fact.
    # Bound this check to a quantified market-space estimate, not every
    # occurrence of 应该 (which can also express an instruction).
    estimated_space=re.search(r'空间[^。！？!?]{0,8}(?:应该|应当|可能|估计|预计)[^。！？!?]{0,24}倍',transcript)
    if estimated_space:
        for copy in (title,cover):
            if (re.search(r'空间[^。！？!?]{0,20}倍',copy)
                    and not re.search(r'应该|应当|应有|应在|可能|估计|预计|有望|或有|能否|是否',copy)):
                return '市场空间倍数是原话的估计，标题和封面都须保留应该、可能或估计的语气'
    quantities=_quantity_intervals(transcript)
    durations={(a,b) for unit,a,b,_ in quantities if unit=='个月'}
    calendars={(a,b) for unit,a,b,_ in quantities if unit=='月'}
    for copy in (title,cover):
        rows=_quantity_intervals(copy)
        if any(unit=='月' and (a,b) in durations and (a,b) not in calendars for unit,a,b,_ in rows):
            return '原文的月份时长不能变成日历月份；十二个月不是十二月，标题与封面都须保留单位'
        if re.search(r'这个点(?:位)?|此点(?:位)?',copy) and not re.search(r'[0-9]{3,5}点',copy):
            return '点位指代没有解释，标题与封面不能让观众猜“这个点”；不得擅自补点位'
        if (durations and re.search(r'点位|这个点|牛市',transcript)
                and re.search(r'个月.{0,5}可能性',copy)
                and not re.search(r'市场|指数|牛市|突破|涨到|回本',copy)):
            return '时间概率缺少具体事件；“十二个月可能性大”没有说明什么可能发生，不能仅删除不明点位'
        # A short qualifier in another ASR cue is still part of this prediction.
        forecast=bool(rows and re.search(r'市场|指数|点位|牛市|突破|涨到',copy))
        uncertain=re.search(r'可能性.{0,8}(?:大|存在)|不好预测|判断不了',transcript)
        qualified=re.search(r'可能|不确定|不好预测|判断不了|(?:我|个人)(?:的)?(?:判断|预测|估计)|多久|何时|什么时候|能否|[？?]',copy)
        if forecast and uncertain and not qualified:
            return '点位或牛市的时间判断丢失原文不确定性；标题和封面都不能写成确定的时间承诺'
    return None


def unresolved_subject_error(title, cover):
    for copy in (title,cover):
        # Exact quotations also need a self-contained object on the cover.
        # Preserve explicit apposition (例如“这些医药公司”) and first-person voice.
        vague=re.search(r'这(?:些|类|种|几个|几家)(?:公司|企业|东西|有关系|相关)',copy)
        # Real source46 already names 龙头 before qualifying these companies
        # as rare. Do not reject the qualifier we need the editor to preserve.
        antecedent=(vague and re.search(
            r'龙头(?:公司|企业)?|(?:医药|白酒|科技|半导体|食品|光伏|能源)(?:公司|企业)',
            copy[:vague.start()]))
        if vague and not antecedent:
            return '标题或封面的讨论对象只有未解释的指代；写明具体对象，不能把原文中的“这些”单独摘成标题'
        if (re.search(r'这(?:两|三|几)种病',copy)
                and not re.search(r'心脏病|糖尿病|高血压|并发症',copy)):
            return '文案只有“这几种病”的未解释指代；写出疾病或实际讨论的并发症产品，不复制问题残句'
        body=re.sub(r'^[^：:]+[：:]', '', copy)
        if (re.search(r'(?:我|我们)?(?:没买|没有买|不买)[，,。；;]', body)
                and re.search(r'(?:它|这|那).{0,8}(?:不符合|符合).{0,6}标准', body)
                and not subject_catalog([body])):
            return '没买什么没有说清；不能用它和标准代替具体对象，须从已确认嘉宾上下文补足并引用证据'
        if re.search(r'这原因那原因|(?:这|那)(?:个)?原因',body) and not subject_catalog([body]):
            return '原因没有具体对象；原话虽真实，也不能把离开上下文的泛指句当成标题'
        if re.search(r'(?:这个|那个|这一个)(?:位置|价位)',body) and not re.search(
                r'A股|股市|市场|上证|沪指|指数|牛市|熊市|估值|股价|股息|医药|消费|白酒|光伏|中石油|茅台|片仔癀',body):
            return '标题或封面的这个位置没有具体对象；须说明原文所指市场或产品，不能让观众猜位置'
        if re.search(r'(?:新|旧|这个|那个)东西',body) and not re.search(
                r'人工智能|AI|新能源(?:汽车)?|医药|白酒|半导体|制造业|服务业|技术|产品|企业|公司',body):
            return '标题或封面只写新东西、旧东西，没有具体对象；不能用泛指词充当标题主题'
        if (re.match(r'要有时间|不排除(?:十|十二|12)个月', body)
                and not re.search(r'市场|指数|牛市|突破|回本|收益|盈利|投资', body)):
            return '时间原话缺少发生什么的对象；摘录真实短句也不能让观众猜十二个月指什么'
    return None


def unsupported_hedge_error(title, cover, evidence):
    """An invented hedge also changes the speaker's claim, even if cautious.

    The real source28 review twice approved uncertainty absent from its
    selected guest evidence. This narrow check catches that family, not every
    paraphrase or semantic error; ambiguous cases still need review.
    """
    # Library220's reviewer treated “就是这么个规律” as support for
    # “但得看它是不是规律”. A nearby 可能 about luck is not uncertainty
    # about this separate claim. Keep this object-specific, not a blanket ban
    # on question titles or on the word 可能 elsewhere in the answer.
    source = compact(''.join(evidence))
    claim=compact(title+'。'+cover)
    # Real replay 35564387599: the critic checked only the first clause and
    # approved an invented efficacy assessment in the second. A discussion of
    # disease-related products does not itself support a claim about efficacy.
    if (re.search(r'疗效|(?:药物|药品|产品).{0,4}(?:效果|是否有效)', claim)
            and not re.search(r'疗效|效果|有效|无效|起作用|管用', source)):
        return '原文未评价药物或产品效果，标题不能新增疗效好坏、待验证或不确定的判断'
    if '空间估值' in claim and not re.search(r'估值|市盈率|PE|pe', source):
        return '原文讨论市场空间，不能改成估值倍数'
    questioning_rule = re.search(r'(?:得|要|需)看.{0,5}(?:是不是|是否).{0,4}规律', title+'。'+cover)
    if (questioning_rule and re.search(r'就是.{0,6}规律', source)
            and not re.search(r'(?:是不是|是否|算不算|不一定|未必).{0,6}规律', source)):
        return '标题把原文明确说的规律改成待确认的规律；不能新增嘉宾没有提出的怀疑'
    invented = re.search(r'(?:未来|后市|趋势|走势).{0,8}(?:不确定|需(?:要)?观察|(?:仍|还)?要看.{0,4}(?:变化|情况|走势))|仍(?:需|要)观察',
                         title + '。' + cover)
    stated = re.search(r'可能|也许|未必|不确定|不一定|不好说|难说|说不准|判断不了|无法判断|不能判断|不能确定|不敢判断|不知道|需.{0,3}观察|再看看|要看.{0,4}(?:变化|情况|走势)',
                       ''.join(evidence))
    if invented and not stated:
        return '标题新增了嘉宾证据中没有的不确定判断；不能用审慎套话改写原话的明确观点'
    return None


def product_contrast_error(title, cover, transcript):
    source=compact(transcript)
    # The actual short source95 says 科技成分 (technological content),
    # not drug ingredients. The model invented an opposing selection rule
    # from the shared word 成分 despite a positive independent verdict.
    if ('科技成分' in source and '成分' not in source.replace('科技成分','')):
        for copy in (title, cover):
            if re.search(r'(?:不(?:能|要|应|该)|别).{0,4}看(?:药物|药品|中药)?成分', compact(copy)):
                return '原文科技成分不是药物成分，不能据此编造不看成分的选药或投资规则'
    if (re.search(r'看好的不是治疗.{0,18}药物',source) and '并发症' in source):
        for copy in (title,cover):
            # Actual 35616565071: the critic approved “看好的不是药物而是
            # 并发症”. A drug mentioned only on the negated side cannot
            # supply the missing product object on the affirmative side.
            positive=re.split(r'而是|不是.+?[，,](?:是)?',copy)[-1]
            if ('并发症' in positive
                    and not re.search(r'产品|药物|药品|器械|用品',positive)):
                return '原文看好的是并发症相关产品，标题和封面须保留产品对象，不能变成看好疾病或诊疗建议'
            if re.search(r'药物|药品|三(?:种|大)病',copy) and '并发症' not in copy:
                return '原文明确转向并发症相关产品，不能把被排除的药物写成看好对象或只留下否定的半句'
            for clause in re.split(r'[，,。；;！？!?]',copy):
                if (re.search(r'(?:药物|药品)(?:的)?(?:市场)?空间',clause)
                        and '并发症' not in clause and not re.search(r'不是|并非|不看好',clause)):
                    return '不能在前半句把被排除的药物写成空间很大的对象；后半句补上并发症不能修正前半句的关系'
    return None


def incremental_cost_error(title, cover, evidence):
    """Do not turn low incremental business investment into no-cost profit."""
    source=compact(''.join(evidence))
    incremental=re.search(r'不(?:需要|用|必).{0,3}再.{0,8}(?:花钱|投资|投入)|不(?:需要|用|必).{0,3}追加',source)
    absolute=re.search(r'不花钱|(?:不靠|不用|无需|不需要)(?:我|去)?花钱|不(?:用|需|需要)投入|零(?:成本|投入)',title+'。'+cover)
    if incremental and absolute:
        return '原文说不必追加投入，标题不能丢掉“再、追加”的范围变成无需成本或投入'
    return None


def personal_action_error(title, cover, evidence):
    """A contrast in what the guest favors is not a stated refusal to invest."""
    claim=title+'。'+cover
    refusal=re.search(r'(?:我(?:们)?(?:就|也|从来|绝对)?|^|[，。：])不(?:投(?:资)?|买(?:入)?)',claim)
    source=compact(''.join(evidence))
    stated=re.search(r'不(?:会|想|去|再|愿|能)?(?:投(?:资)?|买(?:入)?|碰)|没(?:有)?(?:投|买|参与)',source)
    if refusal and not stated:
        return '原文未明确说不投或不买，不能把偏好或讨论对象的对比改成投资行动'
    return None


def loss_claim_error(title, cover, transcript):
    """A directional choice is not an explicit no-loss outcome.

    Observed source68: the 9B critic approved 不亏 on a cover from 不会错.
    Exact source support only bypasses this narrow guard; normal attribution,
    time/condition and independent review still apply afterwards.
    """
    for term in ('不亏', '不会亏', '不会赔', '不赔钱', '保本'):
        if term in compact(title + '。' + cover) and term not in compact(transcript):
            return '不能把选方向不会错改成不亏或保本；标题与封面的损益判断须有原话支持'
    return None


def participation_phase_error(title,cover,transcript):
    # Actual 9B full replay changed 没参与 to 退出, implying a prior position.
    source=compact(transcript)
    if re.search(r'没(?:有)?(?:参与|投(?:资)?|买(?:入)?)',source):
        for action in ('退出','撤资','清仓','卖出','减仓'):
            if action in title+'。'+cover and action not in source:
                return '原文未参与不等于先参与再退出；不得新增退出、卖出或减仓的行动经历'
    return None


def research_scope(text):
    """Observed-company qualifiers must not become industry-wide results."""
    return re.search(r'(?:我|我们)(?:所)?(?:研究|调研|跟踪|考察)(?:过|的)?(?:这些|这几家|的)?公司',compact(text))


def unresearched_reports(transcript):
    """Identify the explicit 'not researched, someone told me' construction.

    This is a bounded safeguard for observed source17, not a general claim
    entailment model. Preserve sentence scope rather than treating every
    statement in a long interview as hearsay.
    """
    reports=[]
    for sentence in re.split(r'[。！？!?；;]',transcript):
        for match in re.finditer(r'(?:听别人说|听说|听闻|有人(?:给|跟|对)我(?:们)?说)(.{4,160})',sentence):
            before=sentence[:match.start()]
            if re.search(r'(?:没|没有)(?:没有)?(?:特意|专门|亲自)?(?:去)?(?:研究|调研|核实|调查|验证)',before):
                reports.append(match[1])
    return reports


def reported_claim_error(title,cover,transcript):
    reports=unresearched_reports(transcript)
    if not reports:return None
    import jieba
    import jieba.posseg
    # Search segmentation exposes a verbal component inside a noun compound
    # such as 环境污染; ordinary POS segmentation emits only the whole noun.
    predicates={word for report in reports for token in jieba.cut_for_search(report)
                for word,tag in jieba.posseg.cut(token)
                if tag.startswith('v') and len(compact(word))>=2
                and word not in {'研究','调研','认为','知道','告诉','说过','参与'}}
    for text in (title,cover):
        if (any(word in text for word in predicates)
                and not re.search(r'听说|听闻|听别人说|有人.{0,6}说|转述|据说',text)):
            return '原话未研究且仅转述他人说法；涉及该判断时标题与封面都须保留听说，未研究不能替代转述限定'
    return None


def population_scope_error(title,cover,transcript):
    """Do not turn an age-group increase into total-population growth."""
    from title_quantity_context import error as quantity_context_error
    issue=quantity_context_error(title,cover,transcript)
    if issue:return issue
    source=compact(transcript)
    if not re.search(r'老龄|老人|老年|年龄越来越大|[五六七八九十0-9]{1,3}岁以上',source):
        return None
    # An explicit total-population claim in the source needs normal review.
    if re.search(r'(?:总人口|人口总量|人口数量|人口)(?:在|持续|不断)?(?:增长|增加|扩大)',source):
        return None
    for copy in (title,cover):
        if re.search(r'(?<!老年)(?<!老龄)人口(?:的)?(?:增长|增加|扩大)',copy):
            return '原文讨论老龄人口或年龄增长，不能改写成总人口增长；标题与封面分别保留对象范围'
    return None


def research_scope_error(title, cover, transcript):
    # Actual source95 said 我们研究的公司. Both 8B and 14B reviewers
    # approved a sector-wide rewrite, omitting that short standalone cue.
    if not research_scope(transcript):
        return None
    for copy in (title, cover):
        if (re.search(r'业绩|营收|利润|股价',copy)
                and not re.search(r'(?:研究|调研|跟踪|考察)(?:过)?的?[\u4e00-\u9fff]{0,6}(?:公司|企业|药企)',copy)):
            return '业绩或股价判断须保留研究公司范围；不能把有限样本改成整个行业，标题与封面分别保留范围'
    return None


def _candidate_error(item, transcript, speaker, existing_titles, check_layout=True):
    from speaker_attribution import other_guest_indices
    if other_guest_indices([transcript],speaker):
        return '片段包含指名其他嘉宾的轮次，不能把多位嘉宾的原声统一归给主讲人'
    title, cover = item.get('title'), item.get('cover_title')
    if not isinstance(title, str) or not title.startswith(speaker + '：'):
        return '标题缺少主讲人前缀'
    if summary_heading(title):
        return '标题必须呈现一个具体观点，不能是主题目录或残句'
    if not copy_length_ok(title.split('：',1)[-1],52):
        return '标题正文须4~52个有效字；短于8字须有明确对象和完整动作，不能写成标签或凑字数'
    if not isinstance(cover, str) or not copy_length_ok(cover,18):
        return f'封面有效字数为{len(compact(cover))}，须4~18字；短于8字须有明确对象和完整动作，不能只写名词标签'
    if summary_heading(cover.removeprefix(speaker)):
        return '封面仍是主题目录；要写出这个片段的具体判断或完整问题'
    if copy_fragment(title) or copy_fragment(cover):
        return '标题或封面截成残句；补全宾语和判断，不能停在真正的、成为龙头的等半句话'
    attribute_issue = subject_attribute_error(title, cover, transcript)
    if attribute_issue:
        return attribute_issue
    qualifier_issue = cover_qualifier_error(title, cover)
    if qualifier_issue:
        return qualifier_issue
    source_condition_issue = source_payback_condition_error(title, cover, transcript)
    if source_condition_issue:
        return source_condition_issue
    range_issue = quantity_range_error(title, cover, transcript)
    if range_issue:
        return range_issue
    forecast_issue = forecast_copy_error(title,cover,transcript)
    if forecast_issue:
        return forecast_issue
    subject_issue = unresolved_subject_error(title,cover)
    if subject_issue:
        return subject_issue
    contrast_issue = product_contrast_error(title,cover,transcript)
    if contrast_issue:
        return contrast_issue
    scope_issue = research_scope_error(title,cover,transcript)
    if scope_issue:
        return scope_issue
    hearsay_issue=reported_claim_error(title,cover,transcript)
    if hearsay_issue:return hearsay_issue
    population_issue=population_scope_error(title,cover,transcript)
    if population_issue:return population_issue
    loss_issue=loss_claim_error(title,cover,transcript)
    if loss_issue:return loss_issue
    phase_issue=participation_phase_error(title,cover,transcript)
    if phase_issue:return phase_issue
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
    if any(not evidence_usable(q) or compact(q) not in source for q in evidence):
        return '观点证据不是这段真实原文'
    hedge_issue = unsupported_hedge_error(title, cover, evidence)
    if hedge_issue:
        return hedge_issue
    cost_issue = incremental_cost_error(title, cover, [transcript])
    if cost_issue:
        return cost_issue
    action_issue = personal_action_error(title, cover, evidence)
    if action_issue:
        return action_issue
    # An observed 4B-model false positive inferred "更安全" from position sizing.
    # Such financial claims need explicit evidence even if a reviewer says true.
    risk_claims=('更安全','更稳妥','更稳健','风险更低','风险小','降低风险','避险',
                 '收益更高','回报更高','更赚钱','最赚钱','稳赚','保证收益',
                 '盈利的保障','盈利保障','赚钱的保障','赚钱保障','收益的保障','收益保障',
                 '确保盈利','确保赚钱','保证盈利','保证赚钱',
                 '不用怕','不用担心','不必担心','无需担心','放心买','没风险','让我安心','让人安心',
                 '粘性强','粘性更强','黏性强','黏性更强','超预期','不值')
    stated=compact(''.join(evidence))
    relation_issue=relation_error(title,cover,transcript)
    if relation_issue:return relation_issue
    if any(term in compact(title+cover) and term not in stated for term in risk_claims):
        return '标题新增了所选嘉宾原文没有的比较或经营判断'
    subject = item.get('subject')
    if not isinstance(subject, str) or not 2 <= len(compact(subject)) <= 12 or subject in ABSTRACT_ANCHORS:
        return '缺少具体讨论对象'
    if compact(subject) not in compact(title) or not any(compact(subject) in compact(q) for q in evidence):
        return '标题对象与原文证据不对应'
    if any(compact(subject)*2 in compact(copy) for copy in (title,cover)):
        return '标题或封面重复写了同一对象，不能把转写口吃当标题'
    # The semantic review also checks written numbers, attribution and negation.
    for number in re.findall(r'\d+(?:\.\d+)?[%％]?', title + cover):
        if number not in re.findall(r'\d+(?:\.\d+)?[%％]?', transcript):
            return '标题或封面添加了原文没有的数字'
    return None


def _binding(title, cover, subject, evidence):
    return hashlib.sha256(json.dumps([title, cover, subject, evidence], ensure_ascii=False).encode()).hexdigest()


def editorial_features(item):
    """Tie-break fact-checked copy, not a prediction of clicks or factuality."""
    title = item['title'].split('：', 1)[-1]
    evidence = ''.join(item.get('evidence') or [])
    subject = item.get('subject') or ''
    # Only reward an observable contrast/first-person choice also in evidence.
    contrast = r'但是|但|却|不是|不买|不卖|不能|不要|而是'
    first_person = r'我(?:们)?(?:买|不买|不卖|持有|看|投)'
    return dict(
        subject_early=bool(subject and 0 <= title.find(subject) < 12),
        sourced_contrast=bool(re.search(contrast, title) and re.search(contrast, evidence)),
        sourced_voice=bool(re.search(first_person, title) and re.search(first_person, evidence)),
        concise=copy_length_ok(title,24),
        generic=bool(re.search(r'坚持投资理念|抓住机遇|核心策略|深度解读|投资逻辑解析', title)),
    )


def select_reviewed_candidate(accepted, candidates):
    """Retain the review gate; resolve ubiquitous 4/5 ties by explicit features."""
    def key(row):
        f = editorial_features(candidates[row['index']])
        return (row['appeal'], not f['generic'], f['sourced_contrast'],
                f['subject_early'], f['sourced_voice'], f['concise'])
    # Lexical final tie-break makes candidate order irrelevant.
    return sorted(accepted, key=lambda r: (tuple(-int(x) for x in key(r)),
                  candidates[r['index']]['title']))[0]


def _package(item, transcript, review, candidates):
    title, cover = item['title'], item['cover_title']
    review = {**review, 'copy_sha256':_binding(title, cover, item['subject'], item['evidence'])}
    proof = dict(version=VERSION, kind='editorial_claim', title=title, cover=cover,
                 subject=item['subject'], evidence=item['evidence'], review=review,
                 cover_qualifier_policy=2026092101,
                 quantity_range_policy=2026092101,
                 source_hedge_policy=2026092101,
                 source_sha256=hashlib.sha256(compact(transcript).encode()).hexdigest())
    return dict(title=title, cover_title=cover, title_rewrite=proof,
                title_candidates=[c['title'] for c in candidates], title_quality_verified=True,
                packaging_method='source_claim_editor', desc='本段讨论：' + title.split('：', 1)[1])


def _extractive(transcript, speaker, existing_titles, preferred=None, guest_passages=None,
                only_preferred=False):
    from headline_policy import title_candidates, body, cover_copy, complete
    # Never stitch across a host turn or pick a host's more fluent question.
    from speaker_attribution import other_guest_indices
    if guest_passages is None and other_guest_indices([transcript],speaker):
        raise ValueError('包含明确指名其他嘉宾的回答，不能用全文摘句归给主讲人')
    passages = [transcript] if guest_passages is None else guest_passages
    titles = [title for passage in passages
              for title in title_candidates(passage, speaker, existing_titles)]
    if preferred and preferred not in titles:
        titles.append(preferred)
    if only_preferred:
        titles = [preferred] if preferred else []
    for title in titles:
        if only_preferred and any(difflib.SequenceMatcher(None, compact(title), compact(old)).ratio() >= .84
                                  for old in existing_titles):
            continue
        quote = body(title, speaker)
        if not complete(quote) or compact(quote) not in compact(transcript) or summary_heading(title):
            continue
        if not any(compact(quote) in compact(passage) for passage in passages):
            continue
        cover = cover_copy(title, quote, speaker)
        if cover.get('reason') == 'needs_editorial_copy':
            continue
        if (copy_fragment(cover['text']) or subject_attribute_error(title, cover['text'], transcript)
                or research_scope_error(title, cover['text'], transcript)
                or reported_claim_error(title,cover['text'],transcript)):
            continue
        if cover_qualifier_error(title, cover['text']):
            continue
        if source_payback_condition_error(title, cover['text'], transcript):
            continue
        if quantity_range_error(title, cover['text'], transcript):
            continue
        if (forecast_copy_error(title, cover['text'], transcript)
                or unresolved_subject_error(title, cover['text'])
                or population_scope_error(title, cover['text'], transcript)
                or loss_claim_error(title, cover['text'], transcript)
                or participation_phase_error(title, cover['text'], transcript)
                or incremental_cost_error(title, cover['text'], [transcript])
                or product_contrast_error(title, cover['text'], transcript)
                or unsupported_hedge_error(title, cover['text'], [quote])):
            continue
        # Whole source claims are the fallback; not a list of detected subjects.
        item = dict(title=title, cover_title=cover['text'], evidence=[quote], subject=quote)
        if not copy_length_ok(title.split('：',1)[-1],52):
            continue
        review = dict(method='source_quote', quote=quote)
        if guest_passages is not None:
            review['attribution'] = 'reader_guest_passage'
        return _package(item, transcript, review, [item])
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
             structured_model=None, source_cues=None, answer_focus=False, answer_subject=False,
             prefer_reviewed_quote=False):
    if prefer_reviewed_quote and (not preferred or not structured_model or answer_focus):
        raise ValueError('核对过的金句须提供原句并先完成完整嘉宾归属阅读')
    if answer_subject and not answer_focus:
        raise ValueError('对象交接仅适用于独立阅读主要回答的实验配置')
    if model is None and structured_model is None:
        return _extractive(transcript, speaker, existing_titles, preferred)
    units = list(source_cues) if source_cues else source_units(transcript)
    if any(not isinstance(u,str) for u in units) or ''.join(units)!=transcript:
        raise ValueError('标题原始字幕边界与完整原文不一致')
    if answer_focus:
        units=answer_reading_units(units,speaker)
    subjects = subject_catalog(units) if structured_model else {}
    def call(prompt, schema):
        return structured_model(prompt, schema) if structured_model else model(prompt)
    from speaker_attribution import other_guest_indices
    host_ids=sorted(explicit_host_cues(units) | other_guest_indices(units,speaker))
    reader_prompt=f'''只做访谈原文阅读，不拟标题，不比较吸引力。主讲嘉宾是{speaker}。
根据明确的提问、主持人复述与指名其他嘉宾的轮次，以下编号不能归为{speaker}回答：{host_ids}。
这些只是排除项；其余编号的说话人仍须通读全文判断，不能自动视为嘉宾。
先通读全部字幕，找主持人的完整问题和嘉宾实际回复。主持人提问前的背景、假设、举例仍属于主持人；嘉宾短答不等于确认问题全部前提。
同一人的连续讲话是一个轮次，不能因为换了一条字幕或出现问号就换说话人。字幕标点可能不准，必须连贯理解上下文。
先在a_guest_answer用最多三句概括嘉宾明确回答的主要判断及限定条件，不抄整篇字幕，不混入主持人的总结。
再用b_question_premise概括主持人问题和未确认假设。两个概括各不超过240字。
读清问答后，最后在c_guest_spans标出嘉宾每段明确回答的原文范围：a_start为回答第一条字幕编号，b_end为回答最后一条字幕编号，两端都包含。
只列嘉宾的实际回答，不能只列主持人的提问起点。连续回答合为一段，区间按顺序、不重叠；主持人的追问、第三人称概括及结束语必须留在范围外。
其他字幕全部保留给独立复核，但不会作为嘉宾原话。说话人不清楚的句子不要选入嘉宾范围，不要为了覆盖全文而把主持人算进来。
“您觉得”“你怎么看”通常是主持人的提问；“我听懂了”“我来总结”通常是主持人复述，不能当嘉宾原话。
没有主持人时写“无主持人提问”。必须阅读所有字幕，不因某段提问较长就把它当嘉宾观点。只输出JSON。
按原顺序编号的完整字幕：{json.dumps(dict(enumerate(units)),ensure_ascii=False)}'''
    reading=None;roles=None
    main_ids=[];bound_subject=None
    if answer_focus:
        reader_prompt=f'''完整阅读这段访谈，主讲嘉宾是{speaker}。此时不拟标题、不评价吸引力。
显示换行已合并到原有句末，文字一个未改。先读到全文结尾，区分问题、回答、理由与举例。
先在a_guest_answer用最多三句说清嘉宾实际回答的主要判断与限定，b_question_premise说明主持人问什么、哪些前提嘉宾没有确认；无主持人则写无主持人提问。主要回答不一定在开头。
然后在c_sentence_roles逐句标出说话人：guest仅指{speaker}本人，host是提问者、主持人或其他嘉宾，unknown表示不能确认。每个u编号都要判断，不只标主要回答；明确的其他回答、理由和例子也须按说话人保留，不能因不适合做标题就省略。
主持人提问的背景、假设和复述仍是主持人；嘉宾短答不等于确认全部前提。相邻句可能属于同一人，不因问号就换人；不清楚时保留unknown，不能猜身份。
以下编号已有明确提问或其他嘉宾轮次证据，不能作为{speaker}原话：{host_ids}。其他编号仍须独立判断。
最后在d_main_answer_quote选一段最直接回答主要问题的连续原句，保留否定、条件和语气。不能用例子代替回答，也不能改写原句。选出的原句必须属于已确认的guest。
先分清主要问题是在问实际选择、经历、判断，还是理由。问买没买、参没参与，而嘉宾明确回答了买或没买、参与或没参与时，这项实际选择就是直接回答；后面的原因和行业解释用于说明它，不能自动替代它。只有原文确实回答了个人选择才按此处理，不能从看好前景推断已经买入。
从直接回答与解释中，选择最能说明嘉宾实际答复的一句。含代词的直接答复仍可按原句保留，拟稿时再从已确认的嘉宾上下文补足对象；不能为了句子更像书面总结而跳过这句回答。“我讲一下为什么”等开场预告不是实际回答。
完整原文：{json.dumps({f'u{i:04d}':u for i,u in enumerate(units)},ensure_ascii=False)}
精确原句选项（包含主持人原句，并不代表归属已确认）：{json.dumps(main_answer_quotes(units),ensure_ascii=False)}
仅输出JSON。'''
        if answer_subject:
            reader_prompt+='''\n在e_subject_name写明这项主要回答实际讨论的具体对象（产品、公司、行业或市场的名称），不能用它、原因、标准等泛词。须逐字摘取原文中的名称，不自行扩成整个行业。
程序会在已确认的嘉宾原句中逐字定位这个名称，可以出现在同一问答的后续解释。主持人问题只帮助理解指代，不能作为名称证据，也不能把主持人的假设附加到名称上。拿不准时不要猜。'''
    reading_repair_note=''
    def fallback(only_preferred=False):
        if answer_focus:
            raise ValueError('主要回答标题尚未通过，保留重试；不回退到旁枝摘句')
        if not structured_model:
            return _extractive(transcript, speaker, existing_titles, preferred)
        if reading is None or roles is None:
            raise ValueError('未确认嘉宾原话归属，不能用全文摘句代替标题审核；保留素材等待重试')
        from speaker_attribution import other_guest_indices
        blocked = explicit_host_cues(units) | other_guest_indices(units,speaker)
        passages=[]; current=[]
        for i, unit in enumerate(units):
            if roles[i] == 'guest' and i not in blocked:
                current.append(unit)
            elif current:
                passages.append(''.join(current)); current=[]
        if current:
            passages.append(''.join(current))
        return _extractive(transcript, speaker, existing_titles, preferred,
                           guest_passages=passages, only_preferred=only_preferred)
    prompt = f'''你是B站视频编辑，要写自然、有看点、忠于访谈的中文标题。
先分清主持人的提问、猜测与嘉宾已经回答的内容。中心观点按嘉宾回答的信息量选择，不能按主持人的发言长度或关键词频率选择。
嘉宾没有确认的新品表现、未来变化和问题前提，不能写成嘉宾的观点。优先写嘉宾明确表达的观察和判断，保留转折后的限定条件。
同一对象同时有正反两面判断时，应一起保留；相对预期的比较不能替代实际情况的程度限定，标题和封面都不能只摘其中一面。
问答轮次已由单独的原文阅读步骤划分，不能为了写标题而改动说话人。只能根据列出的嘉宾原话选择中心观点。
再在b_focus.a_claim用一句完整的话写出上述嘉宾回答里信息最充分的核心判断、做法及限定条件，
“不是A，而是B”的回答要写清B，不能只摘否定的前半句。市场空间不等于估值；提到药物不等于评价疗效，不能自行加“效果待观察”。
用b_focus.b_evidence_ids选1~4条支撑它的guest原文编号；不能选host或unknown。不要把主持人的猜测或一处举例当成中心观点。
最后在c_candidates为同一观点写3个不同角度的标题：原话中的鲜明判断、具体做法、这段确实回答的问题。不要三个角度都写成“为什么”。
文风像嘉宾在当面说话，不像编辑在写研究报告。优先保留嘉宾原话里有辨识度的动词、语气和具体对象，把最有看点的判断放在前半句。
嘉宾明确说自己的选择时，可以保留“我买”“我不卖”“我看的是”等第一人称；原文没说，不能为了像林园而编一句“金句”，也不能把主持人的话改成“我”。
不要在原话之外补“投资逻辑解析”“深度解读”“核心策略”“价值重估”等总结包装；原文确实讨论这些概念时，可以用，但仍要说出具体判断。
吸引力来自原文里真实的分歧、选择或反问，不来自收益承诺、吓人字眼或故意藏起讨论对象。转折、否定、条件和“可能”等限定必须保留。
每条title以“{speaker}：”开头，正文4~52个汉字；cover_title为4~18个汉字，不加姓名。
完整短句不凑字数；短于8字时须明确说出对象和动作，不能仅列名词。封面不强求填满两行。
每条标题必须明确说出讨论对象，并包含至少一个嘉宾原文对象词：__TITLE_SUBJECTS__。
保留这些词本身及其关系，不把原文对象换成“潜力股”等含义不同的金融标签。
{COPY_FACT_CONSTRAINTS}
用日常说话的完整句子。禁止“谈A、B与C”等关键词目录，禁止术语堆砌、换行、空格和无意义尾巴凑字数。
例如原文说“利润涨了但货款收不回，暂时不买”，标题可以问“利润在增长，为什么还要先看回款？”
这个例子只说明文风，不能套用它的事实。不得夸大收益、安全性、因果或删掉否定和不确定性。
只输出JSON。__TITLE_SOURCE__'''
    last_error = ''
    repair_checks=set()
    structural_repairs=[]
    for attempt in range(3):
        try:
            # Finish with the source, not three repetitions of a rejected claim.
            # In run 147 the retry feedback outweighed the actual guest answer
            # and the same host hypothesis returned in every round.
            retry_note = (f'第{attempt + 1}轮重新阅读；以下是已退回的错误稿，不能当作原文事实：'
                          + last_error + '\n\n请回到下面完整原文重新判断：\n' if last_error else '')
            if structured_model and reading is None:
                proposed_reading=_json(call((reading_repair_note or retry_note)+reader_prompt,
                    reading_schema(len(units),answer_focus,units if answer_focus else None,answer_subject)))
                reading_repair_note=''
                roles=bind_reading(proposed_reading,units)
                reading=proposed_reading
            guest_ids=guest_evidence_ids(units,roles,speaker) if structured_model else []
            if answer_focus:
                try:
                    main_ids=bind_answer_focus(reading,units,roles,speaker)
                    if answer_subject:
                        bound_subject=bind_answer_subject(reading,units,roles,speaker)
                except ValueError:
                    reading=None;roles=None
                    raise
            if structured_model and not guest_ids:
                reading=None
                raise ValueError('嘉宾回答中没有达到原文证据长度的条目，不能选主持人或短语凑证据')
            if prefer_reviewed_quote:
                return fallback(only_preferred=True)
            if structured_model:
                subjects={word:[i for i in ids if i in guest_ids] for word,ids in subject_catalog(units).items()}
                subjects={word:ids for word,ids in subjects.items() if ids}
            draft_prompt=prompt.replace('__TITLE_SUBJECTS__',json.dumps(list(subjects),ensure_ascii=False) if subjects else '用原文中的讨论对象')
            draft_retry=retry_note
            if structured_model:
                # Run 164 selected real guest evidence yet copied "粘性强"
                # from the host/raw reading summary into every rejected draft.
                # The reader and independent reviewer retain the full dialogue;
                # factual drafting receives only guest source statements. Keep
                # short guest cues too: they may contain a negation/qualifier.
                blocked=explicit_host_cues(units)
                draft_source={i:u for i,u in enumerate(units) if roles[i]=='guest' and i not in blocked}
                source_heading='以下是可用于标题事实的嘉宾原话，按原始编号排列。保留短句中的转折和限定；不得补充问题假设或常识推断：\n'
                if answer_focus:
                    source_heading+=f'独立阅读在未看到候选标题时选出的主要回答编号：{main_ids}。主要回答原话：{reading["d_main_answer_quote"]}。三个候选都围绕这个判断换说法，每个候选必须表达它并引用至少一条对应编号，不能只往旁枝标题附上编号。保留该判断自己的应该、可能、我相信等语气，不能只摘后面的例子或解释。其他原文只用于理解和补足同一判断的限定；阅读步骤的概括不作为新事实。\n'
                    source_heading+='以下原文对象词及编号只用于定位上下文，不代表已经确定主回答对象；补足代词时须读取对应完整嘉宾句子并列入证据：'+json.dumps(subjects,ensure_ascii=False)+'\n'
                    if bound_subject:
                        source_heading+='阅读阶段确认的本回答对象及嘉宾原句：'+json.dumps(bound_subject,ensure_ascii=False)+'。每个候选的标题和封面都写明此对象，并引用主要回答和对象原句；不能只留下没买、这个位置等片段。\n'
                if research_scope(''.join(draft_source.values())):
                    source_heading+='原话限定的是自己研究、调研的公司。若写业绩或股价，标题和封面各自保留研究公司范围，不能说成整个行业。短字幕里的范围也是事实，不能因字数短省掉。\n'
                if unresearched_reports(''.join(draft_source.values())):
                    source_heading+='原话明确未研究，并只转述别人说法。涉及这条转述的判断时，标题与封面各自保留“听说”；仅写“未研究”不等于保留转述。也可以只写本人明确的做法，不把转述写成已证实的结论。“没参与”是没有参加，不是先参加再“退出”，不可新增持仓经历。\n'
                if relation_error('选择龙头','选择龙头',''.join(draft_source.values())):
                    source_heading+='原文明确说龙头尚未形成。写到龙头时，标题与封面必须保留“没有、尚未、未来、可能成为”等原有阶段；不能写成选定现成龙头，不能只添加限定词却保留相反做法。\n'
                draft_retry=(f'第{attempt+1}轮重新拟稿；上轮未通过原文或文案检查。只从下方嘉宾原话重新提炼判断，不延续上轮措辞。\n' if last_error else '')
                # Preserve actionable failed checks, without injecting the old
                # fabricated claim into factual drafting. Run169 discarded all
                # qualifier feedback and repeated the same omission three times.
                repairs={
                    'preserves_qualifiers':'同一对象的正反两面判断必须一起保留，包括相对预期的比较和对实际情况的限定；不能只写其中一面，也不能扩大讨论对象的范围。',
                    'source_supported':'逐项删除原话没有明说的比较、因果和结论，不从主持人问题或常识补事实。',
                    'attribution_correct':'只写明确属于嘉宾回答的判断，不能把提问里的假设归给嘉宾。',
                    'central_point':'重新选择有完整解释支持的主要判断，不能只摘旁枝例子。',
                    'cover_consistent':'封面必须对应标题同一个对象与判断，不能比标题更肯定。',
                    'readable':'用自然完整的中文短句，保留对象、动作和宾语，不能拼术语或截半句。',
                }
                corrections=[k+'：'+repairs[k] for k in CHECKS if k in repair_checks]
                if answer_focus:
                    corrections += ['structural：'+issue for issue in structural_repairs]
                if corrections:
                    draft_retry+='上轮退回的检查项及本轮必须执行的修正（不是事实来源）：\n'+'\n'.join(corrections)+'\n'
            else:
                draft_source=dict(enumerate(units))
                source_heading='下面是按原顺序编号的完整字幕，未删改：\n'
            draft_prompt=draft_prompt.replace('__TITLE_SOURCE__',source_heading+json.dumps(draft_source,ensure_ascii=False))
            proposal = _json(call(draft_retry + draft_prompt,
                                  proposal_schema(len(units), subjects,guest_ids if structured_model else None)))
            candidates = proposal.get('c_candidates' if structured_model else 'candidates')
            if not isinstance(candidates, list) or len(candidates) != 3:
                raise ValueError('必须提供三个不同角度的候选标题')
            # A structured production call never accepts model-authored evidence.
            if structured_model:
                raw_focus=proposal.get('b_focus') or {}
                # One malformed sibling must not discard source-bound drafts.
                bound = []
                for candidate in candidates:
                    try:
                        item=bind_guest_candidate(candidate,raw_focus,units,subjects,guest_ids)
                        if bound_subject:
                            item=require_answer_subject(item,bound_subject)
                        bound.append(require_answer_focus(item,main_ids) if answer_focus else item)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        bound.append({})
                candidates = bound
            errors = [(_candidate_error(c, transcript, speaker, existing_titles)
                       if isinstance(c, dict) else '候选不是JSON对象') for c in candidates]
            # Review every distinct, structurally valid draft in this attempt.
            # A malformed sibling is not a reason to regenerate good copy.
            # These are proposals, never approvals or cross-source cache data.
            candidate_pool={}
            for candidate,issue in zip(candidates,errors):
                if not issue:
                    candidate_pool[compact(candidate['title'])]=candidate
            valid = list(candidate_pool.values())[:3]
            if not valid:
                # Real source13 retried the same missing-object draft three
                # times. Feed back the program's checks, not its failed copy.
                if answer_focus:
                    structural_repairs=list(dict.fromkeys(issue for issue in errors if issue))[:3]
                # Real replay 35613314961: reading omitted the guest's earlier
                # 牛市 / 不好预测 cues, so three writers could not name the
                # forecast event. Request a fresh reading; never relabel the
                # omitted cues as guest ourselves or bypass attribution review.
                guest_text=''.join(draft_source.values())
                if (structured_model
                        and any(issue and '时间概率缺少具体事件' in issue for issue in errors)
                        and re.search(r'牛市|指数|市场',transcript)
                        and not re.search(r'牛市|指数|市场',guest_text)):
                    reading=None;roles=None
                    reading_repair_note=(f'第{attempt+2}轮重新阅读：可用嘉宾范围缺少时间判断的具体事件，'
                        '拟稿无法独立说明发生什么。请重新通读完整问答，检查是否误把回答前半段当成问题；'
                        '不能因需要对象而把主持人假设归给嘉宾。若确实缺少语境，应保留未知，不补写事实。\n')
                issues=[f"{c.get('title','')} / {c.get('cover_title','')}"
                        f"（对象={c.get('subject')},证据编号={c.get('evidence_ids')}）：{issue}"
                        for c,issue in zip(candidates,errors) if issue]
                raise ValueError('没有可送独立复核的候选；需修正：' + '；'.join(issues))
            # If semantic review rejects the set (or fails to return a valid
            # verdict), the next attempt must not silently reuse rejected copy.
            dialogue_context = (json.dumps([dict(id=i,text=u) for i,u in enumerate(units)],
                                ensure_ascii=False) if structured_model else transcript)
            judge = f'''独立核对这些视频标题与完整字幕，只评价下方实际候选的标题和封面，不重做选段或字幕审核。
先完成a_analysis：a_guest_answer只概括嘉宾实际回答，b_question_premise列出主持人的问题前提；
然后c_reason引用本候选实际出现的短语，与原文中对应的嘉宾回答比较。最后才填b_verdict中的各个判定。
标为“原文”“原话”“字幕”的引号内容必须逐字复制实际字幕，不能把自己的概括放进原文引号；概括须明确写为概括。
不得指出候选没有写过的词，不能空填全部true。原文出现某个词不能证明是嘉宾的观点，主持人长段提问中的假设也不算嘉宾确认。
区分主持人提问中的猜测和嘉宾明确给出的回答，标题不能把前者归为嘉宾观点。
不合格时说明原文实际的做法或判断，再指出标题偏差，供下一轮修正中心观点和措辞。
严格寻找实际标题/封面新增的比较、因果、收益和安全性判断。只有候选确实写出了新增判断，才能以此判source_supported=false。
保留原文中的否定、程度、转折和不确定性，不把有限的肯定扩大成整体乐观，不改变讨论对象间的关系。
同时检查是否凭空添加审慎结尾：明确判断不能被改写成不确定判断。更谨慎的句子也可能不忠于原话。
逐个判断核对语气：别处出现“可能”不代表整段观点都不确定。“就是这么个规律”不支持“得看是不是规律”。
如果全段没有主持人讲话，b_question_premise写“无主持人提问”，不要补出一段未出现的问题或引导。
判断限定是否保留要比较实际含义，不能仅因使用等义的日常表达而拒绝。完整问句可以是标题或封面；不能仅因它未提前揭示答案就判不完整，但问题前提仍必须有原文支持。
拗口的术语堆砌、主体关系错误、把有限的肯定扩大成整体乐观，分别判readable、source_supported、preserves_qualifiers=false。
逐条检查：source_supported原文支持；central_point抓住中心而不是举例或旁枝；
attribution_correct没有把主持人的猜测归为嘉宾断言；preserves_qualifiers保留条件否定和不确定性；
cover_consistent封面和标题同一观点且没有更强断言；readable自然好懂。
appeal按具体看点和想点开的程度评1~5，空泛目录只能1分。相同事实下优先嘉宾原话式的鲜明判断和自然短句；研究报告式总结、模板套话、三个角度重复的问题句应低分。
第一人称必须是嘉宾自己的选择，不能把主持人问题包装成嘉宾金句。口语化不能省略原文的否定、条件、比较对象或不确定性。严格输出布尔值，不因文字流畅而放过编造。
返回JSON的reviews数组，每项先a_analysis（a_guest_answer、b_question_premise、c_reason），
再b_verdict（index、source_supported、central_point、attribution_correct、preserves_qualifiers、cover_consistent、readable、appeal）。
待独立核对的标题和封面：{json.dumps([dict(title=c['title'],cover_title=c['cover_title']) for c in valid], ensure_ascii=False)}
原文编号只帮助定位，依据中也可能含主持人的问题，必须与上下文分清说话人。若嘉宾确实说出了某个判断，不能仅因主持人也提到它就判归属错误。
不提供上游模型的说话人标签或所选证据，必须从前后问答独立判断谁说了什么。
只提供由明确提问/复述句式得到的排除编号：{host_ids}。这些句子及延续的问题不能当成嘉宾原话；其他句子仍须独立判断。
只用guest真正说出的内容支撑标题事实；host只提供问题背景，不能把未被回答确认的假设写进标题。
完整字幕：{dialogue_context}'''
            reviews = _json(call(judge, review_schema(len(valid)))).get('reviews', [])
            if not isinstance(reviews, list) or len(reviews) != len(valid):
                raise ValueError('独立复核必须逐一覆盖实际送审候选')
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
            indices = [row.get('index') if isinstance(row, dict) else None for row in reviews]
            if (any(type(i) is not int for i in indices)
                    or sorted(indices) != list(range(len(valid)))):
                raise ValueError('独立复核编号重复、遗漏或越界，不能据此选稿')
            accepted = []
            for row in reviews:
                if isinstance(row,dict):
                    quote_issue=review_source_quote_error(row.get('reason'),transcript)
                    if quote_issue:
                        row['source_supported']=False
                        row['reason']=quote_issue
                if (isinstance(row, dict) and type(row.get('index')) is int and 0 <= row['index'] < len(valid)
                        and all(row.get(k) is True for k in CHECKS)
                        and isinstance(row.get('reason'),str) and len(compact(row['reason'])) >= 12
                        and type(row.get('appeal')) is int and 3 <= row['appeal'] <= 5):
                    accepted.append(row)
            if not accepted:
                repair_checks={k for row in reviews if isinstance(row,dict)
                               for k in CHECKS if row.get(k) is not True}
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
            winner = select_reviewed_candidate(accepted, valid)
            item = valid[winner['index']]
            result = _package(item, transcript, dict(method='cpu_text_review', **winner), valid)
            if answer_focus:
                result['answer_focus_reading']=dict(main_answer_ids=main_ids,
                    main_answer_quote=reading['d_main_answer_quote'],
                    exact_source=[units[i] for i in main_ids],reader=reading,
                    transcript_sha256=hashlib.sha256(transcript.encode()).hexdigest(),
                    reading_unit_policy='exact_continuations_preserve_exclusions_v1',
                    independent_review_unchanged=True,
                    limitation='Source-bound main-answer proposal, not a human editorial approval')
                if bound_subject:
                    result['answer_focus_reading']['subject']=bound_subject
            result['editorial_selection'] = dict(
                policy='review_then_source_features_v1', attempt=attempt+1,
                reviewed_count=len(valid), accepted_count=len(accepted),
                selected_index=winner['index'],
                candidates=[dict(title=c['title'], features=editorial_features(c),
                    accepted=i in {r['index'] for r in accepted}) for i,c in enumerate(valid)],
                note='同分排序依据，不代表点击率或新增事实核验')
            return result
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError) as exc:
            if getattr(exc,'retryable_service',False):
                # A timed-out model is not editorial feedback. Do not enqueue
                # three full-transcript requests behind the still-busy server.
                # Preserve the existing complete-source-quote fallback; it must
                # pass its own evidence/readability checks before use.
                try:
                    return fallback(only_preferred=prefer_reviewed_quote)
                except ValueError:
                    raise exc
            last_error = str(exc)
            print(f'[标题观点] 第{attempt + 1}次生成待修正：{last_error}', flush=True)
    return fallback(only_preferred=prefer_reviewed_quote)


def error(title, proof, transcript=None, speaker='林园'):
    from speaker_attribution import other_guest_indices
    if transcript and other_guest_indices([transcript],speaker):
        return '标题原文包含指名其他嘉宾的轮次，须重新核对发言归属'
    if (not isinstance(proof, dict) or proof.get('version') != VERSION
            or proof.get('kind') != 'editorial_claim' or summary_heading(title)):
        return '标题需重新提炼具体观点，不能使用旧的关键词拼盘'
    if title != proof.get('title') or not isinstance(proof.get('cover'), str):
        return '标题或封面与观点证明不一致'
    if copy_fragment(title) or copy_fragment(proof['cover']):
        return '标题或封面截成残句，旧审核证明不能替代完整对象'
    qualifier_issue = cover_qualifier_error(title, proof['cover'])
    if qualifier_issue:
        return qualifier_issue
    source_condition_issue = source_payback_condition_error(title, proof['cover'], transcript or '')
    if source_condition_issue:
        return source_condition_issue
    evidence = proof.get('evidence')
    if not isinstance(evidence, list) or not evidence or any(not isinstance(q, str) for q in evidence):
        return '标题缺少完整原文证据'
    range_issue = quantity_range_error(title, proof['cover'], transcript or title + ''.join(evidence))
    if range_issue:
        return range_issue
    for issue in (forecast_copy_error(title, proof['cover'], transcript or ''.join(evidence)),
                  subject_attribute_error(title, proof['cover'], transcript or ''.join(evidence)),
                  unresolved_subject_error(title, proof['cover']),
                  product_contrast_error(title, proof['cover'], transcript or ''.join(evidence)),
                  research_scope_error(title, proof['cover'], transcript or ''.join(evidence)),
                  reported_claim_error(title, proof['cover'], transcript or ''.join(evidence)),
                  population_scope_error(title, proof['cover'], transcript or ''.join(evidence)),
                  loss_claim_error(title, proof['cover'], transcript or ''.join(evidence)),
                  participation_phase_error(title, proof['cover'], transcript or ''.join(evidence)),
                  incremental_cost_error(title, proof['cover'], [transcript or ''.join(evidence)]),
                  unsupported_hedge_error(title, proof['cover'], evidence)):
        if issue:
            return issue
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
        # A cached proof's selected evidence may omit other real source lines.
        # Only declare a source quote absent when the full transcript is given.
        quote_issue=review_source_quote_error(review['reason'], transcript)
        if quote_issue:
            return quote_issue
        item = dict(title=title, cover_title=proof['cover'], subject=proof.get('subject'), evidence=evidence)
        issue = _candidate_error(item, transcript or ''.join(evidence), speaker, (), check_layout=False)
        if issue:
            return issue
    else:
        return '标题审核方法缺失'
    return None
