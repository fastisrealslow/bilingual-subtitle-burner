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
from audio_preprocessing import policy, matches
from asr_context import specification, matches as context_matches


def restore_raw_qwen_evidence(archive, directory, source_sha, revisions):
    """Reuse actual complete decoder/alignment records, never an editorial pass."""
    with zipfile.ZipFile(archive) as z:
        names=[n for n in z.namelist() if n.endswith('/asr_raw_chunks.json') or n=='asr_raw_chunks.json']
        if len(names)!=1 or z.getinfo(names[0]).file_size>20*1024*1024:return False
        reports=json.loads(z.read(names[0]))
    if not isinstance(reports,list) or not reports:return False
    if not matches(reports,policy(source_sha)) or not context_matches(reports,source_sha=source_sha):return False
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
    context=specification(source_sha=source)['identity']
    if context:choice['recognition_context']=context
    if choice['backend'] not in ('sensevoice','qwen3'):
        raise ValueError('Daily configuration must explicitly select a supported CPU offline model')
    return choice


def emit(name,value):
    if '\n' in str(value) or '\r' in str(value):
        raise ValueError('Invalid runtime configuration value')
    with open(os.environ['GITHUB_ENV'],'a') as stream:
        stream.write(f'{name}={value}\n')


def mother_cache_key(choice, root=None):
    """Key only the selected source and inference inputs, not other sources.

    configuration() includes the global source_overrides dictionary. Hashing
    that dictionary invalidated every unrelated mother whenever one source's
    audio channel or incident-evidence location changed.
    """
    from audio_preprocessing import DEFAULT, normalized_policy
    root=Path(root) if root is not None else Path(__file__).parent
    identity={key:choice[key] for key in ('source_sha256','backend','model_revisions')}
    identity['audio_preprocessing']=normalized_policy(choice.get('audio_preprocessing',DEFAULT))
    context=specification(root=root,source_sha=choice['source_sha256'])['identity']
    if context:identity['recognition_context']=context
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode())
    production=(root/'produce_cn.py').read_text()
    version=re.search(r'^ASR_PIPELINE_VERSION\s*=\s*\d+',production,re.M)
    if not version:raise ValueError('Missing ASR pipeline version')
    digest.update(version.group(0).encode())
    for name in ('qwen_cpu_transcript.py','qwen_asr_evidence.py','reviewed_asr_corrections.py'):
        digest.update((root/name).read_bytes())
    return choice['source_sha256']+'-'+digest.hexdigest()[:20]


def cached_evidence(report_path,choice):
    from qwen_asr_evidence import load_reports
    directory=Path(report_path).parent/'qwen_cpu'
    reports=load_reports(directory)
    revisions=choice.get('model_revisions') or {}
    if (not reports or not matches(reports,policy(choice['source_sha256']))
            or not context_matches(reports,source_sha=choice['source_sha256'])):return None
    for report in reports:
        alignment=report.get('alignment') or {}
        if (report.get('source_video_sha256')!=choice['source_sha256']
                or report.get('device')!='cpu' or report.get('networking_during_inference') is not False
                or report.get('model_id')!='Qwen/Qwen3-ASR-0.6B'
                or report.get('model_revision')!=revisions.get('asr')
                or alignment.get('device')!='cpu' or alignment.get('networking_during_inference') is not False
                or alignment.get('model_id')!='Qwen/Qwen3-ForcedAligner-0.6B'
                or alignment.get('model_revision')!=revisions.get('aligner')
                or not report.get('chunks')):return None
    return directory.resolve()


def immutable_run_evidence(choice):
    """An expired download is a cache miss, not a failed fresh recognition.

    Never expose a partially downloaded directory. A successful download still
    goes through the normal source/PCM/model/coverage checks in production.
    """
    run=choice.get('evidence_run_id')
    if type(run) is not int or run<=0:
        raise ValueError('Invalid immutable ASR evidence run')
    directory=(Path('_asr_evidence')/choice['source_sha256']/f'run-{run}').resolve()
    try:
        subprocess.run(['gh','run','download',str(run),'--repo',os.environ['GITHUB_REPOSITORY'],
            '--pattern','long-cpu-transcript-*','--dir',str(directory)],
            check=True,capture_output=True,text=True,timeout=300)
    except (subprocess.SubprocessError,OSError) as exc:
        # Do not print stderr: it may include authenticated URLs. No explicit
        # evidence path is emitted; qwen3 must decode and align the actual audio
        # using the already prepared pinned weights instead.
        print('先前固定任务的转写缓存不可用，按当前离线模型实际识别：',type(exc).__name__)
        return None
    from qwen_asr_evidence import load_reports
    reports=load_reports(directory)
    if not reports:
        print('先前固定任务未提供完整转写，按当前离线模型实际识别')
        return None
    # Present evidence is never silently ignored merely because its immutable
    # source/model binding is wrong. Full PCM and core ownership follow later.
    revisions=choice.get('model_revisions') or {}
    if (not matches(reports,policy(choice['source_sha256']))
            or not context_matches(reports,source_sha=choice['source_sha256'])
            or any(r.get('source_video_sha256')!=choice['source_sha256']
                or r.get('device')!='cpu' or r.get('networking_during_inference') is not False
                or r.get('model_id')!='Qwen/Qwen3-ASR-0.6B'
                or r.get('model_revision')!=revisions.get('asr')
                or (r.get('alignment') or {}).get('device')!='cpu'
                or (r.get('alignment') or {}).get('networking_during_inference') is not False
                or (r.get('alignment') or {}).get('model_id')!='Qwen/Qwen3-ForcedAligner-0.6B'
                or (r.get('alignment') or {}).get('model_revision')!=revisions.get('aligner')
                or not r.get('chunks') for r in reports)):
        raise ValueError('Immutable ASR evidence has incompatible source/model provenance')
    return directory


def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['select','prepare','cache'])
    p.add_argument('--source-report',required=True)
    args=p.parse_args()
    choice=configuration(args.source_report)
    if args.mode=='cache':
        directory=cached_evidence(args.source_report,choice) if choice['backend']=='qwen3' else None
        if directory is None and choice['backend']=='qwen3' and os.environ.get('GH_TOKEN') and os.environ.get('GITHUB_REPOSITORY'):
            from recover_mother_asr import restore
            directory=restore(choice,Path(args.source_report).parent,mother_cache_key(choice))
        emit('ASR_EVIDENCE_READY','true' if directory else 'false')
        if directory:
            emit('QWEN3_EVIDENCE_DIR',directory)
            print('复用母片绑定的逐字对齐证据；实际PCM与覆盖率仍在出片前校验')
        return
    if args.mode=='select':
        emit('ASR_BACKEND',choice['backend'])
        # Cache recognition once per exact mother video, model and code. This
        # remains a hypothesis cache, never a substitute for editorial review.
        # Rendering/editorial edits must not invalidate expensive recognition.
        # The cue-conversion version does invalidate the mother cue cache, but
        # still reuses immutable decoder/alignment evidence instead of decoding
        # the audio again. Other rendering/editorial edits stay out of this key.
        emit('MOTHER_ASR_KEY',mother_cache_key(choice))
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
        directory=immutable_run_evidence(choice)
        if directory:emit('QWEN3_EVIDENCE_DIR',directory)
    else:
        # Evidence belongs to the immutable mother video, not to a delivery
        # directory.  A reviewed source can therefore reuse its prior raw CPU
        # transcript under a fresh output slug without importing old reviews.
        slug=choice.get('evidence_slug') or os.environ.get('ASR_SOURCE_SLUG','')
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
