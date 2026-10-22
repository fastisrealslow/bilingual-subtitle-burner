"""Full baseline shelves must not suppress a missing treatment's bounded retry."""
import json
from pathlib import Path
import sys

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),
             str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as fc


def test_actual_eight_baseline_landscapes_cannot_cover_zero_treatment_landscapes(monkeypatch):
    monkeypatch.setattr(fc.time,'time',lambda:1792653000)
    state=dict(dispatched=[dict(slug='story',editorial_profile=fc.experiment.TREATMENT,
                               output_layout='landscape')])
    stock=dict(verified_landscape=8,experiment_ready=dict(landscape=0,portrait=0))
    normal=fc.landscape_admission_deficit(stock,state,[])
    needed=fc.experiment.production_deficits(stock,state,[])
    assert normal==0 and needed['landscape']==2
    assert fc.retry_supply_needed(state['dispatched'][0],normal,needed)
    assert not fc.retry_supply_needed(dict(output_layout='landscape',editorial_profile='baseline'),normal,needed)
    assert stock['verified_landscape']==8 and stock['experiment_ready']['landscape']==0


def test_actual_active_treatment_jobs_still_reserve_opportunities_not_stock(monkeypatch):
    monkeypatch.setattr(fc.time,'time',lambda:1792653000)
    entries=[dict(slug='story'+str(i),editorial_profile=fc.experiment.TREATMENT,
                  output_layout='landscape') for i in range(2)]
    state=dict(dispatched=entries)
    active=[dict(status='in_progress',display_title='中文源出片 · '+e['slug']) for e in entries]
    stock=dict(verified_landscape=8,experiment_ready=dict(landscape=0,portrait=0))
    needs=fc.experiment.production_deficits(stock,state,active)
    assert needs['landscape']==0 and not fc.retry_supply_needed(entries[0],0,needs)
    needs=fc.experiment.production_deficits(stock,state,active[:1])
    assert needs['landscape']==1 and fc.retry_supply_needed(entries[0],0,needs)
    assert stock['experiment_ready']['landscape']==0


def test_retry_claims_treatment_opportunity_before_new_mother_admission(monkeypatch):
    now=1792653000
    monkeypatch.setattr(fc.time,'time',lambda:now)
    retry=dict(slug='retry-story',source_url='https://www.bilibili.com/video/BVretry',
        editorial_profile=fc.experiment.TREATMENT,output_layout='landscape',
        source_check_retry_after=now-1,source_check_run_id=7)
    parent=dict(slug='parent',source_url='https://www.bilibili.com/video/BVparent',source='bilibili')
    state=dict(dispatched=[retry],published={},rejected=[],pending_retry=[])
    stock=dict(inventory_fresh=True,verified_landscape=8,verified_portrait=2,daily_mix_usable=10,
               experiment_ready=dict(landscape=1,portrait=2))
    calls=[]
    monkeypatch.setattr(fc,'load_state',lambda:state)
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda _:0)
    monkeypatch.setattr(fc,'source_inventory',lambda *a:stock)
    monkeypatch.setattr(fc,'save_state',lambda _:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    monkeypatch.setattr(fc,'staging_release_id',lambda:1)
    monkeypatch.setattr(fc,'obsolete_review_candidates',lambda *a:[])
    monkeypatch.setattr(fc,'experiment_reuse_candidates',lambda *a:iter([(parent,dict(run_id=8))]))
    monkeypatch.setattr(fc,'weekly_full_request',lambda *a:None)
    monkeypatch.setattr(fc,'_process_retries',lambda *a:None)
    def gh(method,path,*args,**kwargs):
        if method=='POST':calls.append(args[0]);return {}
        if fc.DATA_JSON in path:return b'[]'
        if fc.SOURCE_INVENTORY_KEY in path:return b'{}'
        return dict(workflow_runs=[])
    monkeypatch.setattr(fc,'gh',gh)
    def pick(items,st,limit,audit):audit.update(records=0,candidate_count=0);return []
    monkeypatch.setattr(fc,'pick',pick)
    result=fc._dispatch_admitted(dict(execution_backend='github',_refill_count=2))
    assert result['dispatched']==1 and len(calls)==1
    assert calls[0]['inputs']['slug']=='retry-story'
    assert calls[0]['inputs']['editorial_profile']==fc.experiment.TREATMENT
    assert calls[0]['inputs']['recovery_run_id']=='7'
    assert retry['source_check_attempts']==1 and 'source_check_retry_after' not in retry
    assert state['published']=={} and len(state['dispatched'])==1


def test_six_active_workers_still_prevent_any_seventh_retry(monkeypatch):
    state=dict(dispatched=[])
    stock=dict(inventory_fresh=True,verified_landscape=8,verified_portrait=10,
        daily_mix_usable=18,experiment_ready=dict(landscape=0,portrait=0))
    monkeypatch.setattr(fc,'load_state',lambda:state)
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda _:0)
    monkeypatch.setattr(fc,'source_inventory',lambda *a:stock)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    monkeypatch.setattr(fc.experiment,'preparing',lambda *a:True)
    def gh(method,path,*a,**kw):
        assert method=='GET' and '/actions/workflows/' in path
        return dict(workflow_runs=[dict(status='in_progress',display_title='中文源出片 · '+str(i))
                                  for i in range(6)] if 'in_progress' in path else [])
    monkeypatch.setattr(fc,'gh',gh)
    assert fc._dispatch_admitted(dict(execution_backend='github'))['active_sources']==6
