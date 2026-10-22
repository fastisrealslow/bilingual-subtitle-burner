"""Source-bound text plausibility review, explicitly not audio verification."""
import json
import re
from pathlib import Path

from editorial_evidence import sentences
from editorial_policy import text_digest

VERSION = 5

CRITICAL_TOKEN = re.compile(r'[0-9零〇一二两三四五六七八九十百千万亿兆点分成倍%％不没无未非否]')

# Narrowly identify a single ordinary amount, not arbitrary numeric speech.
# Percentages, negations, ranges, repeated numbers and malformed Chinese
# numerals are intentionally NOT eligible for this evidence-format check.
SINGLE_AMOUNT = re.compile(
    r'^(?:[一二两三四五六七八九](?:十|百|千|万|亿|十万|百万|千万|十亿|百亿|千亿|万亿)?'
    r'|[0-9]+(?:\.[0-9]+)?(?:万|亿|兆)?)'
    r'(?:元|块钱|美元|港元)?(?:以上|以下|左右)?的?(?:基金|资金|资产|市值|规模)?$')


def unsupported_subject_objection(item):
    """Invalid reviewer evidence, NEVER an exemption for a critical number.

    The real fund interview was blocked because the reviewer explicitly
    complained of a missing subject in a well-formed amount. That violates
    this text-only review's existing scope. Require another actual review;
    never turn this objection into a pass or change the spoken amount.
    """
    if item.get('kind')!='ambiguous_number_or_negation':return False
    quote=item.get('suspect_quote','');reason=item.get('reason','')
    if not SINGLE_AMOUNT.fullmatch(quote):return False
    if re.search(r'矛盾|冲突|截断|残缺|断裂|否定|数词.{0,6}(?:错|异常|不完整)',reason):return False
    return bool(re.search(r'(?:缺少|缺失|省略|缺乏)(?:了)?主语|主语(?:缺失|不明|不清)',reason))


def unsupported_relative_zero_objection(item, sentence):
    """A fact-based objection to a well-formed relative zero is not ASR proof.

    Only the actual cross-time comparison pattern is eligible. This requests
    another real review, NEVER approves audio or normalizes a spoken number.
    Malformed numerals, percentages, negation and same-period conflicts stay
    outside this narrow reviewer-evidence check.
    """
    if item.get('kind')!='ambiguous_number_or_negation':return False
    quote=item.get('suspect_quote','');reason=item.get('reason','')
    if not re.fullmatch(r'(?:我(?:们)?(?:认为|觉得|以为))?(?:今天|现在|目前)(?:是|为|几乎|接近)*(?:零|0)',quote):
        return False
    if (not re.search(r'相对于(?:今天|现在|目前)',sentence)
            or not re.search(r'到[0-9零〇一二两三四五六七八九十]{4,8}年|未来|多年后',sentence)):
        return False
    if re.search(r'数词.{0,8}(?:异常|错误|截断|残缺)|同一(?:时点|时期|范围).{0,8}(?:矛盾|冲突)',reason):
        return False
    return bool(re.search(r'不符合(?:常理|常识|事实)|当前已有|现实已有|目前已有|绝对化',reason))


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
    identity = dict(version=VERSION, transcript_sha256=text_digest(text), speaker=speaker, model=model)
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
    prompt=('只检查以下自动识别文字是否有明显影响理解的识别疑点，不审核观点完整性、吸引力或投资判断对错。'
        '你没有听到原音，不能声称核实了发音，更不能修改原文。'
        '逐句只检查无法解释的具体词语、疑似错写的专有名词，以及含义不明的关键数字或否定。'
        '不要因为文字出现在字幕里就默认识别正确，也不要把不自然的金融或行业词自动解释成行话。'
        '正常口语、语气词、重复、口吃、反问、隐喻，以及鲜明的个人观点本身不是问题。'
        '这不是润色任务：短句、口语省略主语、缺少连接词、填充词多、不够顺畅，都不能作为识别错误。'
        '例如“我们看看看看情况”中的口吃不改变原意；“随时可以调整”是正常短句。'
        '具体区分识别疑点和口语风格：“拥抱某个行业”是普通比喻，不需要改成金融术语；'
        '“搞一点”是泛指少量，不能因为没有精确金额而报数字错误。'
        '“百分之十，或者百分之五”是明确的备选比例，不是互相冲突的数字；'
        '“百分之七八十、九十”是口语范围和追加数值，不因表述不够书面而报识别错误。'
        '带数字或否定字的句子也可能完全正常，必须指出数字或否定本身的具体污染，不可借分类之名审核比喻、指代或精确程度。'
        '“我我还没有这种感觉”中的重复和口语指代不能被标成否定识别错误；'
        '但真正不认识的行业词、错写的专名、截断或互相矛盾的关键数字、含义不明的否定，仍必须列出。'
        '每项必须用suspect_quote摘录真正有疑点的最小词语（1到24字），不得用整句代替具体问题。'
        '数字或否定类必须指明原文中实际的数字或否定字。不要推测替换词，不要审核事实真伪。'
        '特别注意：合法数词后接“以上资金、以上基金”等口语省略，不能只因缺少主语或未补写“规模”就归为数字误识。'
        '必须指出原文数字本身的异常或原文内部的实际冲突，不能把书面语完整性变成数字识别结论。'
        '明确区分跨时点的相对比较与同范围矛盾：原文说“相对于多年后，今天是零”，可能是夸张类比。'
        '不能因为常识认为今天已有消费或数值不是字面零，就断言识别错误；你没有听到音频。'
        '仍须报告数词自身截断、无法解释的词语和同一时间范围内真正矛盾的数字；不修改任何数字或否定。'
        '有疑点时返回原句编号和具体理由，无疑点时issues为空数组。'
        '目标嘉宾：'+speaker+'。编号原句：'+json.dumps(dict(enumerate(units)),ensure_ascii=False))
    last = None
    rejected_reviews=[]
    for attempt in range(2):
        raw = call(prompt,schema)
        path.with_name(path.stem+f'-response-{attempt}.txt').write_text(raw)
        try:
            data=json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('issues'),list):
                raise ValueError('Missing source issue list')
            if not isinstance(data.get('explanation'),str) or not data['explanation'].strip():
                raise ValueError('Missing text-review explanation')
            bound=[];ignored=[];invalid=[]
            for item in data['issues']:
                ident=item.get('sentence_id');reason=item.get('reason');quote=item.get('suspect_quote')
                if (type(ident) is not int or not 0<=ident<len(units)
                        or item.get('kind') not in issue['properties']['kind']['enum']
                        or not isinstance(reason,str) or not reason.strip()
                        or not isinstance(quote,str) or not 1<=len(quote)<=24 or quote not in units[ident]):
                    raise ValueError('Issue must identify an actual source sentence and reason')
                evidence=dict(sentence_id=ident,quote=units[ident],suspect_quote=quote,kind=item['kind'],reason=reason)
                if ordinary_disfluency(quote):
                    ignored.append({**evidence,'ignored_reason':'ordinary_disfluency_not_asr_error'})
                    continue
                if item['kind']=='ambiguous_number_or_negation' and not CRITICAL_TOKEN.search(quote):
                    invalid.append({**evidence,'invalid_reason':
                        '数字或否定疑点必须摘录实际的数字或否定字，不能把一般口语重复归入此类'})
                    continue
                if unsupported_subject_objection(item):
                    invalid.append({**evidence,'invalid_reason':
                        '口语省略主语不是数字识别证据；需重新检查真实数词异常，不得猜改或直接放行'})
                    continue
                if unsupported_relative_zero_objection(item,units[ident]):
                    invalid.append({**evidence,'invalid_reason':
                        '原句已明确未来与今天的相对比较；不符合常理不是数词误识证据，须重新实际审核，不能猜改或直接放行'})
                    continue
                bound.append(evidence)
            # Retain valid negative evidence even when an extra issue is
            # misclassified. This NEVER creates a pass: an invalid-only result
            # must still be retried/fail closed. Valid issues remain blockers.
            if invalid and not bound:
                rejected_reviews.append(dict(attempt=attempt,invalid_issue_evidence=invalid))
                raise ValueError(invalid[0]['invalid_reason'])
            proof={**identity,'issues':bound,'ignored_style_objections':ignored,'passed':not bound,'explanation':data['explanation'],
                   'invalid_issue_evidence':invalid,'audio_verified':False,'method':'independent_cpu_text_plausibility'}
            proof['rejected_review_evidence']=rejected_reviews
            path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
            return proof
        except (ValueError,TypeError,AttributeError) as exc:
            last=exc
            prompt+='\n上次响应格式错误：'+str(exc)+'。请仅使用schema字段并核对句编号。'
    raise ValueError('Text plausibility reviewer returned invalid evidence: '+str(last))
