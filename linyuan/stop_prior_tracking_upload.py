"""Stop only this repair's pre-edit overseas upload and release its dead lease."""
import base64
import json
import subprocess
import time

REPO='fastisrealslow/bilingual-subtitle-burner'
RUN=38684058923
HEAD='d8c7faede1b7532a133832076d0eb0a14e164dc6'
KEY='linyuan/.automation/pipeline_lease.json'
STATE='linyuan/.automation/fc_state.json'
BVID='BV1UEpC6bEQB'
# Captured read-only while RUN was still inside its serialized upload and the
# target receipt was upload_requested; never infer ownership from time alone.
CAPTURED_OWNER='1a46c93c078b4bf8a2ddeb2af65c5c3a'


def stop(api,read_state,cancel,sleep=time.sleep,now=time.time):
    run=api('GET',f'actions/runs/{RUN}')
    was_live=run.get('status')!='completed'
    if (run.get('head_sha')!=HEAD or run.get('head_branch')!='codex/linyuan-replace-jitter-20261015'
            or run.get('path')!='.github/workflows/linyuan-replace-reported-jitter.yml'):
        raise ValueError('Wrong prior repair run')
    txn=(read_state().get('tracking_revision_20261015') or {}).get(BVID) or {}
    if str(txn.get('workflow_run_id'))!=str(RUN):return dict(status='unrelated_receipt')
    if was_live and (txn.get('status')!='upload_requested' or txn.get('filename')):
        return dict(status='do_not_interrupt_edit')
    before=api('GET',f'contents/{KEY}?ref=main')
    lease=json.loads(base64.b64decode(before['content']))
    # apply() only creates the transaction after acquiring this shared lease.
    # Two-hour live leases cannot be acquired by another writer in between.
    if lease.get('owner')!=CAPTURED_OWNER or lease.get('expires_at',0)<=now():
        return dict(status='prior_lease_already_released')
    if (lease.get('kind')!='publish'
            or abs(lease['expires_at']-7200-float(txn.get('created_at') or 0))>10):
        raise ValueError('Cannot positively bind prior worker to its live lease')
    if was_live:cancel(RUN)
    for _ in range(20):
        if api('GET',f'actions/runs/{RUN}').get('status')=='completed':break
        sleep(3)
    else:raise RuntimeError('Prior worker not terminal; do not release a live lease')
    current=api('GET',f'contents/{KEY}?ref=main')
    value=json.loads(base64.b64decode(current['content']))
    if value.get('owner')==lease['owner'] and value.get('expires_at',0)>now():
        # CAS against the exact captured owner after the job is terminal.
        # A new publisher's lease, even milliseconds later, is never touched.
        api('PUT',f'contents/{KEY}',dict(sha=current['sha'],
            message='chore(repair): release exact stopped pre-edit upload lease',
            content=base64.b64encode(json.dumps(dict(owner=lease['owner'],expires_at=0)).encode()).decode()))
    return dict(status='prior_stopped',new_posts=0)


def api(method,path,payload=None):
    args=['gh','api','--method',method,f'repos/{REPO}/'+path]
    if payload is not None:args+=['--input','-']
    return json.loads(subprocess.check_output(args,input=json.dumps(payload).encode() if payload else None))


if __name__=='__main__':
    def read():
        return json.loads(subprocess.check_output(['gh','api','-H','Accept: application/vnd.github.raw+json',
            f'repos/{REPO}/contents/{STATE}?ref=main']))
    result=stop(api,read,lambda run:subprocess.run(['gh','run','cancel',str(run),'--repo',REPO],check=True))
    print(json.dumps(result))
