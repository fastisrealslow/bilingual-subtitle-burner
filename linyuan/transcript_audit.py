"""Source-bound text plausibility review, explicitly not audio verification."""
import json
import re
from pathlib import Path

from editorial_evidence import sentences
from editorial_policy import text_digest

VERSION = 4
PROMPT_VERSION = 2026102002

CRITICAL_TOKEN = re.compile(r'[0-9零〇一二两三四五六七八九十百千万亿兆点分成倍%％不没无未非否]')


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
    identity = dict(version=VERSION, prompt_version=PROMPT_VERSION,
                    transcript_sha256=text_digest(text), speaker=speaker, model=model)
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
    # Decide BEFORE opening an issue array. With issues first, the constrained
    # decoder committed to an item, then explained that it was NOT an issue.
    # Mutually exclusive native branches prevent that structural false reject;
    # clear never overrides an actual issue, and malformed output fails closed.
    branches=[]
    for assessment,minimum,maximum in [('clear',0,0),('needs_audio_review',1,6)]:
        branch=dict(type='object',additionalProperties=False,properties=dict(
            assessment=dict(type='string',enum=[assessment]),
            explanation=dict(type='string',minLength=1,maxLength=180),
            issues=dict(type='array',minItems=minimum,maxItems=maximum,items=issue)))
        branch['required']=list(branch['properties']);branches.append(branch)
    schema=dict(type='object',oneOf=branches)
    prompt=('先判断assessment：clear表示没有具体识别疑点，needs_audio_review表示存在需原音复核的具体疑点。'
        '先说明explanation，再给issues；clear分支只能返回空数组，needs_audio_review必须给出具体疑点。'
        '若理由认为没有错写、不是识别错误或不应列为疑点，就选择clear，不能先填一个问题再解释它不是问题。'
        '只检查以下自动识别文字是否有明显影响理解的识别疑点，不审核观点完整性、吸引力或投资判断对错。'
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
        '先泛指、后补充一个清楚的近似范围也不是数字冲突，例如“几个人，是五六个人”、'
        '“几块钱，的是五六块钱”；中间的“的是”属于口语冗余，不会把五六变成另一组数字。'
        '不要要求这种原话补充精确日期、报价基准、币种或计算口径才算识别正确；那是事实语境审核，不是辨字检查。'
        '必须保留原数值和近似程度，不得把“五六”改成“五十六”、精确的“五”或“六”。'
        '带数字或否定字的句子也可能完全正常，必须指出数字或否定本身的具体污染，不可借分类之名审核比喻、指代或精确程度。'
        '“我我还没有这种感觉”中的重复和口语指代不能被标成否定识别错误；'
        '但真正不认识的行业词、错写的专名、截断或互相矛盾的关键数字、含义不明的否定，仍必须列出。'
        '每项必须用suspect_quote摘录真正有疑点的最小词语（1到24字），不得用整句代替具体问题。'
        '数字或否定类必须指明原文中实际的数字或否定字。不要推测替换词，不要审核事实真伪。'
        '不能仅因不熟悉某个历史公司或想确认它现在是否存在就报专名识别错误；必须指出文字本身的具体错写疑点。'
        '如果判断“原词无误、不属于疑点、只是口语风格”，不要把该项放进issues。'
        '有疑点时返回原句编号和具体理由，无疑点时issues为空数组。'
        '目标嘉宾：'+speaker+'。编号原句：'+json.dumps(dict(enumerate(units)),ensure_ascii=False))
    last = None
    for attempt in range(2):
        raw = call(prompt,schema)
        path.with_name(path.stem+f'-response-{attempt}.txt').write_text(raw)
        try:
            data=json.loads(raw)
            if not isinstance(data,dict) or not isinstance(data.get('issues'),list):
                raise ValueError('Missing source issue list')
            assessment=data.get('assessment')
            if (assessment not in {'clear','needs_audio_review'}
                    or (assessment=='clear') != (not data['issues'])):
                raise ValueError('Assessment and issues must agree; never silently drop an issue to create a pass')
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
                bound.append(evidence)
            # Retain valid negative evidence even when an extra issue is
            # misclassified. This NEVER creates a pass: an invalid-only result
            # must still be retried/fail closed. Valid issues remain blockers.
            if invalid and not bound:
                raise ValueError(invalid[0]['invalid_reason'])
            proof={**identity,'model_assessment':assessment,'issues':bound,'ignored_style_objections':ignored,'passed':not bound,'explanation':data['explanation'],
                   'invalid_issue_evidence':invalid,'audio_verified':False,'method':'independent_cpu_text_plausibility'}
            path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
            return proof
        except (ValueError,TypeError,AttributeError) as exc:
            last=exc
            prompt+='\n上次响应格式错误：'+str(exc)+'。请仅使用schema字段并核对句编号。'
    raise ValueError('Text plausibility reviewer returned invalid evidence: '+str(last))
