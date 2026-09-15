"""Restore completed work only after matching the freshly checked media hash."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def restore(evidence, work, *, titles_only=False):
    evidence,work=Path(evidence),Path(work)
    current=json.loads((work/'source_quality.json').read_text())
    old=json.loads((evidence/'source_quality.json').read_text())
    if not current.get('passed') or not current.get('source_sha256'):
        raise ValueError('本次母片尚未通过素材检查，不能恢复证据')
    if (old.get('passed') is False and old.get('failure_stage')=='source-fetch'
            and not old.get('source_sha256')):
        # A fetch timeout has no complete mother or ASR to restore. The Sep 13
        # 806 retry completed its 693 MB download, then this optional cache
        # restore falsely treated the old missing hash as a new source failure.
        print('旧任务仅取源失败，没有可复用的完整母片证据；使用本次已核验母片继续离线识别')
        return False
    if current['source_sha256']!=old.get('source_sha256'):
        raise ValueError('恢复证据与本次通过质检的母片哈希不一致')
    copied=[]
    from mother_asr_cache import transfer
    restored_asr=transfer(evidence,work,current['source_sha256']) if not titles_only else False
    if not titles_only and not restored_asr:
        # Keep unfinished hypotheses separate from accepted ASR evidence. The
        # CPU worker checks audio SHA, model/config and each core before reuse;
        # the existing full-coverage validator still gates completed subtitles.
        partial=evidence/'qwen_cpu'
        if (partial/'recognition.json').is_file():
            report=json.loads((partial/'recognition.json').read_text())
            if report.get('source_video_sha256')!=current['source_sha256']:
                raise ValueError('分段转写断点与本次母片哈希不一致')
            target=work/'_partial_qwen_cpu'
            if target.exists():shutil.rmtree(target)
            target.mkdir(parents=True)
            for name in ('recognition.json','aligned.json'):
                if (partial/name).is_file():shutil.copy2(partial/name,target/name)
            print('恢复未完成的转写断点；完整音频覆盖检查通过前不作为字幕或合格成片')
    # Never restore source/identity approvals, final outputs or delivery metadata.
    # Every cache below is revalidated by the existing production functions.
    # Reuse only completed caption boundary suggestions. The current producer
    # revalidates every character, timestamp and layout, and regenerates the
    # edit proof. Never copy old semantic approvals or editing proofs.
    patterns=(('copywrite*.json',) if titles_only else
              ('highlights*.json','copywrite*.json','semantic*.readable-*.json'))
    for pattern in patterns:
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
        if restore(reports[0].parent,args.work) is False:return
        # Run 801's preferred editorial artifact retained ASR but omitted every
        # validated copywrite file. The full debug artifact still has them.
        # Supplement titles only; don't replace current ASR or quality evidence.
        debug=next((name for name in names if name.startswith('debug-')),None)
        if names[0].startswith('editorial-') and debug and not list(args.work.glob('copywrite*.json')):
            with tempfile.TemporaryDirectory(prefix='production-title-evidence-') as extra:
                try:
                    subprocess.run(['gh','run','download',str(args.run_id),'--repo',repo,
                                    '--name',debug,'--dir',extra],check=True,timeout=300)
                    reports=list(Path(extra).rglob('source_quality.json'))
                    if len(reports)!=1:raise ValueError('旧标题缓存没有唯一母片证据')
                    restore(reports[0].parent,args.work,titles_only=True)
                except (subprocess.SubprocessError,OSError,ValueError) as exc:
                    print(f'旧标题补充恢复不可用，已恢复的转写仍可使用：{type(exc).__name__}')


if __name__=='__main__':main()
