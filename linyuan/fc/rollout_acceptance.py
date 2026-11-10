"""Require a real public CI publication before retiring four paid wake-ups."""
import hashlib
import json
from pathlib import Path
import re

PROOF='linyuan/.automation/fc_cost_rollout.json'
FILES=('linyuan/ci_upload.py','linyuan/fc/ci_upload_client.py',
       'linyuan/fc/publish_on_runner.py','linyuan/fc/index.py',
       '.github/workflows/linyuan-publish-runner.yml')


def code_hashes(root):
    return {name:hashlib.sha256((Path(root)/name).read_bytes()).hexdigest() for name in FILES}


def assert_clock_acceptance(root=None):
    root=Path(root) if root else Path(__file__).resolve().parents[2]
    try:
        proof=json.loads((root/PROOF).read_text())
        state=json.loads((root/'linyuan/.automation/fc_state.json').read_text())
    except (OSError,ValueError) as exc:
        raise SystemExit('CI clock cutover requires a durable live publication proof') from exc
    if (proof.get('backend')!='github-qn' or proof.get('public') is not True
            or proof.get('owner_verified') is not True or proof.get('fc_invocations')!=0
            or proof.get('experiment_compliant') is not True
            or not re.fullmatch(r'BV[a-zA-Z0-9]{10}',str(proof.get('bvid') or ''))
            or not re.fullmatch('[a-f0-9]{64}',str(proof.get('sha256') or ''))
            or proof.get('code_hashes')!=code_hashes(root)):
        raise SystemExit('CI clock cutover proof is missing or belongs to different code')
    receipts=[p for v in state.get('published',{}).values() for p in v.get('parts',[])
        if p.get('status')=='published' and p.get('bvid')==proof['bvid']
        and (p.get('fingerprints') or {}).get('sha256')==proof['sha256']
        and p.get('upload_backend')=='github-qn'
        and (p.get('publishing_experiment') or {}).get('compliant') is True]
    if len(receipts)!=1:
        raise SystemExit('CI clock proof has no exact durable publication receipt')
    return proof


def proof_for_publication(state,bvid,creator,expected_slot,root=None):
    root=Path(root) if root else Path(__file__).resolve().parents[2]
    rows=[p for v in state.get('published',{}).values() for p in v.get('parts',[])
          if p.get('status')=='published' and p.get('bvid')==bvid]
    videos=[v for v in creator.get('videos',[]) if v.get('bvid')==bvid]
    if (len(rows)!=1 or len(videos)!=1 or not creator.get('owner_verified')
            or str(creator.get('owner_mid'))!='275211725'):
        raise ValueError('No exact owned creator/publication evidence')
    part,video=rows[0],videos[0]
    metrics=part.get('upload_metrics') or {}
    if (part.get('upload_backend')!='github-qn' or metrics.get('fc_invocations')!=0
            or not video.get('public') or str(video.get('mid'))!='275211725'
            or video.get('title')!=part.get('title')
            or abs(float(video.get('duration') or 0)-float(part.get('duration_sec') or 0))>2
            or abs(float(video.get('ctime') or 0)-float(part.get('ts') or 0))>120
            or part.get('scheduled_slot')!=expected_slot
            or (part.get('publishing_experiment') or {}).get('compliant') is not True):
        raise ValueError('Canary did not preserve slot, experiment, file or public ownership')
    sha=(part.get('fingerprints') or {}).get('sha256')
    if metrics.get('video_sha256')!=sha or not re.fullmatch('[a-f0-9]{64}',str(sha or '')):
        raise ValueError('Canary transfer is not bound to the actual reviewed video')
    return dict(version=1,backend='github-qn',bvid=bvid,sha256=sha,public=True,
        owner_verified=True,fc_invocations=0,slot=expected_slot,experiment_compliant=True,
        code_hashes=code_hashes(root),upload_seconds=metrics.get('total_seconds'),
        publication_ts=part['ts'])
