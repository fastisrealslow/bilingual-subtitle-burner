"""Replay the Nov 18 exact-lookup budget, without network or media approval."""
from collections import Counter
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import seed_campaign as C
import seed_growth as G

FIXTURE=Path(__file__).parent/'fixtures/linyuan_seed_lookup_waste_20261118.json'


def setup_queue(tmp_path,monkeypatch,leads):
    monkeypatch.setattr(G,'census',lambda _:dict(total=0))
    monkeypatch.setattr(G,'existing_roots',lambda _:set())
    monkeypatch.setattr(G,'catalog_leads',lambda _:[])
    monkeypatch.setattr(G.time,'sleep',lambda _:None)
    (tmp_path/'bilibili_seeds.json').write_text('{"seeds":[]}')
    G.save_pending_leads(tmp_path/'.automation/seed_growth_leads.json',
                         {r['bvid']:r for r in leads})


def test_actual_32_deferred_lookups_keep_evidence_but_not_waste_next_request(tmp_path,monkeypatch):
    # conftest keeps historical fixtures on legacy120; this live snapshot
    # came from reference_v1, including two legitimate short-source lookups.
    import source_catalogue_duration as duration
    from editorial_policy import DURATION_POLICIES
    monkeypatch.setattr(duration,'MIN_SECONDS',DURATION_POLICIES['reference_v1'])
    fixture=json.loads(FIXTURE.read_text());leads=fixture['candidates']
    assert len(leads)==32 and fixture['provenance']['snapshot_commit']=='7e9a642'
    assert Counter(G.discovery_exclusion(r) for r in leads)=={
        'ineligible_author':28,'unrelated_or_non_live_title':2,None:2}
    setup_queue(tmp_path,monkeypatch,leads)
    calls=[]
    def verify(lead):
        calls.append(lead)
        return dict(lead,status='not_verified',error='TimeoutError')
    report=G.run(tmp_path,target=5000,limit=1,search=False,verify=verify)
    assert len(calls)==1 and G.discovery_exclusion(calls[0]) is None,report
    assert report['preverification_excluded']=={
        'ineligible_author':28,'unrelated_or_non_live_title':2}
    assert report['pending_eligible_lead_count']==2
    assert report['pending_lead_count']==32  # Not deleted, hidden or counted as seeds.
    assert report['added_count']==report['accepted_mp4_count']==0
    assert report['rights_approved_count']==0 and report['production_dispatch'] is False
    saved=G.pending_leads(tmp_path/'.automation/seed_growth_leads.json')
    assert len(saved)==32 and all(r['bvid'] in saved for r in leads)
    assert json.loads((tmp_path/'bilibili_seeds.json').read_text())=={'seeds':[]}


@pytest.mark.parametrize('lead',[{},dict(catalog_author=''),dict(catalog_title=''),
    dict(catalog_author=None),dict(catalog_author='真实作者',catalog_title='林园完整访谈')])
def test_missing_discovery_metadata_still_gets_exact_lookup(lead):
    assert G.discovery_exclusion(lead) is None


@pytest.mark.parametrize('author',['账号已注销','已注销','园园滚雪球',' 园来滚雪球 '])
def test_known_ineligible_uploader_does_not_spend_a_lookup(author):
    assert G.discovery_exclusion(dict(catalog_author=author))=='ineligible_author'


def test_all_ineligible_queue_uses_no_network_and_keeps_unchanged_budget_cursor(tmp_path,monkeypatch):
    leads=[dict(bvid='BV1NpBFYkEKx',catalog_duration=1800,catalog_author='账号已注销')]
    setup_queue(tmp_path,monkeypatch,leads)
    scan=tmp_path/'.automation/seed_growth_scan.json'
    saved=dict(parents=dict(cursor=7,seed_count=11),verification_cooldown=dict(until=0))
    scan.write_text(json.dumps(saved))
    def forbidden(*a,**k):pytest.fail('Known-ineligible lead spent an exact API lookup')
    report=G.run(tmp_path,target=5000,search=False,verify=forbidden)
    assert report['verification']=={} and report['pending_eligible_lead_count']==0
    assert json.loads(scan.read_text())==saved
    assert G.pending_leads(tmp_path/'.automation/seed_growth_leads.json')=={leads[0]['bvid']:leads[0]}


def test_undated_catalogue_snapshot_cannot_clear_a_pending_denial_or_reset_retry(tmp_path,monkeypatch):
    bvid='BV1NpBFYkEKx'
    stale=dict(bvid=bvid,catalog_author='账号已注销',catalog_title='林园完整访谈',
               catalog_duration=1800,verification_retry_after=G.time.time()+3600)
    setup_queue(tmp_path,monkeypatch,[stale])
    cached={**stale,'catalog_author':'实际作者'};cached.pop('verification_retry_after')
    monkeypatch.setattr(G,'catalog_leads',lambda _:[cached])
    def forbidden(*a):pytest.fail('Fresh discovery must not reset the exact-lookup cooldown')
    report=G.run(tmp_path,target=5000,search=False,verify=forbidden)
    saved=G.pending_leads(tmp_path/'.automation/seed_growth_leads.json')[bvid]
    assert saved['catalog_author']=='账号已注销'
    assert saved['verification_retry_after']==stale['verification_retry_after']
    assert report['pending_eligible_lead_count']==0 and report['verification']=={}


def test_search_drops_new_ineligible_clues_but_retains_old_and_can_rediscover(tmp_path,monkeypatch):
    old='BV1NpBFYkEKx';new='BV1LQVc6eEFu'
    setup_queue(tmp_path,monkeypatch,[dict(bvid=old,catalog_duration=1800,catalog_author='账号已注销')])
    author=['账号已注销']
    monkeypatch.setattr(G.monitor.BilibiliSearchSource,'_fetch_via_api',lambda *a:[dict(
        bvid=b,title='林园完整访谈',duration=1800,up=author[0]) for b in (old,new)])
    calls=[]
    def unavailable(lead):
        calls.append(lead['bvid']);return dict(lead,status='not_verified',error='TimeoutError')
    first=G.run(tmp_path,target=5000,limit=2,verify=unavailable)
    assert calls==[] and first['pending_lead_count']==1
    assert first['discovery_excluded']['ineligible_author']==6  # Same three-query bound.
    author[0]='实际作者'
    second=G.run(tmp_path,target=5000,limit=2,verify=unavailable)
    assert set(calls)=={old,new} and len(calls)==2
    assert second['preverification_excluded']=={} and second['added_count']==0


@pytest.mark.parametrize('data,reason',[
    (dict(bvid='BV1wrong0000'),'exact_metadata_unavailable'),
    (dict(title='华林园酒店'),'unrelated_or_non_live_title'),
    (dict(owner=dict(name='账号已注销',mid=123)),'ineligible_publisher'),
    (dict(pages=[]),'no_eligible_video_pages')])
def test_exact_metadata_rejections_get_static_reason_codes_not_approvals(data,reason):
    metadata=dict(bvid='BV1NpBFYkEKx',title='林园完整访谈',owner=dict(name='实际作者',mid=123),
                  pages=[dict(page=1,cid=100,duration=1800,part='访谈')])
    metadata.update(data)
    row=C.verify_seed(dict(bvid='BV1NpBFYkEKx'),lambda *a,**k:json.dumps(dict(code=0,data=metadata)))
    assert row['status']=='not_verified' and row['rejection_reason']==reason
    assert row['error']=='ValueError' and row['api_code']==0
    assert 'media_verified' not in row and 'direct_dispatch' not in row


def test_arbitrary_exception_text_is_not_exported():
    def unavailable(*a,**k):raise ValueError('secret=private-token signed-url=private')
    row=C.verify_seed(dict(bvid='BV1NpBFYkEKx'),unavailable)
    assert row==dict(bvid='BV1NpBFYkEKx',status='not_verified',error='ValueError')


def test_report_aggregates_safe_rejection_reasons(tmp_path,monkeypatch):
    setup_queue(tmp_path,monkeypatch,[dict(bvid='BV1NpBFYkEKx',catalog_duration=1800)])
    report=G.run(tmp_path,target=5000,search=False,verify=lambda r:dict(
        r,status='not_verified',error='ValueError',api_code=0,rejection_reason='ineligible_publisher'))
    assert report['verification_rejections']=={'ineligible_publisher':1}
    assert report['added_count']==0 and report['production_dispatch'] is False
