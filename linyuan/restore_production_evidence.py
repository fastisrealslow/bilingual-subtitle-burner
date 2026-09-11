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
    from mother_asr_cache import transfer
    restored_asr=transfer(evidence,work,current['source_sha256'])
    # Never restore source/identity approvals, final outputs or delivery metadata.
    # Every cache below is revalidated by the existing production functions.
    for pattern in ('highlights*.json','copywrite*.json'):
        for path in evidence.glob(pattern):
            shutil.copy2(path,work/path.name);copied.append(path.name)
    print(json.dumps(dict(restored=copied,restored_asr=restored_asr,source_sha256=current['source_sha256']),ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id',required=True,type=int)
    parser.add_argument('--work',required=True,type=Path)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='production-evidence-') as directory:
        # Successful batches retain raw ASR in editorial artifacts, while older
        # failures may only have debug evidence. Never redownload/reinfer good ASR.
        repo=__import__('os').environ.get('GITHUB_REPOSITORY','fastisrealslow/bilingual-subtitle-burner')
        artifacts=json.loads(subprocess.check_output(['gh','api',
            f'repos/{repo}/actions/runs/{args.run_id}/artifacts']))['artifacts']
        names=[a['name'] for prefix in ('editorial-','debug-') for a in artifacts
               if a['name'].startswith(prefix) and not a.get('expired')]
        if not names:raise ValueError('原任务没有可用的转写证据')
        subprocess.run(['gh','run','download',str(args.run_id),'--repo',repo,
                        '--name',names[0],'--dir',directory],check=True)
        reports=list(Path(directory).rglob('source_quality.json'))
        if len(reports)!=1:raise ValueError('需要唯一的失败母片证据')
        restore(reports[0].parent,args.work)


if __name__=='__main__':main()
