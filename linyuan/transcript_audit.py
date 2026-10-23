"""Source-bound text plausibility review, explicitly not audio verification."""
import json
import re
from pathlib import Path

from editorial_evidence import sentences
from editorial_policy import text_digest
from transcript_review_evidence import AUDIO_ACCESS_CLAIM

VERSION = 4
SPOKEN_RANGE_POLICY_VERSION = 2026102310

CRITICAL_REVIEW_SCOPE=(
    '先通读整段，再按“是否会误解是谁、哪家公司、多少钱、何时、实际做了什么或是否做过”的标准检查。'
    '具体专名错写、关键金额/时间/否定或直接决定实际动作的词有污染，即使整体能猜懂也必须报告。'
    '但普通通用词不必是金融术语；口语省略虚词、临时改口、句法不整齐、指代泛化、'
    '标点把连续意思分开，不能单独变成识别错误。连读上下文能理解且不改变具体身份、事实或动作的'
    '口语瑕疵不报告。不要为润色补字，不根据观点是否符合金融常识判断识别，不猜原音还有其他词。')
CRITICAL_REVIEW_SCOPE+='口语年份省略、相对时间和约数保持原样，不推算或补齐世纪、绝对日期或精确金额；缺少绝对值不等于识别错误。'

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
            if all(saved.get(k)==v for k,v in identity.items()) and isinstance(saved.get('issues'),list):
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
        issues=dict(type='array',maxItems=6,items=issue),
        explanation=dict(type='string',minLength=1,maxLength=180)))
    schema['required']=list(schema['properties'])
    prompt=(CRITICAL_REVIEW_SCOPE+'只检查以下自动识别文字是否有明显影响理解的识别疑点，不审核观点完整性、吸引力或投资判断对错。'
        '你没有听到原音，不能声称核实了发音，更不能修改原文。'
        '逐句只检查无法解释的具体词语、疑似错写的专有名词，以及含义不明的关键数字或否定。'
        '不要因为文字出现在字幕里就默认识别正确；专名乱码不能用行话掩盖。普通通用词不要求是行业术语。'
        '正常口语、语气词、重复、口吃、反问、隐喻，以及鲜明的个人观点本身不是问题。'
        '这不是润色任务：短句、口语省略主语、缺少连接词、填充词多、不够顺畅，都不能作为识别错误。'
        '例如“我们看看看看情况”中的口吃不改变原意；“随时可以调整”是正常短句。'
        '具体区分识别疑点和口语风格：“拥抱某个行业”是普通比喻，不需要改成金融术语；'
        '“搞一点”是泛指少量，不能因为没有精确金额而报数字错误。'
        '“百分之十，或者百分之五”是明确的备选比例，不是互相冲突的数字；'
        '“百分之七八十、九十”是口语范围和追加数值，不因表述不够书面而报识别错误。'
        '“几块钱的是五六块钱”“两三年”“七八百元”也都是可以原样保留的口语范围；'
        '没有小数点或角分单位时，不得凭空另造“五点六”“五块六”等读法，再以无法排除该读法为由报错。'
        '范围表达不需要变成精确价格；缺少精确数值不等于识别污染。'
        '如果唯一理由是“正常口语模糊范围，但不知道是否还有另一种精确读法”，应返回issues为空，保持原话。'
        '这不豁免数字检查：截断数值、单位不明、同段实际冲突的数字或否定仍需列出。'
        '带数字或否定字的句子也可能完全正常，必须指出数字或否定本身的具体污染，不可借分类之名审核比喻、指代或精确程度。'
        '“我我还没有这种感觉”中的重复和口语指代不能被标成否定识别错误；'
        '但真正不认识的行业词、错写的专名、截断或互相矛盾的关键数字、含义不明的否定，仍必须列出。'
        '每项必须用suspect_quote摘录真正有疑点的最小词语（1到24字），不得用整句代替具体问题。'
        '数字或否定类必须指明原文中实际的数字或否定字。不要推测替换词，不要审核事实真伪。'
        '有疑点时返回原句编号和具体理由，无疑点时issues为空数组。'
        '目标嘉宾：'+speaker+'。编号原句：'+json.dumps(dict(enumerate(units)),ensure_ascii=False))
    last = None
    explanation_first_retry = False
    independent_evidence_retry = False
    for attempt in range(2):
        request_schema=schema
        if explanation_first_retry:
            # Keep the validated first-pass protocol for real corrupt terms.
            # Only an explicit, contradictory "not an ASR error" response may
            # receive a fresh explanation-first decision. Never waive an issue.
            request_schema={**schema,'properties':dict(
                explanation=schema['properties']['explanation'],issues=schema['properties']['issues']),
                'required':['explanation','issues']}
        raw = call(prompt,request_schema)
        path.with_name(path.stem+f'-response-{attempt}.txt').write_text(raw)
        try:
            data=json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('issues'),list):
                raise ValueError('Missing source issue list')
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
                explanation_first_retry=True
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
                    explanation_first_retry=True
                    independent_evidence_retry=True
                    invalid.append({**evidence,'invalid_reason':
                        '文字审核未收到原音，不能用听感或声称核实发音作为证据。'
                        '请独立判断实际文字中的具体污染，不猜替换词，不改原话。'})
                    continue
                if (ident,reported_quote,item['kind']) in disavowed_keys:
                    explanation_first_retry=True
                    independent_evidence_retry=True
                    invalid.append({**evidence,'invalid_reason':
                        '此项理由明确否认识别错误，不能同时列为负面识别证据。仅保留实际认为有污染的最小词语；'
                        '正常口语范围必须保持原样，不能凭空另造精确读法。'})
                    continue
                if ordinary_disfluency(quote):
                    ignored.append({**evidence,'ignored_reason':'ordinary_disfluency_not_asr_error'})
                    continue
                if item['kind']=='ambiguous_number_or_negation' and not CRITICAL_TOKEN.search(quote):
                    explanation_first_retry=True
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
            proof={**identity,'issues':bound,'ignored_style_objections':ignored,'passed':not bound,'explanation':data['explanation'],
                   'invalid_issue_evidence':invalid,'audio_verified':False,'method':'independent_cpu_text_plausibility'}
            path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
            return proof
        except (ValueError,TypeError,AttributeError) as exc:
            last=exc
            if independent_evidence_retry:
                # A fresh, source-only decision, not an instruction to find
                # more faults or to justify a hallucinated previous verdict.
                # Do not include that verdict or any guessed replacement word.
                prompt=(CRITICAL_REVIEW_SCOPE+'独立审核下面的自动转写文字。你未收到原音，不能声称听过、听感或核实发音，不能猜替换词。'
                    '只报告从完整上下文可具体指出、影响理解的识别污染：无法理解的实际词语、错写专名、'
                    '被截断或互相矛盾的关键数字、含义不明的否定。'
                    '这是文字可理解性检查，不是润色或事实核查。口语省略、重复、语气词、松散句法、'
                    '标点把连续意思分开，均不能单独作为识别错误；请连读前后句。'
                    '正常泛指数词、程度和排名观点不因不精确或可能不符合事实就成为识别错误。'
                    '不能假设原音还有不同的字词或限定，再据此报错。'
                    '解释中独立判断实际是否有污染；issues只保留有具体文字依据的最小疑词。'
                    '不要把明确否认识别错误的项目列为错误。'
                    '无法指出实际污染时issues为空，保持原话；确有错词、数字或否定疑点仍必须列出。'
                    '目标嘉宾：'+speaker+'。编号完整原文：'+json.dumps(dict(enumerate(units)),ensure_ascii=False))
                continue
            prompt+='\n上次响应格式错误：'+str(exc)+'。请仅使用schema字段并核对句编号。'
            if explanation_first_retry:
                prompt+='先在explanation独立判断是否实际存在识别污染，再填写issues；保持原话，不得把你认为不是识别错误的项目列为错误。'
    raise ValueError('Text plausibility reviewer returned invalid evidence: '+str(last))
