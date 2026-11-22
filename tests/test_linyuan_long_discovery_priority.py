"""Actual T shortage gets long-source discovery before the 5000 seed target."""
import json
import pytest
from test_linyuan_seed_replenishment import base,verified
import seed_growth as G


def lead(n,duration):return dict(bvid=f'BV{n:010d}',catalog_duration=duration)


def test_separate_cursors_reserve_long_checks_without_increasing_total_or_starving_short(base):
    path=base/'.automation/seed_growth_scan.json'
    path.write_text(json.dumps(dict(parents=dict(cursor=20,seed_count=24))))
    rows=[lead(i,1800 if i<7 else 80) for i in range(1,25)]
    selected=list(G.verification_batch(rows,path,8,True))
    assert len(selected)==8 and sum(r['catalog_duration']>=1200 for r in selected)==6
    scan=json.loads(path.read_text())
    assert scan['parents']==dict(cursor=20,seed_count=24)
    assert 'long_parents' in scan and 'other_parents' in scan


def test_shrinking_short_queue_cannot_skip_new_long_sources(base):
    path=base/'.automation/seed_growth_scan.json'
    list(G.verification_batch([lead(1,80),lead(2,80)],path,2,True))
    selected=list(G.verification_batch([lead(3,1800),lead(4,1800),lead(2,80)],path,3,True))
    assert [r['bvid'] for r in selected][:2]==['BV0000000003','BV0000000004']


def test_below_target_keeps_long_priority_and_breadth_with_original_six_page_budget(base,monkeypatch):
    queries=[]
    class Source:
        def __init__(self,config,state):assert config['pages']==2
        def _fetch_via_api(self,query):queries.append(query);return []
    monkeypatch.setattr(G.monitor,'BilibiliSearchSource',Source)
    report=G.run(base,target=5000,limit=8,verify=verified,replenish_long=True)
    assert report['long_discovery_priority'] and len(queries)==3
    assert sum(q in G.LONG_QUERIES for q in queries)==2
    assert queries[2] in G.QUERIES and queries[2] not in G.LONG_QUERIES
    assert report['search_query_policy']=='two_long_one_breadth'
    assert sum(len(p) for p in report['search_pages'].values())==6
    assert report['accepted_mp4_count']==0 and report['production_dispatch'] is False


def test_no_shortage_keeps_original_discovery_rotation(base,monkeypatch):
    monkeypatch.setattr(G,'long_replenishment_budget',lambda _:0)
    report=G.run(base,target=5000,limit=4,search=False,verify=verified,replenish_long=True)
    assert report['long_discovery_priority'] is False


def test_target_reached_still_obeys_original_four_per_pass_eight_per_day_limit(base):
    first=G.run(base,target=1,search=False,verify=verified,replenish_long=True)
    second=G.run(base,target=1,search=False,verify=verified,replenish_long=True)
    third=G.run(base,target=1,search=False,verify=verified,replenish_long=True)
    assert [r['added_count'] for r in (first,second,third)]==[4,4,0]


@pytest.mark.parametrize('status',[403,412,429])
def test_long_shortage_provider_denial_never_proceeds_to_breadth_or_verification(base,monkeypatch,status):
    from urllib.error import HTTPError
    queries=[]
    class Source:
        def __init__(self,config,state):assert config['pages']==2
        def _fetch_via_api(self,query):
            queries.append(query)
            raise HTTPError('https://api.bilibili.com/',status,'restricted',{},None)
    monkeypatch.setattr(G.monitor,'BilibiliSearchSource',Source)
    report=G.run(base,target=10000,limit=8,replenish_long=True,
        verify=lambda _:pytest.fail('provider cooldown bypass'))
    assert len(queries)==1 and report['verification_paused']['http_status']==status
    assert report['added_count']==0 and report['accepted_mp4_count']==0
