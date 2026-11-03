"""An index outage must not erase independently verified producer files."""
from pathlib import Path
import subprocess
import sys
import time

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_supply as S


def artifact(identity=7):
    return dict(id=identity,name='deliver-mother',expired=False,
        expires_at='2027-01-01T00:00:00Z',created_at='2026-11-01T00:00:00Z',
        workflow_run=dict(id=17))


def test_global_index_outage_uses_actual_exact_run_and_preserves_partial_production():
    calls=[]
    def api(path):
        calls.append(path)
        if path.startswith('actions/artifacts?'):
            raise subprocess.CalledProcessError(1,['gh'],stderr=b'gh: index unavailable (HTTP 403)')
        assert path=='actions/runs/17/artifacts'
        return dict(artifacts=[artifact()])
    found=S.find_deliveries({'mother':{}},api,{'mother':dict(id=17,status='completed',conclusion='failure')})
    assert found=={'mother':artifact()} and len(calls)==2


def test_cached_approval_needs_fresh_exact_identity_when_latest_run_has_no_delivery():
    calls=[]
    def api(path):
        calls.append(path)
        if path.startswith('actions/artifacts?'):raise TimeoutError('index unavailable')
        if path=='actions/runs/18/artifacts':return dict(artifacts=[])
        assert path=='actions/runs/17/artifacts'
        return dict(artifacts=[artifact()])
    prior=[dict(slug='mother',artifact_id=7,run_id=17,parts=[dict(status='verified')])]
    found=S.find_deliveries({'mother':{}},api,{'mother':dict(id=18,status='completed')},prior)
    assert found=={'mother':artifact()} and calls.count('actions/runs/17/artifacts')==1


@pytest.mark.parametrize('failure',['unavailable','missing','expired','wrong_name','wrong_id'])
def test_no_fresh_stock_on_unknown_missing_expired_or_different_files(failure):
    def api(path):
        if path.startswith('actions/artifacts?'):raise TimeoutError('index unavailable')
        if failure=='unavailable':raise PermissionError('exact run unavailable')
        row=artifact()
        if failure=='expired':row['expired']=True
        elif failure=='wrong_name':row['name']='deliver-other'
        elif failure=='wrong_id':row['id']=8
        return dict(artifacts=[] if failure=='missing' else [row])
    prior=[dict(slug='mother',artifact_id=7,run_id=17)]
    with pytest.raises((TimeoutError,PermissionError)):
        S.find_deliveries({'mother':{}},api,{},prior)


def test_no_global_failure_becomes_a_fresh_empty_inventory():
    def api(_):raise TimeoutError('index unavailable')
    with pytest.raises(TimeoutError):S.find_deliveries({},api,{})


def test_cached_identity_beyond_first_run_page_is_verified_without_global_index():
    def api(path):
        if path.startswith('actions/artifacts?'):raise TimeoutError('index unavailable')
        if path.endswith('page=2'):return dict(total_count=31,artifacts=[artifact()])
        return dict(total_count=31,artifacts=[dict(id=100+i,name='unrelated') for i in range(30)])
    prior=[dict(slug='mother',artifact_id=7,run_id=17)]
    assert S.find_deliveries({'mother':{}},api,{},prior)=={'mother':artifact()}


def test_incomplete_run_listing_is_not_proof_of_removal():
    def api(path):
        if path.startswith('actions/artifacts?'):raise TimeoutError('index unavailable')
        if 'page=2' in path:return dict(total_count=31,artifacts=[])
        return dict(total_count=31,artifacts=[dict(id=100+i,name='unrelated') for i in range(30)])
    with pytest.raises(RuntimeError,match='incomplete'):
        S.find_deliveries({'mother':{}},api,{},[dict(slug='mother',artifact_id=7,run_id=17)])
