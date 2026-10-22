"""Read-only replay of real delivered bytes and the current publication history."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan'),str(ROOT/'scripts')]
import source_supply as S
import publishing_experiment as E
from verify_linyuan_story_delivery import verify


def main():
    p=argparse.ArgumentParser();p.add_argument('--artifact',required=True)
    p.add_argument('--state',required=True);p.add_argument('--out',required=True)
    args=p.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(args.artifact) as z:
        for n in z.namelist():
            if Path(n).name!=n or n.endswith('/'):raise ValueError('Non-flat delivery')
            if n=='meta.json' or n.endswith(('.mp4','.jpg','.ass','.json')):
                (out/n).write_bytes(z.read(n))
    acceptance=verify(out)
    data=json.loads((out/'meta.json').read_text());rows=data if isinstance(data,list) else [data]
    state=json.loads(Path(args.state).read_text());results=[]
    for m in rows:
        error=S.validate_part(m,out)
        if error:raise ValueError('Actual media gate failed: '+error)
        assert E.actual_profile(m)==E.TREATMENT
        duplicate=S.fc.inventory_publication_error(m,dict(slug='readonly-real-proof'),state)
        results.append(dict(title=m['title'],source_sha256=m['source_sha256'],
            video_sha256=hashlib.sha256((out/m['final']).read_bytes()).hexdigest(),
            actual_profile=E.actual_profile(m),actual_media_gates_passed=True,
            publication_history_block=duplicate,publishable_under_current_history=not bool(duplicate)))
    report=dict(read_only=True,never_uploaded=True,acceptance=acceptance,results=results)
    (out/'runtime-delivery-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
