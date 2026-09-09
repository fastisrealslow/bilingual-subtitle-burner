"""Dispatch and track one cached-ASR recovery, preserving unrelated FC state."""
import base64
import argparse
import json
import subprocess
import time

REPO='fastisrealslow/bilingual-subtitle-burner'
OLD='ly-0909-7c4660'
NEW=OLD+'-selector8'
PATH='linyuan/.automation/fc_state.json'


def gh(*args,body=None):
    return subprocess.run(['gh',*args],input=None if body is None else json.dumps(body),
                          text=True,capture_output=True,check=True).stdout


def read_state():
    doc=json.loads(gh('api',f'repos/{REPO}/contents/{PATH}?ref=main'))
    return doc,json.loads(base64.b64decode(doc['content']))


def main():
    global NEW, OLD
    parser=argparse.ArgumentParser()
    parser.add_argument('--suffix',default='selector8')
    parser.add_argument('--evidence-run',default='')
    parser.add_argument('--reviewed-parts',default='')
    parser.add_argument('--selected-parts',default='')
    parser.add_argument('--origin-slug',default=OLD)
    parser.add_argument('--expected-source',default='https://www.bilibili.com/video/BV1SazbBhE8a')
    args=parser.parse_args()
    OLD=args.origin_slug
    NEW=OLD+'-'+args.suffix
    doc,state=read_state()
    if any(e.get('slug')==NEW for e in state.get('dispatched',[])):
        print('Recovery already registered; no duplicate dispatch')
        return
    origin=max((e for e in state['dispatched'] if e.get('slug')==OLD),key=lambda e:e.get('ts',0))
    source=origin['source_url']
    assert source==args.expected_source,source
    gh('workflow','run','linyuan-produce-cn.yml','--repo',REPO,'--ref','main',
       '-f',f'source={source}','-f',f'slug={NEW}','-f','speaker=林园',
       '-f',f'occasion={origin.get("title") or "林园公开访谈"}','-f','source_platform=bilibili',
       '-f','auto_publish=false','-f','include_full=false',
       '-f',f'recovery_run_id={args.evidence_run}',
       '-f',f'reviewed_parts={args.reviewed_parts}',
       '-f',f'selected_parts={args.selected_parts}')
    entry={k:origin[k] for k in ('key','video_id','source_url','asset_url','title','source',
           'production_rules_version','required_presentation_version') if k in origin}
    entry.update(slug=NEW,ts=int(time.time()),delay_hours=0,repair_of=OLD)
    for attempt in range(6):
        doc,state=read_state()
        if any(e.get('slug')==NEW for e in state.get('dispatched',[])):return
        state.setdefault('dispatched',[]).append(entry)
        body=dict(message='chore: track one selector8 recovery using cached mother ASR',
            sha=doc['sha'],branch='main',content=base64.b64encode(json.dumps(state,ensure_ascii=False,indent=2).encode()).decode())
        try:
            gh('api','--method','PUT',f'repos/{REPO}/contents/{PATH}','--input','-',body=body)
            print(f'Dispatched and registered {NEW}; publication remains scheduled and gated')
            return
        except subprocess.CalledProcessError:
            if attempt==5:raise
            time.sleep(2)


if __name__=='__main__':main()
