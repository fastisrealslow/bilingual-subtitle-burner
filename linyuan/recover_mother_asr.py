"""Recover immutable raw ASR when Actions caches have been evicted."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


def api(path):
    result=subprocess.run(['gh','api','repos/'+os.environ['GITHUB_REPOSITORY']+'/'+path],
        check=True,capture_output=True,text=True,timeout=20)
    return json.loads(result.stdout)


def candidates(cache_key):
    seen=set()
    for name in ('raw-asr-'+cache_key,'preview-raw-asr-'+cache_key):
        for row in api('actions/artifacts?name='+name+'&per_page=3').get('artifacts',[]):
            if row['id'] not in seen:seen.add(row['id']);yield row
    # Compatibility recovery reads raw evidence from recent normal/preview
    # jobs. No source IDs, title answers or manually selected run IDs are used.
    workflows=['linyuan-automatic-preview.yml','linyuan-produce-cn.yml']
    current=os.environ.get('GITHUB_WORKFLOW_REF','').split('/')[-1].split('@')[0]
    workflows.sort(key=lambda x:x!=current)
    for workflow in workflows:
        runs=api('actions/workflows/'+workflow+'/runs?status=completed&per_page=4').get('workflow_runs',[])
        for run in runs:
            for row in api('actions/runs/'+str(run['id'])+'/artifacts?per_page=100').get('artifacts',[]):
                if row['id'] not in seen and row.get('name','').startswith(('editorial-','preview-editorial-')):
                    seen.add(row['id']);yield row


def restore(choice,work,cache_key):
    from prepare_asr_runtime import restore_raw_qwen_evidence
    from qwen_asr_evidence import load_reports,validated_words
    source_sha=choice['source_sha256'];work=Path(work)
    attempts=0
    try:
        for artifact in candidates(cache_key):
            if artifact.get('expired') or not 0<artifact.get('size_in_bytes',0)<20*1024**2:continue
            if attempts>=8:break
            attempts+=1
            with tempfile.TemporaryDirectory() as tmp:
                archive=Path(tmp)/'evidence.zip';stage=Path(tmp)/'raw'
                with archive.open('wb') as stream:
                    subprocess.run(['gh','api','repos/'+os.environ['GITHUB_REPOSITORY']+
                        '/actions/artifacts/'+str(artifact['id'])+'/zip'],stdout=stream,
                        stderr=subprocess.PIPE,check=True,timeout=45)
                if not restore_raw_qwen_evidence(archive,stage,source_sha,choice.get('model_revisions') or {}):continue
                reports=load_reports(stage)
                # Reject partial or internally inconsistent alignment before
                # skipping recognition. Actual PCM is checked again in render.
                try:validated_words(reports,reports[0]['source_pcm_sha256'],source_sha,float(reports[0]['duration']))
                except (KeyError,ValueError,TypeError):continue
                target=work/'recovered_raw_asr'/str(artifact['id']);target.mkdir(parents=True,exist_ok=True)
                for i,report in enumerate(reports):
                    folder=target/str(i);folder.mkdir(exist_ok=True)
                    (folder/'aligned.json').write_text(json.dumps(report,ensure_ascii=False))
                (work/'raw_asr_recovery.json').write_text(json.dumps(dict(artifact_id=artifact['id'],
                    source_sha256=source_sha,editorial_answers_imported=False),indent=2))
                return target.resolve()
    except (subprocess.SubprocessError,OSError,ValueError,KeyError,TypeError,zipfile.BadZipFile) as exc:
        print('原始ASR检查点暂不可用，继续正常识别：'+type(exc).__name__,flush=True)
    return None
