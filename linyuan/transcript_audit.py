"""Source-bound text plausibility review, explicitly not audio verification."""
import json
from pathlib import Path

from editorial_evidence import sentences
from editorial_policy import text_digest

VERSION = 1


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
        kind=dict(type='string',enum=['unintelligible_term','broken_syntax','ambiguous_number_or_negation']),
        reason=dict(type='string',minLength=1,maxLength=120)))
    issue['required']=list(issue['properties'])
    schema=dict(type='object',additionalProperties=False,properties=dict(
        issues=dict(type='array',maxItems=6,items=issue),
        explanation=dict(type='string',minLength=1,maxLength=180)))
    schema['required']=list(schema['properties'])
    prompt=('只检查以下自动识别文字是否有明显影响理解的识别疑点，不审核观点完整性、吸引力或投资判断对错。'
        '你没有听到原音，不能声称核实了发音，更不能修改原文。'
        '逐句检查无法解释的词语、疑似错写的专有名词、缺少必要成分而无法理解的句子，以及含义不明的关键数字或否定。'
        '不要因为文字出现在字幕里就默认识别正确，也不要把不自然的金融或行业词自动解释成行话。'
        '正常口语、语气词、重复、口吃、反问、隐喻，以及鲜明的个人观点本身不是问题。'
        '只报告实际原句中影响理解的疑点；不要凭空纠正事实，不要生成替换文字。'
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
            if not isinstance(data.get('explanation'),str) or not data['explanation'].strip():
                raise ValueError('Missing text-review explanation')
            bound=[]
            for item in data['issues']:
                ident=item.get('sentence_id');reason=item.get('reason')
                if (type(ident) is not int or not 0<=ident<len(units)
                        or item.get('kind') not in issue['properties']['kind']['enum']
                        or not isinstance(reason,str) or not reason.strip()):
                    raise ValueError('Issue must identify an actual source sentence and reason')
                bound.append(dict(sentence_id=ident,quote=units[ident],kind=item['kind'],reason=reason))
            proof={**identity,'issues':bound,'passed':not bound,'explanation':data['explanation'],
                   'audio_verified':False,'method':'independent_cpu_text_plausibility'}
            path.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
            return proof
        except (ValueError,TypeError,AttributeError) as exc:
            last=exc
            prompt+='\n上次响应格式错误：'+str(exc)+'。请仅使用schema字段并核对句编号。'
    raise ValueError('Text plausibility reviewer returned invalid evidence: '+str(last))
