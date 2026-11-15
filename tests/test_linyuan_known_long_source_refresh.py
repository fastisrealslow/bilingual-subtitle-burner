"""Expired unused long seeds must become real candidates without fake stock."""
from copy import deepcopy
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
from urllib.error import HTTPError
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import known_long_source_refresh as R
import seed_pool_growth as P
from test_linyuan_seed_replenishment import base
from verified_seed_snapshot import items


def seed(n=1):
    return dict(bvid=f'BV{n:010d}',title='林园访谈：第一笔钱是怎么赚的',observed_author='访谈发布者',
        owner_mid=123,url=f'https://www.bilibili.com/video/BV{n:010d}',
        published_at='2024-01-01T00:00:00+00:00',checked_at='2026-10-10T00:00:00+00:00',
        metadata_provenance='bilibili_view_exact_id_author_pages',no_reprint=0,
        source_role='mother_candidate',direct_dispatch=True,target_speaker_title_evidence=True,
        media_verified=False,editorial_approved=False,
        pages=[dict(page=1,cid=n+100,duration=1800,title='林园早年投资经历')])


def prepare(base,number=1):
    rows=[seed(n) for n in range(1,number+1)]
    (base/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=rows)))
    return rows


def verify(lead):
    n=int(lead['bvid'][2:]);r=seed(n)
    return dict(r,status='verified_metadata',checked_at=datetime.fromtimestamp(R.time.time(),timezone.utc).isoformat())


def streams(bvid,page):return dict(width=1280,height=720)


def test_below_global_target_refreshes_existing_hd_parent_without_adding_seed_or_stock(base):
    before=prepare(base);state=(base/'.automation/fc_state.json').read_bytes()
    assert items(before[0])==[]
    report=R.run(base,verify=verify,streams=streams)
    after=json.loads((base/'bilibili_seeds.json').read_text())['seeds']
    assert report['checked_count']==report['refreshed_count']==1
    assert len(after)==len(before)==1 and len(items(after[0]))==1
    assert after[0]['long_source_preflight']['pages'][0]['cid']==101
    assert after[0]['media_verified'] is after[0]['editorial_approved'] is False
    assert report['accepted_mp4_count']==0 and report['production_dispatch'] is False
    assert (base/'.automation/fc_state.json').read_bytes()==state


def test_actual_360p_is_not_refreshed_or_promoted_to_treatment_candidate(base):
    prepare(base);original=(base/'bilibili_seeds.json').read_bytes()
    report=R.run(base,verify=verify,streams=lambda *a:dict(width=640,height=360))
    assert report['refreshed_count']==0
    assert report['rows'][0]['status']=='native_scene_geometry_unavailable'
    assert (base/'bilibili_seeds.json').read_bytes()==original or json.loads(
        (base/'bilibili_seeds.json').read_text())==json.loads(original)


def test_missing_dimensions_are_unknown_and_never_a_proven_geometry_rejection(base):
    prepare(base)
    report=R.run(base,verify=verify,streams=lambda *a:dict(height=0))
    assert report['rows'][0]['status']=='native_dimensions_unknown'
    assert report['refreshed_count']==0
    scan=json.loads((base/'.automation/seed_growth_scan.json').read_text())
    assert scan['known_long_refresh']['checks'][seed()['bvid']]['next_check_at']==R.time.time()+1800


@pytest.mark.parametrize('failed',[True,False,None])
def test_any_prior_attempt_or_unknown_outcome_cannot_earn_a_new_retry(base,failed):
    prepare(base)
    (base/'.automation/fc_state.json').write_text(json.dumps(dict(dispatched=[dict(
        slug='prior',source_url=seed()['url']+'?p=1',failed=failed)],published={})))
    def forbidden(*a):raise AssertionError('Producer attempt may not be reset by metadata refresh')
    assert R.run(base,verify=forbidden,streams=forbidden)['checked_count']==0


@pytest.mark.parametrize('change',[dict(no_reprint=1),dict(source_role='catalog_only'),
    dict(direct_dispatch=False),dict(reuse_hold_reason='permission_pending')])
def test_existing_holds_are_never_reopened(base,change):
    rows=prepare(base);rows[0].update(change)
    (base/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=rows)))
    assert R.run(base,verify=lambda *a:pytest.fail('held seed rechecked'),streams=streams)['checked_count']==0


def test_new_no_reprint_denial_is_persisted_without_fetching_playurl(base):
    prepare(base)
    def denied(lead):return {**verify(lead),'no_reprint':1,'direct_dispatch':False,'source_role':'catalog_only'}
    report=R.run(base,verify=denied,streams=lambda *a:pytest.fail('rights-denied media request'))
    after=json.loads((base/'bilibili_seeds.json').read_text())['seeds'][0]
    assert report['denied_count']==1 and after['no_reprint']==1
    assert after['direct_dispatch'] is False and items(after)==[]


def test_four_checks_per_pass_eight_total_per_cst_day_even_when_metadata_fails(base):
    prepare(base,12);calls=[]
    def unavailable(lead):calls.append(lead['bvid']);return dict(status='not_verified',error='TimeoutError')
    assert R.run(base,verify=unavailable,streams=streams)['checked_count']==4
    assert R.run(base,verify=unavailable,streams=streams)['checked_count']==4
    assert R.run(base,verify=unavailable,streams=streams)['checked_count']==0
    assert len(calls)==len(set(calls))==8


def test_provider_restriction_stops_and_preserves_shared_cooldown(base):
    prepare(base,4);calls=[]
    def denied(lead):calls.append(lead);return dict(status='not_verified',error='HTTPError',http_status=412)
    report=R.run(base,verify=denied,streams=streams)
    assert len(calls)==report['checked_count']==1 and report['rows'][0]['provider_restricted']
    scan=json.loads((base/'.automation/seed_growth_scan.json').read_text())
    assert scan['verification_cooldown']['until']==R.time.time()+3600
    assert R.run(base,verify=denied,streams=streams)['checked_count']==0


def test_stream_restriction_does_not_try_another_identity_or_endpoint(base):
    prepare(base,4)
    def restricted(*a):raise HTTPError('https://api.bilibili.com/',403,'restricted',{},None)
    report=R.run(base,verify=verify,streams=restricted)
    assert report['checked_count']==1 and report['rows'][0]['provider_restricted']


def test_read_only_actual_probe_does_not_change_metadata_state_or_attempt_budgets(base):
    prepare(base);paths=[base/'bilibili_seeds.json',base/'.automation/fc_state.json',base/'.automation/seed_growth_scan.json']
    before={p:p.read_bytes() if p.exists() else None for p in paths}
    report=R.run(base,verify=verify,streams=streams,apply=False)
    assert report['applied'] is False and report['refreshed_count']==1
    assert {p:p.read_bytes() if p.exists() else None for p in paths}==before


@pytest.mark.parametrize('change',[dict(owner_mid=456),dict(bvid='BV9999999999'),
    dict(pages=[dict(page=1,cid=999,duration=1800)]),dict(pages=[dict(page=1,cid=101,duration=80)])])
def test_changed_identity_or_duration_cannot_refresh_old_parent(base,change):
    prepare(base)
    report=R.run(base,verify=lambda lead:{**verify(lead),**change},streams=streams)
    assert report['refreshed_count']==0 and items(json.loads((base/'bilibili_seeds.json').read_text())['seeds'][0])==[]


def test_pool_growth_shares_original_metadata_budget_with_refresh(base,monkeypatch):
    prepare(base);limits=[]
    monkeypatch.setattr(R,'run',lambda *a,**kw:dict(checked_count=4,refreshed_count=2))
    monkeypatch.setattr(P.bilibili,'run',lambda *a,**kw:limits.append(kw['limit']) or dict(added_count=0))
    monkeypatch.setattr(P,'grow_weibo',lambda *a,**kw:dict(added_count=0))
    report=P.run(base,target=5000,limit=48)
    assert limits==[44] and report['added_count']==0 and report['accepted_mp4_count']==0
