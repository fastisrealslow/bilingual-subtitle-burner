"""A proven selector repair may create one new child, never mutate old stock."""
from copy import deepcopy
import hashlib
from pathlib import Path
import sys

import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as F
import publishing_experiment as E


def fixture(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:100)
    monkeypatch.setattr(F,'inventory_record_current',lambda r:not r.get('expired'))
    monkeypatch.setattr(F,'inventory_publication_error',lambda p,*a:p.get('publication_error'))
    scope=dict(id='synthetic-recall',parent_slug='old-child',parent_artifact_id=11,
        parent_run_id=22,source_url='https://www.bilibili.com/video/BVunit',source_sha256='a'*64,
        selection_policy_sha256=hashlib.sha256(Path(E.__file__).read_bytes()).hexdigest(),
        expires_at=200,diagnostic_run_id=33,diagnostic_artifact_id=44,
        diagnostic_video_sha256='b'*64)
    monkeypatch.setattr(F,'PROVEN_COMPANY_RECALLS',(scope,))
    parent=dict(slug='old-child',source_url=scope['source_url'],ts=1,
                experiment_parent_slug='original',experiment_selection_revision=1)
    current=dict(slug='already-published-t',source_url=scope['source_url'],ts=2,
        experiment_parent_slug='original',experiment_selection_revision=E.SELECTION_REVISION,
        experiment_mother_sha256=scope['source_sha256'])
    state=dict(dispatched=[parent,current],published={current['slug']:dict(parts=[dict(
        status='published',bvid='BVreceipt')])})
    record=dict(slug=parent['slug'],artifact_id=11,run_id=22,parts=[dict(index=0,
        status='verified',source_sha256=scope['source_sha256'])])
    inventory=dict(artifacts=[record]);items=[dict(page_url=scope['source_url'],extra=dict(duration=1430))]
    return scope,state,inventory,items


def test_exact_source_repair_creates_distinct_new_slug_without_touching_any_old_data(monkeypatch):
    scope,state,inventory,items=fixture(monkeypatch);before=deepcopy((state,inventory,items))
    choices=list(F.proven_company_recall_candidates(state,inventory,items))
    assert len(choices)==1
    parent,record=choices[0]
    assert record['run_id']==22 and parent['source_sha256']==scope['source_sha256']
    assert F.experiment_reuse_slug(parent)!=F.experiment_reuse_slug(state['dispatched'][0])
    assert set(parent['experiment_proven_recall'])=={'id','source_sha256','selection_policy_sha256',
        'diagnostic_run_id','diagnostic_artifact_id','diagnostic_video_sha256'}
    assert (state,inventory,items)==before
    state['dispatched'].append(dict(slug=F.experiment_reuse_slug(parent),
        experiment_proven_recall=parent['experiment_proven_recall'],failed=True,ts=3))
    assert not list(F.proven_company_recall_candidates(state,inventory,items))


@pytest.mark.parametrize('change',['expired_scope','code','parent','artifact','run','source','hash',
    'expired_record','failed','uploading','processed','rejected','no_parts','unknown_pending',
    'retry_pending','active','short','reference','no_reprint','competitor','no_admission'])
def test_unproven_missing_or_inflight_inputs_never_dispatch(monkeypatch,change):
    scope,state,inventory,items=fixture(monkeypatch);parent=state['dispatched'][0]
    current=state['dispatched'][1];record=inventory['artifacts'][0];active=[]
    if change=='expired_scope':scope['expires_at']=100
    elif change=='code':scope['selection_policy_sha256']='c'*64
    elif change=='parent':scope['parent_slug']='missing'
    elif change=='artifact':record['artifact_id']=12
    elif change=='run':record['run_id']=23
    elif change=='source':parent['source_url']+='?p=2'
    elif change=='hash':record['parts'][0]['source_sha256']='d'*64
    elif change=='expired_record':record['expired']=True
    elif change=='failed':parent['failed']=True
    elif change=='uploading':current['uploading']=True
    elif change=='processed':parent['processed_part_indices']=[0]
    elif change=='rejected':record['parts'][0]['publication_error']='actual current rejection'
    elif change=='no_parts':record['parts']=[]
    elif change=='unknown_pending':state['published']={}
    elif change=='retry_pending':current['source_check_retry_after']=200
    elif change=='active':active=[dict(status='in_progress',display_title='中文源出片 · '+parent['slug'])]
    elif change=='short':items[0]['extra']['duration']=1199
    elif change=='reference':items[0]['extra']['source_role']='reference'
    elif change=='no_reprint':items[0]['extra']['no_reprint']=1
    elif change=='competitor':items[0]['author']=next(iter(F.COMPETITOR_AUTHORS))
    else:items=[]
    assert not list(F.proven_company_recall_candidates(state,inventory,items,active))


def test_regular_reuse_slug_remains_unchanged(monkeypatch):
    _,state,*_=fixture(monkeypatch);parent=state['dispatched'][0]
    assert F.experiment_reuse_slug(parent)=='ly-story-'+E.digest([
        E.EXPERIMENT_ID,parent['source_url'],E.SELECTION_REVISION])[:10]


def test_actual_diagnostic_format_guides_planning_without_becoming_stock(monkeypatch):
    scope,state,inventory,items=fixture(monkeypatch)
    scope.update(diagnostic_resolution=dict(width=720,height=1280),diagnostic_render_mode='live_video_card')
    inventory['artifacts'][0]['parts'][0].update(resolution=dict(width=1280,height=720),render_mode='direct')
    before=deepcopy((state,inventory,items))
    choices=list(F.proven_company_recall_candidates(state,inventory,items))
    assert choices[0][1]['parts']==[]
    assert choices[0][1]['source_geometry_hints'][0]['diagnostic_only'] is True
    plan=F.experiment_reuse_plan(choices,state,dict(portrait=1,landscape=2))
    assert len(plan)==1 and plan[0][2]=='portrait'
    assert (state,inventory,items)==before


def failed_fixture(monkeypatch):
    scope,state,inventory,items=fixture(monkeypatch)
    parent=state['dispatched'][0];parent.update(failed=True,last_error='Old source quality failure')
    inventory['artifacts']=[]
    scope.update(failed_parent_sha256=E.digest(parent),diagnostic_source_duration_sec=1501,
        diagnostic_resolution=dict(width=720,height=1280),diagnostic_render_mode='live_video_card')
    items[0]['extra']['duration']=7212
    return scope,state,inventory,items


def test_exact_failed_parent_can_create_one_fresh_job_without_changing_history(monkeypatch):
    scope,state,inventory,items=failed_fixture(monkeypatch);before=deepcopy((state,inventory,items))
    choices=list(F.proven_company_recall_candidates(state,inventory,items))
    assert len(choices)==1 and choices[0][1]['run_id'] is None
    assert choices[0][1]['parts']==[] and choices[0][1]['source_geometry_hints'][0]['diagnostic_only']
    assert (state,inventory,items)==before and state['dispatched'][0]['failed'] is True
    state['dispatched'].append(dict(slug='new-child',ts=3,failed=True,
        experiment_proven_recall=choices[0][0]['experiment_proven_recall']))
    assert not list(F.proven_company_recall_candidates(state,inventory,items))


@pytest.mark.parametrize('change',['fingerprint','parent_changed','not_failed','old_stock','receipt',
    'short_actual','long_actual','no_geometry','no_run','no_artifact','bad_video','active','restricted'])
def test_failed_source_repair_requires_exact_history_and_actual_trial_evidence(monkeypatch,change):
    scope,state,inventory,items=failed_fixture(monkeypatch);parent=state['dispatched'][0];active=[]
    if change=='fingerprint':scope['failed_parent_sha256']='c'*64
    elif change=='parent_changed':parent['last_error']='New failure'
    elif change=='not_failed':parent['failed']=False
    elif change=='old_stock':inventory['artifacts']=[dict(slug=parent['slug'])]
    elif change=='receipt':state['published'][parent['slug']]=dict(parts=[dict(status='published',bvid='old')])
    elif change=='short_actual':scope['diagnostic_source_duration_sec']=1199
    elif change=='long_actual':scope['diagnostic_source_duration_sec']=5401
    elif change=='no_geometry':scope.pop('diagnostic_resolution')
    elif change=='no_run':scope['diagnostic_run_id']=None
    elif change=='no_artifact':scope['diagnostic_artifact_id']=None
    elif change=='bad_video':scope['diagnostic_video_sha256']='bad'
    elif change=='active':active=[dict(status='in_progress',display_title='中文源出片 · '+parent['slug'])]
    else:items[0]['extra']['no_reprint']=1
    assert not list(F.proven_company_recall_candidates(state,inventory,items,active))


def test_source_repair_output_is_bound_to_the_actual_proven_mother():
    entry=dict(experiment_mother_sha256='a'*64,experiment_proven_recall=dict(source_sha256='a'*64))
    assert F.proven_recall_source_error(dict(source_sha256='a'*64),entry) is None
    assert F.proven_recall_source_error(dict(source_sha256='b'*64),entry)
    entry['experiment_mother_sha256']='b'*64
    assert F.proven_recall_source_error(dict(source_sha256='a'*64),entry)
    assert F.proven_recall_source_error({},dict(experiment_proven_recall=dict(source_sha256='bad')))
    assert F.proven_recall_source_error({},dict()) is None
