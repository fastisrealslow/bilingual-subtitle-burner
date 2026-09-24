#!/usr/bin/env python3
"""Read-only model proposals for long sources missed by explicit-boundary rules."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import editorial_policy as ep
import source_selection as ss


def digest(cues):
    return hashlib.sha256(json.dumps(cues,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def screen(cues,pick):
    units=ss.sentence_units(cues)
    a,b=pick.get('start'),pick.get('end')
    if type(a) is not int or type(b) is not int or not 0<=a<=b<len(cues):
        return 'invalid_cue_coordinates'
    if a not in {u['start'] for u in units} or b not in {u['end'] for u in units}:
        return 'cut_inside_source_sentence'
    seconds=cues[b]['end']-cues[a]['start']
    if not 20<=seconds<=330:return 'duration_outside_20_330'
    return ss.boundary_error(cues,pick) or ep.transcript_integrity_error(''.join(c['text'] for c in cues[a:b+1]))


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case',type=int,required=True)
    args=ap.parse_args()
    assert os.environ.get('TEXT_BACKEND')=='local'
    import produce_cn as p
    cases=json.loads((ROOT/'linyuan/simulations/benchmark-20260921/selection-model-corpus.json').read_text())
    case=cases[args.case];cues=case['cues']
    assert digest(cues)==case['raw_cues_sha256']
    out=Path('selection-model-results');out.mkdir(exist_ok=True)
    row=dict(source_id=case['source_id'],source_sha256=case['source_sha256'],
        raw_cues_sha256=case['raw_cues_sha256'],model=p.LOCAL_LLM_MODEL,
        run_id=os.environ.get('GITHUB_RUN_ID'),commit=os.environ.get('GITHUB_SHA'),
        status='unresolved',editorial_approved=False,production_changed=False,
        scope='Text-only proposals, not completeness approval, produced videos or yield')
    target=out/'result.json'
    def save():target.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
    save();start=time.monotonic()
    units=ss.sentence_units(cues)
    prompt='''只根据整段原始讲话，提出最多3个连续片段供编辑检查，不写标题、不判断成片合格。
每个片段围绕一个完整问题、判断或具体经历，保留理解它所需的理由、条件、否定与结尾。
不要从指代不明的半句话开始；不要拿下一话题的提问凑时长；不要拼接不相邻的句子。
可以没有候选。优先同一段真实的取舍、分歧或具体经历，不为了“精彩”删掉必要上下文。
每条20至330秒，时长跟着内容走。先在a_reason简述片段讲清什么及为何可独立理解，不能仅说“符合要求”。
再选b_start/c_end：它们是下方原始字幕编号，须落在完整句的两端。只输出规定JSON。
完整句格式：字幕起止编号｜原声起止秒｜原文
'''+ '\n'.join(f"{u['start']}-{u['end']}|{cues[u['start']]['start']:.2f}-{cues[u['end']]['end']:.2f}|{u['text']}" for u in units)
    fields=dict(a_reason=dict(type='string'),b_start=dict(type='integer',enum=[u['start'] for u in units]),
                c_end=dict(type='integer',enum=[u['end'] for u in units]))
    schema=dict(type='object',additionalProperties=False,required=['picks'],properties=dict(
        picks=dict(type='array',minItems=0,maxItems=3,items=dict(type='object',additionalProperties=False,
                   required=list(fields),properties=fields))))
    row['prompt']=prompt;save()
    try:
        with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as response:tags=json.load(response)
        row['model_digest']=next(m['digest'] for m in tags['models'] if m['name']==p.LOCAL_LLM_MODEL)
        raw=p.llm([dict(role='user',content=prompt)],'',temperature=0,max_tokens=1100,
                  budget_sec=900,response_schema=schema,read_cache=False)
        row['raw_response']=raw;save()
        import title_rewrite as title
        picks=title._json(raw)['picks']
        if not isinstance(picks,list) or len(picks)>3:raise ValueError('Invalid proposal list')
        row['picks']=[]
        for pick in picks:
            if not isinstance(pick,dict):raise ValueError('Invalid proposal')
            pick=dict(start=pick.get('b_start'),end=pick.get('c_end'),reason=pick.get('a_reason'))
            issue=screen(cues,pick)
            record={**pick,'structural_error':issue,'editorial_approved':False}
            if not issue:
                a,b=pick['start'],pick['end']
                record.update(start_seconds=cues[a]['start'],end_seconds=cues[b]['end'],cues=cues[a:b+1])
            row['picks'].append(record)
        row['status']='proposed'
    except Exception as exc:row['error']=f'{type(exc).__name__}: {exc}'
    row['seconds']=round(time.monotonic()-start,2);save()
    print(json.dumps({k:v for k,v in row.items() if k not in ('prompt','raw_response','picks')},ensure_ascii=False))
    if row['status']!='proposed':raise SystemExit(1)


if __name__=='__main__':main()
