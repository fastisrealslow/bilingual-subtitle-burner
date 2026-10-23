"""Actual all-37 review-order shadow, not a video, approval or CTR test."""
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
import story_review_priority as S


def main():
    model=os.environ['LOCAL_LLM_MODEL']
    if model!='qwen3.5:9b':raise ValueError('Current production model only')
    artifact=12467582807
    raw=subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],timeout=120)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        source=json.loads(archive.read('_tmp/source_quality.json'))
        cues=json.loads(archive.read('_tmp/cues_raw.json'))
        prior=json.loads(archive.read('_tmp/story_selection.json'))
    if (source.get('source_sha256')!='8b5c9d11737003b7a409fabf803ff7b185ed50bd4ed171a9e0377ac95e67e313'
            or prior['revision']!=E.SELECTION_REVISION or prior['transcript_sha256']!=E.digest(cues)):
        raise ValueError('Source/transcript/revision binding changed')
    original=prior['candidates'];ranked=S.rank_candidates(cues,original)
    if len(original)!=37 or {id(p) for p in ranked}!={id(p) for p in original}:
        raise ValueError('Actual candidate set changed')
    original_indices=[original.index(p) for p in ranked]
    pending={r['index'] for r in prior['unresolved'] if r['error']=='selection_budget_exhausted'}
    out=Path('story-review-order-cpu-results');out.mkdir(exist_ok=True);calls=[]
    with urllib.request.urlopen('http://127.0.0.1:11434/api/tags',timeout=15) as response:tags=json.load(response)
    details=next(m for m in tags['models'] if m.get('name')==model)
    def call(prompt,schema):
        payload=dict(model=model,messages=[dict(role='user',content=prompt)],format=schema,
            stream=False,think=False,options=dict(temperature=0,num_predict=1600,num_ctx=16384))
        request=urllib.request.Request('http://127.0.0.1:11434/api/chat',
            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        row=dict(prompt=prompt,schema=schema);calls.append(row);started=time.monotonic()
        with urllib.request.urlopen(request,timeout=240) as response:result=json.load(response)
        row.update(response=result['message']['content'],elapsed_sec=time.monotonic()-started)
        return row['response']
    result=E.choose_stories(cues,ranked,call,out/'selection.json',budget_sec=1200)
    newly_selected=[p for p in result['selected'] if any(
        p['start']==original[i]['start'] and p['end']==original[i]['end'] for i in pending)]
    promoted=[p for p in newly_selected if any(
        p['start']==original[i]['start'] and p['end']==original[i]['end']
        and original_indices.index(i)<len(original)-len(pending) for i in pending)]
    report=dict(diagnostic_only=True,publication_approved=False,audio_verified=False,
        source_cues_changed=False,source_artifact=artifact,source_run=39864415088,
        source_sha256=source['source_sha256'],model=model,model_digest=details['digest'],
        experiment_id=E.EXPERIMENT_ID,revision=E.SELECTION_REVISION,
        priority_policy=S.POLICY,priority_code_sha256=hashlib.sha256(Path(S.__file__).read_bytes()).hexdigest(),
        experiment_code_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest(),
        original_candidate_count=len(original),original_unresolved=sorted(pending),
        review_order=original_indices,selection=result,newly_selected=newly_selected,
        newly_selected_promoted=promoted,calls=calls,
        benefit_observed=bool(promoted),selection_budget_seconds=1200,call_timeout_seconds=240)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(dict(diagnostic_only=True,selected=len(result['selected']),
        new_pending_selections=len(newly_selected),promoted_selections=len(promoted),
        complete=result['selection_completed'])))
    if not promoted:raise SystemExit('No observed promoted selection benefit; do not integrate ranking')


if __name__=='__main__':main()
