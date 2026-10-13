"""Bounded original-speech topic proposals when structural selection finds none.

Model partitions are proposals, not quality approval. A mechanical read-window
edge is NEVER a clip boundary: edge topics are discarded unless the edge is the
actual source start/end. Every accepted proposal still enters independent gates.
"""
import hashlib
import json
from pathlib import Path

import context_review as review
import editorial_policy as editorial
import source_selection as selection

VERSION=1


def windows(cues,max_seconds=1440,max_chars=9000,overlap_seconds=90):
    units=selection.sentence_units(cues)
    result=[];start=0
    while start<len(units):
        end=start;chars=0
        while end<len(units):
            unit=units[end];size=len(unit['text'])+32
            seconds=cues[unit['end']]['end']-cues[units[start]['start']]['start']
            if seconds>max_seconds or chars+size>max_chars:break
            chars+=size;end+=1
        if end==start:
            start+=1  # Oversized single sentence is not cut to fit a budget.
            continue
        result.append(dict(start=units[start]['start'],end=units[end-1]['end'],
            natural_start=units[start]['start']==0,
            natural_end=units[end-1]['end']==len(cues)-1))
        if end==len(units):break
        following=end
        boundary=cues[units[end-1]['end']]['end']
        for i in range(start+1,end):
            if boundary-cues[units[i]['start']]['start']<=overlap_seconds:
                following=i;break
        start=following
    return result


def topic_ranges(cues,window,topics,min_seconds=20):
    result=[];seen_starts={u['start'] for u in selection.sentence_units(cues)}
    seen_ends={u['end'] for u in selection.sentence_units(cues)}
    for i,topic in enumerate(topics):
        if i==0 and not window['natural_start']:continue
        if i==len(topics)-1 and not window['natural_end']:continue
        a=window['start']+topic['start'];b=window['start']+topic['end']
        if a not in seen_starts or b not in seen_ends or not window['start']<=a<=b<=window['end']:continue
        duration=cues[b]['end']-cues[a]['start']
        if not min_seconds<=duration<=330:continue
        pick=dict(start=a,end=b,score=7,editorial_approved=False,
            selection_method='source_speech_topic_proposal_v1',
            reason='原声完整句之间的话题提案；不使用机械读取窗口边界，仍须独立观点及成片检查')
        if selection.boundary_error(cues,pick):continue
        if editorial.transcript_integrity_error(''.join(c['text'] for c in cues[a:b+1])):continue
        result.append(pick)
    return result


def discover(cues,call,work,source_sha256,speaker='林园',min_seconds=20,max_windows=3):
    if type(max_windows) is not int or not 1<=max_windows<=3:
        raise ValueError('Topic discovery accepts one to three bounded windows')
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    planned=windows(cues)
    # Deterministic coverage, not repeatedly spending every run on the intro.
    indices=(list(range(len(planned))) if len(planned)<=max_windows else [len(planned)//2] if max_windows==1 else
             sorted({round(i*(len(planned)-1)/(max_windows-1)) for i in range(max_windows)}))
    proof=dict(version=VERSION,source_sha256=source_sha256,
        cues_sha256=hashlib.sha256(json.dumps(cues,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),
        editorial_approved=False,planned_window_count=len(planned),reviewed_windows=[],picks=[])
    accepted={}
    try:
        for index in indices:
            window=planned[index];local=cues[window['start']:window['end']+1]
            evidence={**window,'window_index':index,'topics':[]}
            proof['reviewed_windows'].append(evidence)
            transcript='\n'.join(f"字幕{u['start']}-{u['end']}|{u['text']}" for u in selection.sentence_units(local))
            prompt=review.topic_prompt(transcript,local,speaker)+(
                '\n这是母片的一段读取上下文，不是已验收片段。读取窗口的开头和结尾不是自然观点边界。'
                '只按独立问题改变划分原声主题；不能因为时长或为了增加数量，把理由、反问、案例或同一故事拆成小目录。'
                '话题名称只作内部检索，不是标题、字幕、讲话内容或批准。')
            try:
                raw=call(prompt,review.topic_schema(local))
                (work/f'topic-response-{index}.txt').write_text(raw)
                parsed=json.loads(raw,object_pairs_hook=review.unique_object)
                topics=review.parse_topics(json.dumps(parsed,ensure_ascii=False),local)
                evidence['topics']=topics
                for pick in topic_ranges(cues,window,topics,min_seconds):
                    accepted.setdefault((pick['start'],pick['end']),pick)
            except Exception as exc:
                evidence['service_error']=f'{type(exc).__name__}: {exc}'
        proof['picks']=sorted(accepted.values(),key=lambda p:p['start'])
    finally:
        (work/'topic-discovery.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    if not proof['picks'] and any(w.get('service_error') for w in proof['reviewed_windows']):
        raise RuntimeError('原声话题发现服务未完成；保留原始转写和实际响应，不判整源无合格内容')
    return proof['picks']
