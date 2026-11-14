from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import documented_company_recovery as C
import verified_story_title_recovery as R


def proof():
    f=json.loads((C.ROOT/'tests/fixtures/linyuan_documented_company_source_20261030.json').read_text())
    pick=f['candidates'][1]
    selected=C.E.validate_choices(f['source_cues'],[pick],{'candidate_0':dict(kind='company_case',
        subject_sentence=8,event_sentence=9,detail_sentence=15)})
    selected[0]['story_selection']['company_documentary_recall_version']=1
    return dict(passed=True,model='qwen3.5:9b',source_run_id=40993786441,source_sha256=R.MOTHER,
        fixture_sha256=C.E.digest(f),baseline_selected=0,primary_replayed_calls=4,
        selection=dict(selected=selected),controls=[dict(name=n,kind='skip') for n in
            ('quoted_other_author','different_company_result')])


@pytest.fixture
def state(monkeypatch):
    monkeypatch.setattr(R.F.experiment,'production_preparing',lambda:True)
    return dict(dispatched=[dict(slug=R.ORIGINAL,source_url=R.SOURCE,editorial_profile=R.TREATMENT,
        experiment_mother_sha256=R.MOTHER,experiment_selection_revision=2026102201,
        selected_parts='1',quality_retries=2,source_check_attempts=2),
        dict(slug=R.ASR_CHILD,source_url=R.SOURCE,experiment_mother_sha256=R.MOTHER)],published={})


def test_new_model_evidence_earns_one_new_job_without_replaying_selected_ranges_or_old_counters(state):
    original=deepcopy(state);stock=dict(inventory_fresh=True,experiment_ready=dict(landscape=0,portrait=0))
    p=proof();assert C.cpu_error(p) is None
    child,error=R.plan(state,stock,[],[],documentary_proof=p)
    assert error is None and child['slug']==R.DOCUMENTARY_CHILD and state==original
    assert child['documented_company_recovery_evidence']['media_approved'] is False
    assert not {'selected_parts','quality_retries','source_check_attempts','story_selection','segments'}&child.keys()
    state['dispatched'].append(child)
    assert R.plan(state,stock,[],[],documentary_proof=p)[1]=='already_attempted_or_unresolved'


def test_unknown_stock_worker_ceiling_related_source_or_ban_still_prevents_request(state):
    p=proof();stock=dict(inventory_fresh=True,experiment_ready={})
    assert R.plan(state,{**stock,'inventory_fresh':False},[],[],documentary_proof=p)[1]=='inventory_unknown'
    assert R.plan(state,stock,[dict(status='queued')]*6,[],documentary_proof=p)[1]=='worker_limit'
    assert R.plan(state,stock,[dict(status='in_progress',display_title='中文源出片 · '+R.ASR_CHILD)],[],documentary_proof=p)[1]=='related_source_running'
    assert R.plan(state,stock,[],[dict(bvid='BV1iC4y1H7JN',no_reprint=1)],documentary_proof=p)[1]=='source_restricted'


@pytest.mark.parametrize('field,value',[('passed',False),('source_sha256','a'*64),('model','other'),
    ('fixture_sha256','a'*64),('primary_replayed_calls',0)])
def test_wrong_or_unfinished_actual_model_evidence_never_authorizes_a_retry(field,value):
    p=proof();p[field]=value
    assert C.cpu_error(p)


def test_positive_case_without_negative_controls_or_with_a_false_positive_is_rejected():
    p=proof();p['controls']=[];assert C.cpu_error(p)
    p=proof();p['controls'][0]['kind']='company_case';assert C.cpu_error(p)


def test_deployed_code_must_match_new_classifier_and_current_runtime():
    h=dict(daily_limit=4,publishing_experiment=dict(id=C.E.EXPERIMENT_ID,treatment_slots=[2,4]),
        experiment_runtime=dict(passed=True,documented_company_story_sha256=hashlib.sha256(
            (C.ROOT/'linyuan/documented_company_story.py').read_bytes()).hexdigest()),
        code_sha256=hashlib.sha256((C.ROOT/'linyuan/fc/index.py').read_bytes()).hexdigest(),
        publishing_experiment_sha256=hashlib.sha256((C.ROOT/'linyuan/publishing_experiment.py').read_bytes()).hexdigest())
    d=dict(verification_complete=True,health=h)
    h['editorial_code_sha256']=hashlib.sha256((C.ROOT/'linyuan/editorial_policy.py').read_bytes()).hexdigest()
    h['experiment_runtime']['source_timeline_module_sha256']=hashlib.sha256(
        (C.ROOT/'linyuan/source_timeline.py').read_bytes()).hexdigest()
    h['experiment_runtime']['source_timeline_evidence_sha256']=hashlib.sha256(
        (C.ROOT/'linyuan/source_timeline_evidence.json').read_bytes()).hexdigest()
    assert C.deployment_error(d) is None
    h['experiment_runtime']['documented_company_story_sha256']='a'*64
    assert C.deployment_error(d)=='deployed_code_differs'


def test_uncertain_dispatch_keeps_intent_and_cannot_submit_again(state,monkeypatch):
    p=proof();saved=[];sent=[]
    monkeypatch.setattr(C.F,'load_state',lambda:state)
    monkeypatch.setattr(C.F,'save_state',lambda s:saved.append(deepcopy(s)))
    monkeypatch.setattr(C.F,'source_inventory',lambda *a:dict(inventory_fresh=True,experiment_ready={}))
    monkeypatch.setattr(C.F,'gh',lambda method,path,**kw:json.dumps(dict(seeds=[])).encode() if '/contents/' in path else dict(workflow_runs=[]))
    def submit(st,child,slug,reason,artifact):
        assert saved[-1]['dispatched'][-1]['slug']==R.DOCUMENTARY_CHILD
        sent.append((slug,artifact));return False
    monkeypatch.setattr(C.F,'_request_quality_reprocess',submit)
    assert C.request(p)['dispatched']==0
    assert C.request(p)['reason']=='already_attempted_or_unresolved'
    assert sent==[(R.DOCUMENTARY_CHILD,C.DELIVERY_ARTIFACT)]
