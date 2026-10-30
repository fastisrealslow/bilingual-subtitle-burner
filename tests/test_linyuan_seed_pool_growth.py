"""Actual metadata, provider independence and video-level deduplication."""
import json
from pathlib import Path
import sqlite3
import sys
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import seed_pool_growth as G

MID = '5355011066565501'


@pytest.fixture
def base(tmp_path, monkeypatch):
    for kind, key in [('douyin','urls'), ('weibo','urls'), ('haokan','vids'), ('netease','vcodes'), ('yicai','ids')]:
        (tmp_path / (kind + '_seeds.json')).write_text(json.dumps({key: []}))
    (tmp_path / 'monitor_v2_config.json').write_text('[]')
    (tmp_path / 'up_videos.json').write_text('[]')
    (tmp_path / '.automation').mkdir()
    with sqlite3.connect(tmp_path / 'monitor_v2.db') as c:
        c.execute('CREATE TABLE items (id TEXT, source TEXT, title TEXT, author TEXT, extra TEXT)')
    monkeypatch.setattr(G.time, 'sleep', lambda _: None)
    return tmp_path


def lead(base, mid=MID, title='林园完整访谈：我的投资经历', **extra):
    with sqlite3.connect(base / 'monitor_v2.db') as c:
        c.execute('INSERT INTO items VALUES (?,?,?,?,?)', (mid, 'weibo_search', title, '实际发布者',
            json.dumps(dict(mid=mid, duration=1800, has_video=True, **extra))))


def status(mid=MID, **changes):
    return dict(mid=mid, text_raw='林园完整访谈：我的投资经历', user=dict(screen_name='实际发布者'),
        page_info=dict(object_id='1034:5355011066565501', media_info=dict(duration=1800,
            stream_url_hd='https://cdn.example.com/video123.mp4?Expires=SECRET')), **changes)


def session(rows):
    def request(url, headers):
        mid=url.rsplit('=', 1)[-1]
        value=rows[mid]
        if isinstance(value, Exception): raise value
        return json.dumps(value)
    return lambda: (request, 'not-persisted-token')


def test_provider_restriction_does_not_stop_an_independent_provider(base, monkeypatch):
    lead(base)
    monkeypatch.setattr(G.bilibili, 'run', lambda *a, **kw: dict(added_count=0,
        verification_paused=dict(reason='provider_restricted', http_status=412)))
    original=G.grow_weibo
    monkeypatch.setattr(G, 'grow_weibo', lambda *a, **kw: original(*a, **kw, session=session({MID:status()})))
    report=G.run(base)
    assert report['target']==5000 and report['after']['total']==1 and report['added_count']==1
    assert report['providers']['bilibili']['verification_paused']['http_status']==412
    assert report['accepted_mp4_count']==report['rights_approved_count']==0
    assert report['production_dispatch'] is False
    saved=(base/'weibo_seeds.json').read_text()
    assert 'SECRET' not in saved and 'not-persisted-token' not in saved
    row=json.loads(saved)['verified_sources'][0]
    assert row['source_parent_id']=='weibo:1034:5355011066565501'
    assert row['media_verified'] is row['editorial_approved'] is False


def test_reposted_video_and_expiring_url_changes_do_not_inflate_seed_count(base):
    second='5355011066565502'
    lead(base);lead(base,second)
    rows={MID:status(), second:{**status(), 'mid':second}}
    rows[second]['page_info']['media_info']['stream_url_hd']='https://cdn.example.com/video123.mp4?Expires=DIFFERENT'
    r=G.grow_weibo(base,session=session(rows))
    assert r['added_count']==1 and r['duplicate_video_count']==1


def test_existing_video_path_dedup_does_not_depend_on_saved_new_identity_records(base):
    old='5355011066565500'
    (base/'weibo_seeds.json').write_text(json.dumps(dict(urls=['https://m.weibo.cn/detail/'+old])))
    lead(base,old,video_url='https://cdn.example.com/video123.mp4?Expires=OLD')
    lead(base)
    assert G.grow_weibo(base,session=session({MID:status()}))['added_count']==0


@pytest.mark.parametrize('change',[
    dict(retweeted_status=dict(mid='5355011066565599')),
    dict(text_raw='其他人谈投资，像下一个林园'),
    dict(mid='5355011066565599'),
    dict(user={}),
    dict(page_info={}),
])
def test_unknown_identity_reposts_or_no_video_do_not_enter_catalogue(base,change):
    lead(base)
    row=status();row.update(change)
    r=G.grow_weibo(base,session=session({MID:row}))
    assert r['added_count']==0 and G.census(base)['total']==0


def test_pure_forwarding_mention_never_earns_an_exact_lookup(base):
    lead(base,title='转发微博//@别人：林园完整访谈')
    assert G.weibo_leads(base)==[]


def test_restricted_programme_does_not_gain_a_new_production_route(base):
    lead(base,title='10月15日林园：看好AI大时代，但我不会投个股')
    assert G.weibo_leads(base)==[]
    row=status();row['text_raw']='2026.10.15林园完整访谈'
    with pytest.raises(ValueError):G.verify_weibo(dict(mid=MID),session({MID:row})()[0],'')
    assert not G.held_programme_hint('2024.10.15林园完整访谈')


def test_provider_http_restriction_persists_cooldown_and_stops_requests(base):
    lead(base)
    error=HTTPError('https://weibo.com/', 429, 'restricted', {}, None)
    r=G.grow_weibo(base,session=session({MID:error}))
    assert r['added_count']==0 and r['errors'][0]['http_status']==429
    def forbidden():raise AssertionError('Provider cooldown must be honored')
    assert G.grow_weibo(base,session=forbidden)['paused_until']>G.time.time()


def test_no_discovery_requests_when_target_is_met_or_only_a_research_hold_exists(base):
    def forbidden():raise AssertionError('No network work')
    assert G.grow_weibo(base,target=0,session=forbidden)['added_count']==0
    lead(base,direct_dispatch=False)
    assert G.grow_weibo(base,session=forbidden)['discovery_leads']==0


def test_bounded_batch_checks_sixteen_independent_identities_not_source_jobs(base):
    rows={}
    for i in range(20):
        mid=str(int(MID)+i);lead(base,mid)
        row=status();row['mid']=mid
        row['page_info']['object_id']='1034:'+mid
        row['page_info']['media_info']['stream_url_hd']=f'https://cdn.example.com/{mid}.mp4'
        rows[mid]=row
    r=G.grow_weibo(base,limit=100,session=session(rows))
    assert r['added_count']==r['exact_checks']==16
    assert len(json.loads((base/'weibo_seeds.json').read_text())['urls'])==16


def test_rejected_metadata_is_deferred_but_a_changed_lead_can_be_rechecked(base):
    lead(base)
    row=status();row['page_info']={}
    assert G.grow_weibo(base,session=session({MID:row}))['exact_checks']==1
    def forbidden():raise AssertionError('Do not repeatedly request unchanged rejected metadata')
    report=G.grow_weibo(base,session=forbidden)
    assert report['deferred_checks']==1 and report['exact_checks']==0
    with sqlite3.connect(base/'monitor_v2.db') as c:
        c.execute('UPDATE items SET title=?',('林园完整访谈：新视频信息',))
    assert G.grow_weibo(base,session=session({MID:status()}))['added_count']==1


def test_duplicate_video_is_not_requested_again_in_the_next_batch(base):
    second=str(int(MID)+1);lead(base);lead(base,second)
    report=G.grow_weibo(base,session=session({MID:status(),second:{**status(),'mid':second}}))
    assert report['added_count']==1 and report['duplicate_video_count']==1
    def forbidden():raise AssertionError('The same video identity is already known')
    report=G.grow_weibo(base,session=forbidden)
    assert report['exact_checks']==0 and report['deferred_checks']==1


def test_target_and_monotonic_deadline_still_bound_the_larger_batch(base,monkeypatch):
    for i in range(3):lead(base,str(int(MID)+i))
    ticks=iter([0,91])
    monkeypatch.setattr(G.time,'monotonic',lambda:next(ticks))
    def forbidden(url,headers):raise AssertionError('No request after the 90 second budget')
    assert G.grow_weibo(base,session=lambda:(forbidden,''))['exact_checks']==0
