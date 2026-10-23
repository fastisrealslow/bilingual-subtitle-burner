"""Propose source-sentence ranges missed by explicit Q&A/chapter rules.

No proposal is editorial, subtitle, identity, picture or publication approval.
The existing automatic argument reader and every final gate still run.
"""
import hashlib
import json
import math
import re
import time
from pathlib import Path

import editorial_policy as editorial
import source_selection as selection

VERSION = 2026102301
MAX_WINDOW_CHARS = 6000
OVERLAP_SECONDS = 330


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
                                    separators=(',',':')).encode()).hexdigest()


def opening_allowed(text):
    # Classification only; never strip or rewrite a source cue.
    plain=re.sub(r'^(?:[啊嗯呃哦][，,、\s]*|对[，,、\s]+)+','',text)
    return bool(len(re.sub(r'\W','',plain))>=8 and not re.match(
        r'^(?:因为|所以|但是|因此|不过|而且|然后|实际上|除此|其中|'
        r'它|他|她|他们|这个|这些|这种|这就是|那个|那些|那是|那就|那么)',plain))


def opening_error(text):
    plain=re.sub(r'^(?:[啊嗯呃哦][，,、\s]*|对[，,、\s]+)+','',text)
    if re.match(r'^(?:这|那)(?:就|也|也就)?是(?:我|我们)(?:个人)?的?'
                r'(?:看法|观点|想法|判断|意思|结论)(?:[，,。]|$)',plain):
        return 'opening_only_refers_to_a_previous_view'
    return None


def windows(cues):
    units=selection.sentence_units(cues)
    rows=[dict(unit,description=f"{unit['start']}-{unit['end']}|"
        f"{cues[unit['start']]['start']:.2f}-{cues[unit['end']]['end']:.2f}|{unit['text']}")
        for unit in units]
    result=[];start=0
    while start<len(rows):
        end=start;size=0
        while end<len(rows) and (end==start or size+len(rows[end]['description'])+1<=MAX_WINDOW_CHARS):
            size+=len(rows[end]['description'])+1;end+=1
        result.append(rows[start:end])
        if end==len(rows):break
        cutoff=cues[rows[end-1]['end']]['end']-OVERLAP_SECONDS
        # Progress is mandatory even when one unusually long source sentence
        # fills the window. No timestamp inside that sentence is invented.
        start=next((i for i in range(start+1,end)
                    if cues[rows[i]['start']]['start']>=cutoff),end)
    return result


def request(rows):
    if sum(len(row['description'])+1 for row in rows)>MAX_WINDOW_CHARS:
        return None  # Never truncate or split an oversized source sentence.
    starts=[row['start'] for row in rows if opening_allowed(row['text'])]
    ends=[row['end'] for row in rows if not selection.unresolved_question_tail(row['text'])]
    if not starts or not ends:return None
    item=dict(type='object',additionalProperties=False,required=['start','end'],properties={
        'start':dict(type='integer',enum=starts),'end':dict(type='integer',enum=ends)})
    schema=dict(type='object',additionalProperties=False,required=['picks'],properties={
        'picks':dict(type='array',maxItems=3,items=item)})
    prompt=('这是原始长篇讲话的一组完整原句，现有规则没有识别出问答/章节。'
        '只提出最多三个不同的连续片段供后续独立审核，不判断成片合格，不写标题或摘要。'
        '优先本人实际经历、选择及理由，或点名公司的具体事情；没有适合的片段可返回空数组。'
        '每段20至330秒，按原声起止秒核算，不用固定时长切割、不拼接。'
        '开头必须独立交代主题，保留必要问题和前提；不能从“因为/所以/它/这个”这类依赖前文的句子开始。'
        '同一件事的理由、条件、否定和自然结尾都要保留，不能只留结论。'
        '不要跨入新话题，不要把下一问及其铺垫接在前一段后面，不用寒暄或片尾凑时长。'
        'start/end只选允许的原始字幕编号，不是秒数、行号或句子序号。'
        '每行格式是字幕起止编号|原声起止秒|完整原句。仅返回规定JSON。\n'
        +'\n'.join(row['description'] for row in rows))
    return prompt,schema


def validate(cues,rows,data):
    if not isinstance(data,dict) or set(data)!={'picks'} or not isinstance(data['picks'],list) or len(data['picks'])>3:
        raise ValueError('Invalid keynote proposal response')
    allowed=request(rows)
    if allowed is None:return [],[]
    props=allowed[1]['properties']['picks']['items']['properties']
    accepted=[];rejected=[]
    for row in data['picks']:
        reason=None
        if (not isinstance(row,dict) or set(row)!={'start','end'}
                or any(type(row[k]) is not int or row[k] not in props[k]['enum'] for k in ('start','end'))
                or row['start']>row['end']):
            raise ValueError('Proposal cuts inside a sentence or uses a forbidden opening/ending')
        a,b=row['start'],row['end'];seconds=cues[b]['end']-cues[a]['start']
        first=next(unit['text'] for unit in rows if unit['start']==a)
        reason=opening_error(first)
        if not editorial.MIN_SECONDS<=seconds<=330:reason=reason or 'duration_outside_source_policy'
        reason=reason or selection.boundary_error(cues,row)
        text=''.join(c['text'] for c in cues[a:b+1])
        reason=reason or editorial.transcript_integrity_error(text)
        if reason:
            rejected.append(dict(start=a,end=b,reason=reason));continue
        accepted.append(dict(start=a,end=b,score=7,
            reason='从原始完整句提出连续候选，仍须独立观点、字幕和画面验收',
            selection_method='model_proposed_source_sentences_v1'))
    return accepted,rejected


def propose(cues,source_report,call,path,*,budget_sec=1200):
    path=Path(path)
    source_sha=source_report.get('source_sha256')
    duration=source_report.get('duration_sec')
    if (source_report.get('passed') is not True or not re.fullmatch('[a-f0-9]{64}',str(source_sha))
            or type(duration) not in (int,float) or not math.isfinite(duration) or not 900<=duration<=5400
            or not cues or cues[-1]['end']>duration+1):
        raise ValueError('Keynote proposals require the current independently checked long mother')
    identity=dict(version=VERSION,source_sha256=source_sha,transcript_sha256=digest(cues))
    previous={}
    if path.is_file():
        try:
            saved=json.loads(path.read_text())
            if saved.get('identity')==identity:
                previous={digest([row['prompt'],row['schema']]):row['response']
                    for row in saved.get('calls',[]) if isinstance(row,dict)
                    and not row.get('error') and isinstance(row.get('response'),str)
                    and 'prompt' in row and 'schema' in row}
        except (ValueError,TypeError,KeyError):pass
    report=dict(identity=identity,picks=[],rejected=[],unresolved=[],calls=[],
                media_approved=False,selection_completed=False)
    from batch_delivery import write_json
    deadline=time.monotonic()+budget_sec
    for index,rows in enumerate(windows(cues)):
        query=request(rows)
        if query is None:continue
        prompt,schema=query;key=digest([prompt,schema])
        record=dict(index=index,prompt=prompt,schema=schema,cache_reused=key in previous)
        try:
            if key in previous:response=previous[key]
            else:
                remaining=deadline-time.monotonic()
                if remaining<1:raise TimeoutError('Bounded keynote proposal budget exhausted')
                response=call(prompt,schema,min(600,remaining))
            record['response']=response
            parsed=json.loads(response) if isinstance(response,str) else response
            picks,rejected=validate(cues,rows,parsed)
            for pick in picks:
                if not any((p['start'],p['end'])==(pick['start'],pick['end']) for p in report['picks']):
                    report['picks'].append(pick)
            report['rejected'].extend(dict(window=index,**r) for r in rejected)
        except Exception as exc:
            record['error']=f'{type(exc).__name__}: {exc}'
            report['unresolved'].append(dict(window=index,error=record['error']))
        report['calls'].append(record);write_json(path,report)
    report['picks'].sort(key=lambda p:p['start'])
    report['selection_completed']=not report['unresolved']
    write_json(path,report)
    return report
