"""Real unfinished-13 scheduling replay; no media approval, stock or posting."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import publishing_experiment as E


def main():
    model=os.environ['LOCAL_LLM_MODEL']
    if model!='qwen3.5:9b':raise ValueError('Current production CPU model only')
    artifact=12467582807
    raw=subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],timeout=120)
    if hashlib.sha256(raw).hexdigest()!='137d1715a412230373d273b309eff290815d878f5e36ac9779ffea36e919635f':
        raise ValueError('Immutable original evidence archive changed')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        source=json.loads(archive.read('_tmp/source_quality.json'))
        cues=json.loads(archive.read('_tmp/cues_raw.json'))
        prior=json.loads(archive.read('_tmp/story_selection.json'))
    if (source.get('source_sha256')!='8b5c9d11737003b7a409fabf803ff7b185ed50bd4ed171a9e0377ac95e67e313'
            or prior['revision']!=E.SELECTION_REVISION or prior['transcript_sha256']!=E.digest(cues)):
        raise ValueError('Source/transcript/revision binding changed')
    candidates=prior['candidates'];snapshot=json.dumps(cues,sort_keys=True)
    pending={r['index'] for r in prior['unresolved'] if r['error']=='selection_budget_exhausted'}
    if len(candidates)!=37 or pending!=set(range(24,37)):
        raise ValueError('Original observed partial selection changed')
    out=Path('story-review-continuation-cpu-results');out.mkdir(exist_ok=True)
    (out/'story_review_progress.json').write_text(json.dumps(prior,ensure_ascii=False))
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as response:tags=json.load(response)
    details=next(m for m in tags['models'] if m.get('name')==model)
    calls=[]
    def call(prompt,schema):
        payload=dict(model=model,messages=[dict(role='user',content=prompt)],format=schema,
            stream=False,think=False,options=dict(temperature=0,num_predict=1600,num_ctx=16384))
        request=urllib.request.Request('http://127.0.0.1:11434/api/chat',
            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        row=dict(prompt=prompt,schema=schema);calls.append(row);started=time.monotonic()
        with urllib.request.urlopen(request,timeout=240) as response:result=json.load(response)
        row.update(response=result['message']['content'],elapsed_sec=time.monotonic()-started)
        return row['response']
    result=E.choose_stories(cues,candidates,call,out/'selection.json',budget_sec=1200)
    unresolved={r['index'] for r in result['unresolved']}
    pending_completed=sorted(pending-unresolved)
    newly_selected=[p for p in result['selected'] if any(
        p['start']==candidates[i]['start'] and p['end']==candidates[i]['end'] for i in pending)]
    passed=(result['review_order'][:13]==list(range(24,37)) and len(pending_completed)==13
            and result['continuation']['applied'] and not result['continuation']['prior_approvals_reused']
            and sorted(result['review_order'])==list(range(37))
            and json.dumps(cues,sort_keys=True)==snapshot)
    report=dict(diagnostic_only=True,publication_approved=False,audio_verified=False,
        source_cues_changed=False,source_artifact=artifact,source_run=39864415088,
        source_sha256=source['source_sha256'],model=model,model_digest=details['digest'],
        experiment_id=E.EXPERIMENT_ID,revision=E.SELECTION_REVISION,
        experiment_code_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest(),
        original_candidate_count=37,original_pending=sorted(pending),
        pending_completed=pending_completed,new_pending_selected=newly_selected,
        selection=result,calls=calls,selection_budget_seconds=1200,
        new_stock_count=0,passed=passed)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(dict(diagnostic_only=True,passed=passed,
        previously_unreviewed_completed=len(pending_completed),
        new_pending_selections=len(newly_selected),new_stock=0)))
    if not passed:raise SystemExit('Unfinished real decisions not all reached; do not promote')


if __name__=='__main__':main()
