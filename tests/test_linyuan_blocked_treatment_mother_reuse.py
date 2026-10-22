"""Old unpublished overlap cannot reserve an entire long mother forever."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as F
import publishing_experiment as E


def fixture(monkeypatch):
    # Synthetic admission protocol fixture, not media/stock approval.
    monkeypatch.setattr(F.time,'time',lambda:datetime(2026,10,23,4,tzinfo=E.CST).timestamp())
    monkeypatch.setattr(F,'source_inventory',lambda *a:dict(inventory_fresh=True))
    monkeypatch.setattr(F,'inventory_record_current',lambda r,*a:not r.get('expired'))
    monkeypatch.setattr(E,'actual_profile',lambda p:E.TREATMENT if p.get('synthetic_treatment') else E.BASELINE)
    monkeypatch.setattr(F,'inventory_publication_error',lambda p,*a:p.get('publication_error'))
    mother='a'*64;url='https://www.bilibili.com/video/BVunit'
    parent=dict(slug='parent',source_url=url,source_sha256=mother,ts=1)
    old=dict(slug='old-treatment',source_url=url,editorial_profile=E.TREATMENT,
        experiment_selection_revision=2026102005,experiment_parent_slug=parent['slug'],
        experiment_mother_sha256=mother,ts=2)
    part=dict(index=0,status='verified',source_sha256=mother,synthetic_treatment=True,
        publication_error='与已经发布的母片时间段重叠，保留其他完整观点',
        sha256='b'*64,resolution=dict(width=720,height=1280),render_mode='live_video_card')
    record=dict(slug=old['slug'],artifact_id=11,run_id=22,parts=[part])
    state=dict(dispatched=[parent,old],published={parent['slug']:dict(parts=[dict(
        status='published',bvid='BVunit-receipt',source_sha256=mother,
        source_segments=[dict(start=200,end=300)],resolution=dict(width=1280,height=720),
        render_mode='direct')])})
    stock=dict(artifacts=[record])
    items=[dict(page_url=url,extra=dict(duration=3600))]
    return state,stock,items,parent,old,part


def test_older_unpublished_overlap_can_be_reselected_once_without_relabeling(monkeypatch):
    state,stock,items,parent,old,part=fixture(monkeypatch)
    before=deepcopy((state,stock,items))
    choices=list(F.experiment_reuse_candidates(state,stock,items))
    assert len(choices)==1 and choices[0][0]['slug']==parent['slug']
    record=choices[0][1]
    assert record['run_id']==22 and record['source_geometry_hints'][0]['geometry_artifact_id']==11
    assert 'parts' not in record  # A geometry hint is not parent delivery/stock.
    plan=F.experiment_reuse_plan(choices,state,dict(portrait=2,landscape=1))
    assert plan[0][2]=='portrait'  # The old physical portrait, not its publishability.
    assert (state,stock,items)==before
    state['dispatched'].append(dict(slug='current-attempt',source_url=parent['source_url'],
        experiment_selection_revision=E.SELECTION_REVISION,experiment_parent_slug=parent['slug'],
        experiment_mother_sha256=parent['source_sha256'],failed=True,ts=3))
    assert not list(F.experiment_reuse_candidates(state,stock,items))


@pytest.mark.parametrize('change',['current_revision','usable','processed','published','uploading',
                                  'expired','wrong_hash','unknown_error'])
def test_current_success_unknown_or_consumed_outcome_still_blocks_duplicates(monkeypatch,change):
    state,stock,items,parent,old,part=fixture(monkeypatch)
    if change=='current_revision':old['experiment_selection_revision']=E.SELECTION_REVISION
    elif change=='usable':part.pop('publication_error')
    elif change=='processed':old['published_parts']=1
    elif change=='published':state['published'][old['slug']]=dict(parts=[dict(status='published',bvid='BVposted')])
    elif change=='uploading':old['uploading']=True
    elif change=='expired':stock['artifacts'][0]['expired']=True
    elif change=='wrong_hash':part['source_sha256']='c'*64
    else:part['publication_error']='Current source evidence unavailable'
    assert not list(F.experiment_reuse_candidates(state,stock,items))


@pytest.mark.parametrize('extra',[dict(duration=1199),dict(duration=3600,no_reprint=1),
    dict(duration=3600,source_role='reference'),dict(duration=3600,direct_dispatch=False)])
def test_old_overlap_never_bypasses_source_admission(monkeypatch,extra):
    state,stock,items,*_=fixture(monkeypatch)
    items[0]['extra']=extra
    assert not list(F.experiment_reuse_candidates(state,stock,items))


def test_any_still_usable_old_treatment_keeps_the_mother_reserved(monkeypatch):
    state,stock,items,parent,old,part=fixture(monkeypatch)
    stock['artifacts'][0]['parts'].append({**part,'index':1,'publication_error':None})
    assert not list(F.experiment_reuse_candidates(state,stock,items))
