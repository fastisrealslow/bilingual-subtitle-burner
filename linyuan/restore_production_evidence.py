"""Restore completed work only after matching the freshly checked media hash."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def restore(evidence, work):
    evidence,work=Path(evidence),Path(work)
    current=json.loads((work/'source_quality.json').read_text())
    old=json.loads((evidence/'source_quality.json').read_text())
    if (not current.get('passed') or not current.get('source_sha256')
            or current['source_sha256']!=old.get('source_sha256')):
        raise ValueError('恢复证据与本次通过质检的母片哈希不一致')
    copied=[]
    # Never restore source/identity approvals, final outputs or delivery metadata.
    # Every cache below is revalidated by the existing production functions.
    for pattern in ('highlights*.json','copywrite*.json'):
        for path in evidence.glob(pattern):
            shutil.copy2(path,work/path.name);copied.append(path.name)
    print(json.dumps(dict(restored=copied,source_sha256=current['source_sha256']),ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True,type=int)
    parser.add_argument('--work',required=True,type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='production-evidence-') as directory:
        subprocess.run(['gh','run','download',str(args.run_id),
            '--pattern','debug-*','--dir',directory],check=True)
        reports=list(Path(directory).rglob('source_quality.json'))
        if len(reports)!=1:raise ValueError('需要唯一的失败母片证据')
        restore(reports[0].parent,args.work)


if __name__=='__main__':main()
