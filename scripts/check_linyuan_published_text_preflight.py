"""Frozen real-source preflight replay. No inference, rendering or publication."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import produce_cn as P
import publishing_experiment as E
import editorial_policy as editorial
import index as F


def download(artifact,sha):
    raw=subprocess.check_output(['gh','api',
        f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip'],timeout=120)
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Immutable artifact changed')
    return zipfile.ZipFile(io.BytesIO(raw))


def main():
    repo=os.environ['GITHUB_REPOSITORY'];frozen='d89d69fd48392c0f9bd56375cb6f758182d1f05a'
    blob='4b571c6b133b134956e3b5847af2e5df9689b058'
    payload=json.loads(subprocess.check_output(['gh','api',f'repos/{repo}/git/blobs/{blob}']))
    raw=base64.b64decode(payload['content'])
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=blob:
        raise ValueError('Publication history blob changed')
    state=json.loads(raw);snapshot=json.dumps(state,sort_keys=True)
    with download(12472006702,'722f0b4d2d15f99f672240ec684bb7fe1c3f052869fa131c36fb492db0f0c760') as z:
        cues=json.loads(z.read('_tmp/cues_raw.json'))
        source=json.loads(z.read('_tmp/source_quality.json'))
        prior=json.loads(z.read('_tmp/story_selection.json'))
    if source['source_sha256']!='4451ef2e781cc96a640277bf5b809534e83aa09107d6455185ca37e5fb31b9c4':
        raise ValueError('Original source changed')
    candidates=prior['candidates'];url='https://www.bilibili.com/video/BV1Y4Jv6gEYq'
    before=[]
    for pick in candidates:
        meta=dict(source_sha256=source['source_sha256'],editorial_profile=E.TREATMENT,
            long_story_family=E.family_metadata(source['source_sha256'],source['duration_sec']),
            segments=[dict(start=cues[pick['start']]['start'],end=cues[pick['end']]['end'])])
        if not editorial.source_reuse_error(meta,url,E.comparison_state(meta,state,preflight=True)):
            before.append(pick)
    eligible,excluded=P.story_selection_inputs(cues,candidates,source,url,state)
    text_excluded=[r for r in excluded if '文字指纹重复' in r['reason']]
    selected_indices={i for i,p in enumerate(candidates) if any(
        p['start']==s['start'] and p['end']==s['end'] for s in prior['selected'])}
    # Existing accepted real landscape must NOT disappear under shared rules.
    with download(12440338758,'6b009b88b23995ab55bc219b7a07d4bd03ea83c71e70868f8f52f09530b771a8') as z:
        accepted=json.loads(z.read('meta.json'))
        if hashlib.sha256(z.read('final.mp4')).hexdigest()!='fbb19708492be26a2646d58c0219f919f0995eeb90db97b382f250e74d5021fe':
            raise ValueError('Existing accepted final bytes changed')
    positive=F.find_content_duplicate(accepted['fingerprints'],state)
    passed=(len(candidates)==len(before)==37 and len(eligible)==18 and len(text_excluded)==19
        and selected_indices.issubset({r['candidate_index'] for r in text_excluded})
        and positive is None and snapshot==json.dumps(state,sort_keys=True))
    report=dict(diagnostic_only=True,publication_approved=False,audio_verified=False,
        source_cues_changed=False,state_unchanged=snapshot==json.dumps(state,sort_keys=True),
        frozen_state_commit=frozen,frozen_state_blob=blob,source_artifact=12472006702,
        source_run=39881075724,source_sha256=source['source_sha256'],
        original_candidates=37,old_preflight_eligible=len(before),new_preflight_eligible=len(eligible),
        already_published_text_duplicates=len(text_excluded),excluded=excluded,
        old_selected_indices=sorted(selected_indices),existing_accepted_landscape_duplicate=positive,
        new_stock=0,passed=passed)
    Path('published-text-preflight-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='excluded'},ensure_ascii=False))
    if not passed:raise SystemExit('Real paired preflight regression failed; do not release')


if __name__=='__main__':main()
