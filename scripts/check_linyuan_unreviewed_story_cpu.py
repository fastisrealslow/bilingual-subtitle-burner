"""Review only the REAL pending classifier queue; never render or publish."""
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

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E


def main():
    model=os.environ['LOCAL_LLM_MODEL']
    if model!='qwen3.5:9b':raise ValueError('Use the current production model only')
    artifact=12467582807
    raw=subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],timeout=120)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        source=json.loads(archive.read('_tmp/source_quality.json'))
        if source.get('source_sha256')!='8b5c9d11737003b7a409fabf803ff7b185ed50bd4ed171a9e0377ac95e67e313':
            raise ValueError('Source binding changed')
        cues=json.loads(archive.read('_tmp/cues_raw.json'))
        prior=json.loads(archive.read('_tmp/story_selection.json'))
    if prior['revision']!=E.SELECTION_REVISION or prior['transcript_sha256']!=E.digest(cues):
        raise ValueError('Pending queue does not match the actual transcript/revision')
    pending=[r['index'] for r in prior['unresolved'] if r['error']=='selection_budget_exhausted']
    if len(pending)!=13 or len(set(pending))!=13:raise ValueError('Actual pending queue changed')
    # Indices come from the actual machine's unresolved report, not a human
    # selected story/range or any previous positive editorial verdict.
    picks=[prior['candidates'][i] for i in pending]
    out=Path('unreviewed-story-cpu-results');out.mkdir(exist_ok=True)
    calls=[]
    def call(prompt,schema):
        payload=dict(model=model,messages=[dict(role='user',content=prompt)],
            format=schema,stream=False,think=False,
            options=dict(temperature=0,num_predict=1600,num_ctx=16384))
        request=urllib.request.Request('http://127.0.0.1:11434/api/chat',
            data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        row=dict(prompt=prompt,schema=schema);calls.append(row);started=time.monotonic()
        with urllib.request.urlopen(request,timeout=240) as response:result=json.load(response)
        row.update(response=result['message']['content'],elapsed_sec=time.monotonic()-started)
        return row['response']
    selected=E.choose_stories(cues,picks,call,out/'selection.json',budget_sec=1200)
    report=dict(diagnostic_only=True,publication_approved=False,audio_verified=False,
        source_cues_changed=False,experiment_id=E.EXPERIMENT_ID,revision=E.SELECTION_REVISION,
        source_sha256=source['source_sha256'],source_artifact=artifact,source_run=39864415088,
        original_pending_indices=pending,model=model,selection=selected,calls=calls,
        experiment_code_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest(),
        selected_count=len(selected['selected']),selection_completed=selected['selection_completed'])
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:report[k] for k in ('diagnostic_only','selected_count','selection_completed')}))
    if not selected['selection_completed']:raise SystemExit('Pending review still incomplete')


if __name__=='__main__':main()
