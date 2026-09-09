"""Persist source-bound offline hypotheses across distinct editing jobs."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

FILES = ('cues_raw.json', 'asr_cache.json', 'asr_raw_chunks.json',
         'asr_tokens.json', 'asr_reviewed_corrections.json')


def transfer(source, target, video_sha):
    source, target = Path(source), Path(target)
    try:
        provenance = json.loads((source/'asr_cache.json').read_text())
        cues = (source/'cues_raw.json').read_bytes()
        if (provenance['identity']['source_sha256'] != video_sha
                or provenance['cues_sha256'] != hashlib.sha256(cues).hexdigest()):
            return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    target.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        if (source/name).is_file():
            shutil.copyfile(source/name, target/name)
    if (source/'qwen_cpu').is_dir():
        shutil.copytree(source/'qwen_cpu', target/'qwen_cpu', dirs_exist_ok=True)
    elif (source/'asr_raw_chunks.json').is_file():
        reports=json.loads((source/'asr_raw_chunks.json').read_text())
        if isinstance(reports,list) and reports and all(
                r.get('source_video_sha256')==video_sha for r in reports):
            for i,report in enumerate(reports):
                path=target/'qwen_cpu'/str(i)
                path.mkdir(parents=True,exist_ok=True)
                (path/'aligned.json').write_text(json.dumps(report,ensure_ascii=False))
    # produce_cn.transcribe still checks the exact model/config identity and
    # audio coverage; no edited subtitles or selection/review flags are cached.
    return True


if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('mode', choices=['restore','save'])
    p.add_argument('--work', required=True)
    p.add_argument('--cache', required=True)
    p.add_argument('--source-report', required=True)
    a=p.parse_args()
    sha=json.loads(Path(a.source_report).read_text())['source_sha256']
    source,target=(a.cache,a.work) if a.mode=='restore' else (a.work,a.cache)
    print('Mother ASR cache', a.mode, transfer(source,target,sha))
