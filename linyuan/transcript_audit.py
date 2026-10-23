"""Source-bound text plausibility review, explicitly not audio verification."""
import json
import re
from pathlib import Path

from editorial_evidence import sentences
from editorial_policy import text_digest
from transcript_review_evidence import AUDIO_ACCESS_CLAIM

VERSION = 4
SPOKEN_RANGE_POLICY_VERSION = 2026102311

CRITICAL_TOKEN = re.compile(r'[0-9零〇一二两三四五六七八九十百千万亿兆点分成倍%％不没无未非否]')
NO_CONTAMINATION = re.compile(
    r'(?:并非|非|不是|不属于|不构成|未构成|不算)(?:明显的|任何)?(?:识别错误|识别污染|识别疑点)'
    r'|(?:无|没有|未发现|不存在)(?:(?:具体|实际|明确|明显|任何|真正|相关|的))*'
    r'(?:识别错误|识别污染|识别疑点)'
    r'|(?:此项|该项|本项)(?:暂不|不|无需|不应)(?:再)?(?:列为|作为|认定为)(?:识别)?错误')


def bind_layout_quote(quote,unit):
    """Bind a NEGATIVE quote with Latin/CJK layout spaces, never edit source.

    Digits, CJK/CJK and Latin/Latin spaces remain significant. There must be
    exactly one contiguous source match, with every source character intact.
    """
    latin=lambda c:bool(re.fullmatch('[A-Za-z]',c))
    cjk=lambda c:bool(re.fullmatch('[\u4e00-\u9fff]',c))
    plain=re.sub(r'(?<=[A-Za-z])\s+(?=[\u4e00-\u9fff])|(?<=[\u4e00-\u9fff])\s+(?=[A-Za-z])','',quote)
    pattern=''
    for i,char in enumerate(plain):
        pattern+=re.escape(char)
        if i+1<len(plain) and ((latin(char) and cjk(plain[i+1])) or (cjk(char) and latin(plain[i+1]))):
            pattern+=r'\s*'
    matches=list(re.finditer(pattern,unit))
    if len(matches)==1 and 1<=len(matches[0].group())<=24:return matches[0].group()
    return None


def ordinary_disfluency(quote):
    """Only exact repeated dictionary words or standalone fillers are harmless.

    Numbers and negations are never normalized or waived. Production subtitles
    are untouched; this prevents a style objection becoming an ASR failure.
    """
    if quote in ('嗯','呃','啊','唉','的话'):return True
    if CRITICAL_TOKEN.search(quote):return False
    match=re.fullmatch(r'([\u4e00-\u9fff]{2,6})\1{1,2}',quote)
    if not match:return False
    import jieba
    jieba.initialize()
    return bool(jieba.get_FREQ(match[1]))


def review(text, speaker, model, call, path):
    path = Path(path)
    identity = dict(version=VERSION, transcript_sha256=text_digest(text), speaker=speaker, model=model,
                    spoken_range_policy_version=SPOKEN_RANGE_POLICY_VERSION)
    if path.exists():
        try:
            saved = json.loads(path.read_text())
            if (all(saved.get(k)==v for k,v in identity.items()) and isinstance(saved.get('issues'),list)
                    and saved.get('verdict') in ('clear','needs_audio')
                    and (saved['verdict']=='clear')==(not saved['issues'])):
                return {**saved,'passed':not saved['issues']}
        except (ValueError,TypeError):
            pass
    units = sentences(text)
    if not units:
        raise ValueError('Cannot review an empty transcript')
    issue = dict(type='object',additionalProperties=False,properties=dict(
        sentence_id=dict(type='integer',minimum=0,maximum=len(units)-1),
        suspect_quote=dict(type='string',minLength=1,maxLength=24),
        kind=dict(type='string',enum=['unintelligible_term','ambiguous_number_or_negation']),
        reason=dict(type='string',minLength=1,maxLength=120)))
    issue['required']=list(issue['properties'])
    schema=dict(type='object',additionalProperties=False,properties=dict(
        verdict=dict(type='string',enum=['clear','needs_audio']),
        issues=dict(type='array',maxItems=6,items=issue),
        explanation=dict(type='string',minLength=1,maxLength=180)))
    schema['required']=list(schema['properties'])
    instructions=(
        '审核自动转写的文字可理解性，不润色、不审核投资判断或观点完整性。你未收到原音，不能声称听感、听过或核实发音。'
        '通读并连读前后句，检查具体身份、公司名、金额、时间、实际动作和否定是否有识别污染。'
        '专名错写即使整体能猜懂也必须报告；公司名中的异常字母堆积、相邻缩写粘连不能默认是有效专名，无法确定实体时需复核。'
        '普通通用词不要求是行业术语。正常口语、重复、填充词、反问、比喻、松散句法及标点断句，不单独构成识别错误。'
        '泛指少量、明确的备选比例、口语约数和省略世纪的年份保持原样；不得凭空另造精确金额、不同读法或绝对日期。'
        '真正不认识的行业词、错写专名、截断数值、单位不明、实际矛盾数字或含义不明的否定仍需报告。'
        '先选verdict：无具体识别污染选clear且issues必须为空；确有疑点选needs_audio且只列实际污染。'
        'issues不是检查过程或逐句点评，不要列正常句、反例、已经否定的疑点或说明“不是错误”的条目。'
        '每项摘录原句编号和1到24字的最小疑词，理由只写具体污染；数字或否定类须摘录实际的数字或否定字。'
        '不猜替换词，不修改原文；explanation简短总结文字判断，不能代替原音核验。')
    prompt=(instructions+
        '目标嘉宾：'+speaker+'。编号原句：'+json.dumps(dict(enumerate(units)),ensure_ascii=False))
    last = None
    independent_evidence_retry = False
    for attempt in range(2):
        request_schema=schema
        raw = call(prompt,request_schema)
        path.with_name(path.stem+f'-response-{attempt}.txt').write_text(raw)
        try:
            data=json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('issues'),list):
                raise ValueError('Missing source issue list')
            if data.get('verdict') not in ('clear','needs_audio'):
                raise ValueError('Missing explicit source review verdict')
            if (data['verdict']=='clear') != (not data['issues']):
                independent_evidence_retry=True
                raise ValueError('Review verdict contradicts its issue list')
            if not isinstance(data.get('explanation'),str) or not data['explanation'].strip():
                raise ValueError('Missing text-review explanation')
            bound=[];ignored=[];invalid=[]
            # Conflicting copies of ONE reported issue are not independent
            # negative evidence. A truncated copy cannot outvote another copy
            # explicitly saying that exact sentence/quote/kind is not an error.
            disavowed_keys={
                (item.get('sentence_id'),item.get('suspect_quote'),item.get('kind'))
                for item in data['issues'] if isinstance(item,dict)
                and isinstance(item.get('reason'),str) and NO_CONTAMINATION.search(item['reason'])}
            if AUDIO_ACCESS_CLAIM.search(data['explanation']):
                independent_evidence_retry=True
                invalid.append(dict(scope='explanation',invalid_reason=
                    '文字审核未收到原音，不能声称听感、听过或核实发音。'
                    '请独立重审实际文字是否有具体识别污染，不猜替换词，不改原话。'))
            for item in data['issues']:
                ident=item.get('sentence_id');reason=item.get('reason');quote=item.get('suspect_quote')
                if (type(ident) is not int or not 0<=ident<len(units)
                        or item.get('kind') not in issue['properties']['kind']['enum']
                        or not isinstance(reason,str) or not reason.strip()
                        or not isinstance(quote,str) or not 1<=len(quote)<=24):
                    raise ValueError('Issue must identify an actual source sentence and reason')
                reported_quote=quote
                if quote not in units[ident]:
                    quote=bind_layout_quote(quote,units[ident])
                    if not quote:raise ValueError('Issue must identify an actual source sentence and reason')
                evidence=dict(sentence_id=ident,quote=units[ident],suspect_quote=quote,kind=item['kind'],reason=reason)
                if quote!=reported_quote:
                    evidence.update(reported_suspect_quote=reported_quote,
                        quote_binding='unique_contiguous_cjk_latin_layout_space')
                if AUDIO_ACCESS_CLAIM.search(reason):
                    independent_evidence_retry=True
                    invalid.append({**evidence,'invalid_reason':
                        '文字审核未收到原音，不能用听感或声称核实发音作为证据。'
                        '请独立判断实际文字中的具体污染，不猜替换词，不改原话。'})
                    continue
                if (ident,reported_quote,item['kind']) in disavowed_keys:
                    independent_evidence_retry=True
                    invalid.append({**evidence,'invalid_reason':
                        '此项理由明确否认识别错误，不能同时列为负面识别证据。仅保留实际认为有污染的最小词语；'
                        '正常口语范围必须保持原样，不能凭空另造精确读法。'})
                    continue
                if ordinary_disfluency(quote):
                    ignored.append({**evidence,'ignored_reason':'ordinary_disfluency_not_asr_error'})
                    continue
                if item['kind']=='ambiguous_number_or_negation' and not CRITICAL_TOKEN.search(quote):
                    independent_evidence_retry=True
                    invalid.append({**evidence,'invalid_reason':
                        '数字或否定疑点必须摘录实际的数字或否定字，不能把一般口语重复归入此类'})
                    continue
                bound.append(evidence)
            # Retain valid negative evidence even when an extra issue is
            # misclassified. This NEVER creates a pass: an invalid-only result
            # must still be retried/fail closed. Valid issues remain blockers.
            if invalid and not bound:
                raise ValueError(invalid[0]['invalid_reason'])
            proof={**identity,'verdict':'needs_audio' if bound else 'clear','model_verdict':data['verdict'],
                   'issues':bound,'ignored_style_objections':ignored,'passed':not bound,'explanation':data['explanation'],
                   'invalid_issue_evidence':invalid,'audio_verified':False,'method':'independent_cpu_text_plausibility'}
            path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
            return proof
        except (ValueError,TypeError,AttributeError) as exc:
            last=exc
            if independent_evidence_retry:
                # A fresh, source-only decision, not an instruction to find
                # more faults or to justify a hallucinated previous verdict.
                # Do not include that verdict or any guessed replacement word.
                prompt=('请独立重审全部原文，不继承任何先前结论。'+instructions+
                    '不要把明确否认识别错误的项目列为错误；错词、数字或否定疑点仍必须列出。'
                    '目标嘉宾：'+speaker+'。编号完整原文：'+json.dumps(dict(enumerate(units)),ensure_ascii=False))
                continue
            prompt+='\n上次响应格式错误：'+str(exc)+'。请仅使用schema字段并核对句编号。'
    raise ValueError('Text plausibility reviewer returned invalid evidence: '+str(last))
