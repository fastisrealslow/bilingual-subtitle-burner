"""Paired CPU reasoning diagnostic on actual misapproved titles; never production."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

import title_model_lab as lab


def read_json(archive, name):
    with zipfile.ZipFile(archive) as z:return json.loads(z.read(name))


def examples(archives):
    original=archives[12407526223]
    source=read_json(original,'_tmp/source_quality.json')
    audit=read_json(original,'_tmp/transcript_audit.json')
    raw=read_json(original,'_tmp/cues_raw.json')
    pick=read_json(original,'_tmp/story_selection.json')['selected'][0]
    units=[c['text'] for c in raw[pick['start']:pick['end']+1]]
    import sys
    sys.path.insert(0,str(lab.ROOT/'linyuan'))
    from editorial_policy import text_digest
    if (source.get('passed') is not True or source.get('source_sha256')!=
            '1df744406655d5b19d20e13ccb0c8138955953aaa6d49978ee6fbb0053b320be'
            or text_digest(''.join(units))!=audit.get('transcript_sha256')):
        raise ValueError('Original source/text identity mismatch')
    # These are ACTUAL earlier machine outputs, not supplied publication copy.
    cases=[]
    for artifact in (12406859515,12410065151,12410941172):
        item=read_json(archives[artifact],'boundary.json')
        if item.get('source_sha256')!=source['source_sha256']:
            raise ValueError('Control generated from a different source')
        cases.append(dict(artifact=artifact,title=item['title'],cover_title=item['cover_title'],
                          expected_supported=False))
    draft=read_json(archives[12410065151],'title_response-2.txt')['c_candidates'][0]
    if draft['title']!='林园：我只投我看好的消费，在一亩三分地耕耘':
        raise ValueError('Actual positive draft changed')
    cases.append(dict(artifact=12410065151,**draft,expected_supported=True))
    return units,cases,source['source_sha256'],audit['transcript_sha256']


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--profile',required=True,
        choices=['qwen35-9b-direct','qwen35-9b-thinking','qwen3-4b-direct','qwen3-4b-thinking'])
    args=ap.parse_args();out=Path('title-logic-failure-results');out.mkdir(exist_ok=True)
    row=dict(profile=args.profile,model=lab.PROFILES[args.profile]['model'],focused=True,
             diagnostic_only=True,publication_authorized=False,media_approved=False,
             registered_stock=False,audio_verified=False,calls=[],passed=False)
    target=out/(args.profile+'.json')
    def save():target.write_text(json.dumps(row,ensure_ascii=False,indent=2))
    save()
    with tempfile.TemporaryDirectory(prefix='title-logic-original-') as directory:
        archives={}
        for artifact in (12407526223,12406859515,12410065151,12410941172):
            path=Path(directory)/(str(artifact)+'.zip')
            with path.open('wb') as stream:
                subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],
                               stdout=stream,check=True,timeout=180)
            archives[artifact]=path
        units,controls,source_sha,text_sha=examples(archives)
    row.update(source_sha256=source_sha,transcript_sha256=text_sha,
               controls=controls,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    fields=dict(index=dict(type='integer',minimum=0,maximum=len(controls)-1),
        supported=dict(type='boolean'),source_quote=dict(type='string'),reason=dict(type='string'))
    schema=dict(type='object',additionalProperties=False,required=['source_reading','reviews'],
        properties=dict(source_reading=dict(type='string',maxLength=300),reviews=dict(type='array',
            minItems=len(controls),maxItems=len(controls),items=dict(type='object',additionalProperties=False,
                required=list(fields),properties=fields))))
    prompt=('只依据完整原文核对标题和封面的含义，不猜音频、不润色、不评吸引力。'
        '先概括实际投资对象、做或不做的动作，再区分条件由谁满足、条件导致哪个结果。'
        '特别区分“只能选A”与“什么也不选”，以及需求、投资选择与义务；不能因为原文出现同一词就当作同一判断。'
        '若标题或封面有任意新增或反转事实，supported=false。source_quote必须逐字引用连续原话。'
        '以下为按原顺序的完整原文，不是预先批准的结论：\n'+json.dumps(dict(enumerate(units)),ensure_ascii=False)
        +'\n以下文案独立判断，各index恰好一次，不能信任文案作者：\n'
        +json.dumps([dict(index=i,title=c['title'],cover_title=c['cover_title']) for i,c in enumerate(controls)],ensure_ascii=False))
    save()
    response=lab.call(prompt,schema,lab.PROFILES[args.profile],'source_logic_review',row,save)
    verdicts=response.get('reviews',[])
    if sorted(v.get('index') for v in verdicts)!=list(range(len(controls))):
        raise ValueError('Incomplete control verdicts')
    row['response']=response
    full=''.join(units)
    row['passed']=all(type(v.get('supported')) is bool
        and v['supported'] is controls[v['index']]['expected_supported']
        and isinstance(v.get('source_quote'),str) and len(v['source_quote'])>=4
        and v['source_quote'] in full for v in verdicts)
    save();print(json.dumps({k:v for k,v in row.items() if k not in ('calls','response','controls')},ensure_ascii=False))
    if not row['passed']:raise ValueError('Actual semantic controls not preserved; no rollout')


if __name__=='__main__':main()
