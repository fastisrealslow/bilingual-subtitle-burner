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


def exchange_seed(base):
    row=seed();row.update(title='林园重庆交流视频 全网首发',source_role='catalog_only',
        direct_dispatch=False,target_speaker_title_evidence=False)
    (base/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[row])))
    return row


def exchange_verified(lead):
    return {**verify(lead),'title':'林园重庆交流视频 全网首发','target_speaker_title_evidence':True}


def test_legacy_exchange_format_needs_fresh_exact_metadata_and_native_hd_before_promotion(base):
    before=exchange_seed(base);history=(base/'.automation/fc_state.json').read_bytes()
    assert items(before)==[] and R.title_format_only_hold(before)
    result=R.run(base,verify=exchange_verified,streams=streams)
    after=json.loads((base/'bilibili_seeds.json').read_text())['seeds'][0]
    assert result['refreshed_count']==1 and len(items(after))==1
    assert after['direct_dispatch'] is True and after['source_role']=='mother_candidate'
    assert after['media_verified'] is after['editorial_approved'] is False
    assert result['accepted_mp4_count']==0 and (base/'.automation/fc_state.json').read_bytes()==history


def test_low_resolution_exchange_stays_in_research_catalogue(base):
    before=exchange_seed(base)
    result=R.run(base,verify=exchange_verified,streams=lambda *a:dict(width=640,height=360))
    after=json.loads((base/'bilibili_seeds.json').read_text())['seeds'][0]
    assert result['refreshed_count']==0 and after==before and items(after)==[]


@pytest.mark.parametrize('changes',[dict(no_reprint=1),dict(reuse_hold_reason='permission_pending'),
    dict(target_speaker_title_evidence=True),dict(title='林园的投资故事'),
    dict(title='林园好友谈重庆交流视频'),dict(title='林园重庆交流视频解读')])
def test_other_research_or_explicit_holds_do_not_get_the_format_exception(base,changes):
    row=exchange_seed(base);row.update(changes)
    (base/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[row])))
    assert R.run(base,verify=lambda *a:pytest.fail('held material rechecked'),streams=streams)['checked_count']==0


def test_exchange_format_cannot_override_another_seed_denial(base):
    row=exchange_seed(base)
    (base/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[row,{**row,'no_reprint':1}])))
    assert R.run(base,verify=lambda *a:pytest.fail('denied material rechecked'),streams=streams)['checked_count']==0


def test_exchange_format_cannot_override_the_verified_page_restriction_registry(base):
    row=exchange_seed(base);row.update(bvid='BV1RJ411Q7Ki',url='https://www.bilibili.com/video/BV1RJ411Q7Ki')
    (base/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[row])))
    assert R.run(base,verify=lambda *a:pytest.fail('known banned page rechecked'),streams=streams)['checked_count']==0


def test_legacy_missing_dimensions_uses_bounded_header_without_approving_any_media(base,monkeypatch):
    prepare(base)
    import ci_fetch_bilibili as B,native_header_probe as N
    monkeypatch.setattr(B,'opener',lambda:object())
    monkeypatch.setattr(B,'playurl_for_page',lambda *a:dict(video=['https://cdn.bilivideo.com/source.flv'],height=0))
    proof=dict(width=1280,height=720,bytes_read=1024,media_approved=False,method='exact_stream_prefix_ffprobe_v1')
    calls=[]
    monkeypatch.setattr(N,'probe',lambda *a:calls.append(1) or proof)
    result=R.run(base,verify=verify)
    row=json.loads((base/'bilibili_seeds.json').read_text())['seeds'][0]
    assert calls==[1] and result['refreshed_count']==1 and result['accepted_mp4_count']==0
    assert row['long_source_preflight']['pages'][0]['native_header_probe']==proof
    assert row['media_verified'] is row['editorial_approved'] is False


def test_declared_native_stream_dimensions_do_not_spend_a_header_request(base,monkeypatch):
    prepare(base)
    import ci_fetch_bilibili as B,native_header_probe as N
    monkeypatch.setattr(B,'opener',lambda:object())
    monkeypatch.setattr(B,'playurl_for_page',lambda *a:dict(video=['https://cdn.bilivideo.com/source.flv'],width=1280,height=720))
    monkeypatch.setattr(N,'probe',lambda *a:pytest.fail('unneeded media prefix'))
    assert R.run(base,verify=verify)['refreshed_count']==1


@pytest.mark.parametrize('history',[{},dict(dispatched=None,published={}),dict(dispatched=[],published=None)])
def test_unknown_producer_history_cannot_be_treated_as_no_attempts(base,history):
    prepare(base);(base/'.automation/fc_state.json').write_text(json.dumps(history))
    result=R.run(base,verify=lambda *a:pytest.fail('unknown production history'),streams=streams)
    assert result['checked_count']==0 and result['reason']=='source_attempt_history_unknown'


@pytest.mark.parametrize('book',[{},dict(attempts=None,checks={}),dict(attempts=[{}],checks={}),
    dict(attempts=[dict(bvid='BV0000000001',ts=9999999999,date='2026-10-28')],checks={})])
def test_unknown_budget_cannot_reset_daily_refresh_attempts(base,book):
    prepare(base);(base/'.automation/seed_growth_scan.json').write_text(json.dumps(dict(known_long_refresh=book)))
    result=R.run(base,verify=lambda *a:pytest.fail('unknown budget'),streams=streams)
    assert result['checked_count']==0 and result['reason']=='refresh_budget_unknown'
