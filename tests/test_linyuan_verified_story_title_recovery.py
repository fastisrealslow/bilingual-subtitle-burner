"""A repaired actual title earns one ordinary source job, never a T label."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import verified_story_title_recovery as R


@pytest.fixture
def state():
    original=dict(slug=R.ORIGINAL,source_url=R.SOURCE,editorial_profile=R.TREATMENT,
        experiment_mother_sha256=R.MOTHER,experiment_parent_slug='ly-1025-a16d29',
        experiment_selection_revision=2026102201,source_check_attempts=2,ts=1793248138,
        source_sha256=R.MOTHER,editorial_approved=True,selected_parts='1',quality_retries=1)
    return dict(dispatched=[original],published={},daily_publish=dict(date='2026-10-29',count=4))


@pytest.fixture
def stock():
    return dict(inventory_fresh=True,experiment_ready=dict(landscape=0,portrait=0))


def test_exact_verified_source_can_request_one_new_normal_job_without_altering_old_approvals_or_counters(state,stock):
    before=deepcopy(state)
    child,issue=R.plan(state,stock,[],[])
    assert issue is None and child['source_url']==R.SOURCE and child['slug']==R.CHILD
    assert child['editorial_profile']==R.TREATMENT and child['output_layout']=='landscape'
    assert child['title_recovery_evidence']['media_approved'] is False
    assert not any(k in child for k in ['editorial_approved','selected_parts','quality_retries','source_check_attempts'])
    assert state==before and state['daily_publish']['count']==4


def test_unknown_response_or_any_existing_recovery_entry_cannot_replay(state,stock):
    state['dispatched'].append(dict(slug=R.CHILD))
    assert R.plan(state,stock,[],[])[1]=='already_attempted_or_unresolved'


def test_worker_ceiling_related_active_source_and_known_ban_remain_closed(state,stock):
    active=[dict(status='queued',display_title='中文源出片 · other')]*6
    assert R.plan(state,stock,active,[])[1]=='worker_limit'
    assert R.plan(state,stock,[dict(status='in_progress',display_title='中文源出片 · '+R.ORIGINAL)],[])[1]=='related_source_running'
    assert R.plan(state,stock,[],[dict(bvid='BV1iC4y1H7JN',no_reprint=1)])[1]=='source_restricted'


def test_unknown_stock_or_sufficient_real_trials_do_not_create_a_job(state,stock):
    assert R.plan(state,{**stock,'inventory_fresh':False},[],[])[1]=='inventory_unknown'
    stock['experiment_ready']['landscape']=4
    assert R.plan(state,stock,[],[])[1]=='trial_reserve_covered'


@pytest.mark.parametrize('change',[
    dict(source_url='https://www.bilibili.com/video/BV1OTHERwrong'),
    dict(experiment_mother_sha256='a'*64),dict(editorial_profile='baseline'),
    dict(failed=True),dict(source_quality_rejected=True),
])
def test_changed_or_restricted_original_cannot_borrow_cpu_proof(state,stock,change):
    state['dispatched'][0].update(change)
    assert R.plan(state,stock,[],[])[1]=='source_or_state_changed'


def test_normal_request_preserves_parent_and_retains_intent_on_unknown_api_result(state,stock,monkeypatch):
    before=deepcopy(state['dispatched'][0]);saved=[];requests=[]
    monkeypatch.setattr(R,'proof_error',lambda *a:None)
    monkeypatch.setattr(R.F,'load_state',lambda:state)
    monkeypatch.setattr(R.F,'save_state',lambda s:saved.append(deepcopy(s)))
    monkeypatch.setattr(R.F,'source_inventory',lambda *a:stock)
    def gh(method,path,**kw):
        if '/contents/' in path:return json.dumps(dict(seeds=[])).encode()
        return dict(workflow_runs=[])
    monkeypatch.setattr(R.F,'gh',gh)
    def submit(s,child,slug,reason,artifact_id):
        assert s['dispatched'][-1]['slug']==R.CHILD
        assert saved and saved[-1]['dispatched'][-1]['slug']==R.CHILD
        requests.append((slug,artifact_id));return False
    monkeypatch.setattr(R.F,'_request_quality_reprocess',submit)
    result=R.request({},{});assert result['dispatched']==0 and result['actual_treatment_stock_added']==0
    assert state['dispatched'][0]==before and requests==[(R.CHILD,R.DELIVERY_ARTIFACT)]
    assert R.request({}, {})['reason']=='already_attempted_or_unresolved' and len(requests)==1


def test_actual_cpu_proof_cannot_be_used_before_matching_runtime_deployment():
    cpu=json.loads((Path(__file__).with_name('fixtures')/'linyuan_story_title_release_20261030.json').read_text())
    root=Path(__file__).resolve().parents[1]
    health=dict(daily_limit=4,publishing_experiment=dict(treatment_slots=[2,4],id=R.F.experiment.EXPERIMENT_ID),
        experiment_runtime=dict(passed=True),
        publishing_experiment_sha256=hashlib.sha256((root/'linyuan/publishing_experiment.py').read_bytes()).hexdigest(),
        code_sha256=hashlib.sha256((root/'linyuan/fc/index.py').read_bytes()).hexdigest())
    deploy=dict(verification_complete=True,health=health)
    assert R.proof_error(cpu,deploy) is None
    assert R.proof_error({**cpu,'source_sha256':'a'*64},deploy)=='actual_cpu_case_not_verified'
    assert R.proof_error(cpu,{**deploy,'verification_complete':False})=='deployment_not_verified'
    assert R.proof_error(cpu,{**deploy,'health':{**health,'publishing_experiment_sha256':'a'*64}})=='deployed_code_differs'
