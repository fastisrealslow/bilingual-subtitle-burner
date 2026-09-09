"""Actual CPU inference on the two subtitle sequences that timed out in #622."""
import argparse
import json
from pathlib import Path
import re
import time
import produce_cn as p
from presentation import layout_for,wrap_words


def incident_entries(root):
    work=Path(root)/'_tmp'
    report=json.loads((work/'source_quality.json').read_text())
    assert report['source_sha256']=='f2a561a9d6258ca6a4836abbbcbefb07af65029cf181e815b800a81b4669ab4c'
    cues=json.loads((work/'cues_raw.json').read_text())
    result=[]
    for n,(a,b) in enumerate(p._dedup_chunks_char(p._chunk_by_time(cues),cues),1):
        l,r=a,b
        while l>0 and cues[a]['start']-cues[l-1]['start']<=60:l-=1
        while r+1<len(cues) and cues[r+1]['end']-cues[b]['end']<=60:r+=1
        cache=work/f'highlights_block_{n}.json'
        if not cache.exists():continue
        data=json.loads(cache.read_text())
        assert data['identity']==dict(editorial=p.editorial.plan_identity(cues[l:r+1],p.TARGET_SEC),selector_version=8)
        for pick in data['picks']:
            cs=cues[l+pick['start']:l+pick['end']+1]
            result.append([dict(start_sec=c['start']-cs[0]['start'],end_sec=c['end']-cs[0]['start'],zh=c['text'],en='') for c in cs])
    assert [len(x) for x in result]==[63,59]
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('evidence',type=Path)
    parser.add_argument('--output',type=Path,default=Path('/tmp/caption-622'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    p.BASE=args.output  # Isolate model cache to measure real inference.
    layout=layout_for(720,1280,True);rows=[]
    normalize=lambda text:re.sub(r'[\s，。！？；：、]','',text)
    for n,entries in enumerate(incident_entries(args.evidence),1):
        started=time.monotonic()
        groups=p.semantic_caption_entries(entries,'',layout,args.output/f'semantic_{n}.json')
        assert ''.join(normalize(g['zh']) for g in groups)==normalize(''.join(e['zh'] for e in entries))
        assert all(.25<=g['end_sec']-g['start_sec']<=8 for g in groups)
        assert all(len(wrap_words(g['zh'],g.get('line_capacity',layout['line_capacity'])))<=2 for g in groups)
        row=dict(part=n,screens=len(groups),seconds=round(time.monotonic()-started,2),passed=True)
        rows.append(row);print(json.dumps(row),flush=True)
        (args.output/'report.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
