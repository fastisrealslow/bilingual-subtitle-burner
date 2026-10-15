import base64
import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import stop_prior_tracking_upload as s


def environment(stage='upload_requested',changed_owner=False,terminal=True):
    calls=[];stopped=[]
    def api(method,path,payload=None):
        calls.append((method,path,payload))
        if path.startswith('actions/'):
            return dict(status='completed' if stopped and terminal else 'in_progress',
                head_sha=s.HEAD,head_branch='codex/linyuan-replace-jitter-20261015',
                path='.github/workflows/linyuan-replace-reported-jitter.yml')
        if method=='PUT':return {}
        return dict(sha='lease-sha',content=base64.b64encode(json.dumps(dict(
            owner='another-writer' if stopped and changed_owner else s.CAPTURED_OWNER,
            kind='publish',expires_at=8200)).encode()))
    state=dict(tracking_revision_20261015={s.BVID:dict(status=stage,
        workflow_run_id=str(s.RUN),created_at=1000)})
    return api,lambda:state,lambda run:stopped.append(run),calls,stopped


def test_only_exact_dead_lease_is_released_after_cancel():
    api,read,cancel,calls,stopped=environment()
    assert s.stop(api,read,cancel,now=lambda:1001)['status']=='prior_stopped'
    assert stopped==[s.RUN] and sum(m=='PUT' for m,_,_ in calls)==1


def test_never_release_a_different_writers_lease():
    api,read,cancel,calls,stopped=environment(changed_owner=True)
    s.stop(api,read,cancel,now=lambda:1001)
    assert not any(m=='PUT' for m,_,_ in calls)


def test_never_interrupt_or_replay_an_edit_request():
    api,read,cancel,calls,stopped=environment(stage='edit_requested')
    assert s.stop(api,read,cancel,now=lambda:1001)['status']=='do_not_interrupt_edit'
    assert not stopped and not any(m=='PUT' for m,_,_ in calls)


def test_never_release_until_the_old_worker_is_actually_terminal():
    api,read,cancel,calls,stopped=environment(terminal=False)
    with pytest.raises(RuntimeError):s.stop(api,read,cancel,now=lambda:1001,sleep=lambda _:None)
    assert not any(m=='PUT' for m,_,_ in calls)
