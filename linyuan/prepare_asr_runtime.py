"""Choose an explicit offline CPU backend from the versioned production config."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import hashlib


def configuration(report_path):
    config=json.loads((Path(__file__).parent/'asr_production_config.json').read_text())
    source=json.loads(Path(report_path).read_text())['source_sha256']
    override=config.get('source_overrides',{}).get(source,{})
    choice={**config,**override,'source_sha256':source}
    if choice['backend'] not in ('sensevoice','qwen3'):
        raise ValueError('Daily configuration must explicitly select a supported CPU offline model')
    return choice


def emit(name,value):
    if '\n' in str(value) or '\r' in str(value):
        raise ValueError('Invalid runtime configuration value')
    with open(os.environ['GITHUB_ENV'],'a') as stream:
        stream.write(f'{name}={value}\n')


def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['select','prepare'])
    p.add_argument('--source-report',required=True)
    args=p.parse_args()
    choice=configuration(args.source_report)
    if args.mode=='select':
        emit('ASR_BACKEND',choice['backend'])
        # Cache recognition once per exact mother video, model and code. This
        # remains a hypothesis cache, never a substitute for editorial review.
        digest=hashlib.sha256(json.dumps(choice,sort_keys=True).encode())
        for name in ('produce_cn.py','qwen_cpu_transcript.py','qwen_asr_evidence.py','reviewed_asr_corrections.py'):
            digest.update((Path(__file__).parent/name).read_bytes())
        emit('MOTHER_ASR_KEY',choice['source_sha256']+'-'+digest.hexdigest()[:20])
        print('CPU offline ASR backend:',choice['backend'])
        return
    if choice['backend']!='qwen3':return
    from huggingface_hub import snapshot_download
    revisions=choice.get('model_revisions') or {}
    for env,model,key in [('QWEN3_ASR_DIR','Qwen/Qwen3-ASR-0.6B','asr'),
                          ('QWEN3_ALIGNER_DIR','Qwen/Qwen3-ForcedAligner-0.6B','aligner')]:
        path=snapshot_download(model,revision=revisions.get(key))
        emit(env,path)
    run=choice.get('evidence_run_id')
    if run is not None:
        if type(run) is not int or run<=0:raise ValueError('Invalid immutable ASR evidence run')
        directory=(Path('_asr_evidence')/choice['source_sha256']).resolve()
        subprocess.run(['gh','run','download',str(run),'--repo',os.environ['GITHUB_REPOSITORY'],
            '--pattern','long-cpu-transcript-*','--dir',str(directory)],check=True,timeout=300)
        emit('QWEN3_EVIDENCE_DIR',directory)


if __name__=='__main__':
    main()
