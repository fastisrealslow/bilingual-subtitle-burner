"""Choose an explicit offline CPU backend from the versioned production config."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import hashlib
import re
import tempfile
import zipfile


def restore_raw_qwen_evidence(archive, directory, source_sha, revisions):
    """Reuse actual complete decoder/alignment records, never an editorial pass."""
    with zipfile.ZipFile(archive) as z:
        names=[n for n in z.namelist() if n.endswith('/asr_raw_chunks.json') or n=='asr_raw_chunks.json']
        if len(names)!=1 or z.getinfo(names[0]).file_size>20*1024*1024:return False
        reports=json.loads(z.read(names[0]))
    if not isinstance(reports,list) or not reports:return False
    for report in reports:
        alignment=report.get('alignment') or {}
        if (report.get('source_video_sha256')!=source_sha or report.get('device')!='cpu'
                or report.get('networking_during_inference') is not False
                or report.get('model_id')!='Qwen/Qwen3-ASR-0.6B'
                or report.get('model_revision')!=revisions.get('asr')
                or alignment.get('model_id')!='Qwen/Qwen3-ForcedAligner-0.6B'
                or alignment.get('model_revision')!=revisions.get('aligner')
                or not report.get('chunks')):return False
    # Full PCM hash, coverage and token ownership are checked against the actual
    # downloaded source by validated_words immediately before producing cues.
    for i,report in enumerate(reports):
        path=Path(directory)/str(i);path.mkdir(parents=True,exist_ok=True)
        (path/'aligned.json').write_text(json.dumps(report,ensure_ascii=False))
    return True


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
        # Rendering/editorial edits must not invalidate expensive recognition.
        # ASR_PIPELINE_VERSION is already part of the inner provenance check.
        for name in ('qwen_cpu_transcript.py','qwen_asr_evidence.py','reviewed_asr_corrections.py'):
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
    else:
        slug=os.environ.get('ASR_SOURCE_SLUG','')
        if not re.fullmatch(r'[a-zA-Z0-9_-]+',slug):return
        repo=os.environ['GITHUB_REPOSITORY']
        try:
            listing=subprocess.run(['gh','api',f'repos/{repo}/actions/artifacts?name=editorial-{slug}&per_page=5'],
                check=True,capture_output=True,text=True,timeout=30)
            candidates=json.loads(listing.stdout).get('artifacts',[])
            for artifact in candidates[:3]:
                if artifact.get('expired') or artifact.get('size_in_bytes',0)>20*1024*1024:continue
                with tempfile.TemporaryDirectory() as tmp:
                    archive=Path(tmp)/'raw.zip'
                    with archive.open('wb') as stream:
                        subprocess.run(['gh','api',f'repos/{repo}/actions/artifacts/{int(artifact["id"])}/zip'],
                            stdout=stream,check=True,timeout=90)
                    directory=(Path('_asr_evidence')/choice['source_sha256']/str(artifact['id'])).resolve()
                    if restore_raw_qwen_evidence(archive,directory,choice['source_sha256'],revisions):
                        emit('QWEN3_EVIDENCE_DIR',directory)
                        print('复用实际完整CPU转写证据，重新选段及审核：',artifact['id'])
                        break
        except (subprocess.SubprocessError,ValueError,OSError,zipfile.BadZipFile) as exc:
            print('先前转写证据暂不可用，按当前离线模型实际识别：',type(exc).__name__)


if __name__=='__main__':
    main()
