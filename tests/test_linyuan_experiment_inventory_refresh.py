"""Expired experiment inventory must recover, not silently choose controls."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as F
import publishing_experiment as E


def setup(monkeypatch,hour=14,day='2026-10-23',count=0):
    now=datetime.fromisoformat(f'{day}T{hour:02d}:00:00').replace(tzinfo=E.CST).timestamp()
    state=dict(dispatched=[dict(slug='older-real-treatment',ts=now-8*3600,
        editorial_profile=E.TREATMENT,production_rules_version=F.PRODUCTION_RULES_VERSION)],
        published={},daily_publish=dict(date=day,count=count,published_hours=[]))
    monkeypatch.setattr(F.time,'time',lambda:now)
    monkeypatch.setattr(F,'load_state',lambda:state)
    monkeypatch.setattr(F,'save_state',lambda _:None)
    monkeypatch.setattr(F,'log_event',lambda *args:None)
    monkeypatch.setattr(F,'_collect_source_rejections',lambda _:0)
    calls=[]
    def gh(method,path,body=None,**kwargs):
        calls.append((method,path,body))
        assert (method,path,body)==('POST',
            '/actions/workflows/linyuan-source-inventory.yml/dispatches',{'ref':'main'})
    monkeypatch.setattr(F,'gh',gh)
    monkeypatch.setattr(F,'source_inventory',lambda _:dict(inventory_fresh=False))
    return state,calls,now


@pytest.mark.parametrize('hour',[10,14,16,21])
def test_stale_experiment_slot_requests_once_without_scanning_or_mutating_stock(monkeypatch,hour):
    state,calls,now=setup(monkeypatch,hour)
    original=deepcopy(state)
    assert F.publish_handler()==dict(published=0,inventory_unavailable=1,
                                    inventory_refresh_requested=1)
    assert F.publish_handler()==dict(published=0,inventory_unavailable=1,
                                    inventory_refresh_requested=0)
    assert len(calls)==1 and state.pop('inventory_refresh_requested_at')==now
    assert state==original


def test_failed_refresh_does_not_consume_backoff_or_receipt(monkeypatch):
    state,_,_=setup(monkeypatch)
    original=deepcopy(state)
    def unavailable(*args,**kwargs):raise TimeoutError('verifier request unavailable')
    monkeypatch.setattr(F,'gh',unavailable)
    assert F.publish_handler()==dict(published=0,inventory_unavailable=1,
                                    inventory_refresh_requested=0)
    assert state==original


def test_existing_refresh_backoff_is_shared_with_admission(monkeypatch):
    state,calls,now=setup(monkeypatch)
    state['inventory_refresh_requested_at']=now-600
    assert F.publish_handler()['inventory_refresh_requested']==0
    assert not calls
    monkeypatch.setattr(F.time,'time',lambda:now+301)
    assert F.request_inventory_refresh(state)
    assert len(calls)==1


@pytest.mark.parametrize('hour,day,forced,batch',[
    (14,'2026-11-17',False,None),(14,'2026-10-23',True,None),
    (14,'2026-10-23',True,'older-real-treatment')])
def test_other_publication_paths_do_not_add_an_inventory_gate(monkeypatch,hour,day,forced,batch):
    state,calls,_=setup(monkeypatch,hour,day)
    state['dispatched']=[] if not batch else state['dispatched']
    if batch:
        # Stop the explicit path at the unchanged rights/part discovery scan.
        def discovery(method,path,*args,**kwargs):
            assert method=='GET'
            return dict(workflow_runs=[])
        monkeypatch.setattr(F,'gh',discovery)
        monkeypatch.setattr(F,'_has_unpublished_part',lambda *args:False)
    result=F.publish_handler(dict(force_publish=forced,batch_slug=batch))
    assert result==dict(published=0)
    assert not calls and 'inventory_refresh_requested_at' not in state


def test_daily_cap_does_not_request_refresh(monkeypatch):
    state,calls,_=setup(monkeypatch,count=4)
    assert F.publish_handler()==dict(published=0)
    assert not calls


@pytest.mark.parametrize('hour',[10,14,16,21])
def test_fresh_inventory_keeps_the_existing_publication_path(monkeypatch,hour):
    state,calls,_=setup(monkeypatch,hour)
    state['dispatched']=[]
    monkeypatch.setattr(F,'source_inventory',lambda _:dict(inventory_fresh=True))
    assert F.publish_handler()==dict(published=0)
    assert not calls
