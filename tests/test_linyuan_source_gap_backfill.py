import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_gap_backfill as gaps


def test_long_page_is_catalogued_but_not_dispatched():
    row=gaps.collection_item('BVseries',dict(page=5,cid=5,duration=7200,part='长访谈'),
        dict(owner=dict(name='上传者')))
    extra=json.loads(row['extra'])
    assert row['id']=='bilibili_search:BVseries:p5'
    assert extra['source_role']=='catalog_only' and extra['direct_dispatch'] is False


def test_first_page_reuses_search_identity_and_missing_author_stays_blocked():
    row=gaps.collection_item('BVseries',dict(page=1,cid=1,duration=890,part='第一集'),{})
    assert row['id']=='bilibili_search:BVseries'
    assert json.loads(row['extra'])['direct_dispatch'] is False


def test_parent_api_outage_reuses_known_uploader_from_another_page():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE items(id TEXT,author TEXT,publish_time TEXT,extra TEXT)')
    conn.executemany('INSERT INTO items VALUES(?,?,?,?)',[
        ('bilibili_search:BVseries','', '', '{}'),
        ('bilibili_search:BVseries:p5','上传者','2026-09-01','{"collection_title":"林园全网合集"}'),
        ('bilibili_search:BVother:p5','别的人','2026-09-01','{}')])
    parent=gaps.cached_collection_parent(conn,'BVseries')
    row=gaps.collection_item('BVseries',dict(page=2,cid=2,duration=1200,part='第二集'),parent)
    assert row['author']=='上传者' and json.loads(row['extra'])['direct_dispatch']
    conn.execute('INSERT INTO items VALUES(?,?,?,?)',('bilibili_search:BVseries:p6','矛盾账号','','{}'))
    import pytest
    with pytest.raises(ValueError,match='Conflicting'):gaps.cached_collection_parent(conn,'BVseries')
    conn.close()


def test_lineage_keeps_hypotheses_and_blocks_reference_reposts():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE items(id TEXT,url TEXT,extra TEXT)')
    conn.executemany('INSERT INTO items VALUES(?,?,?)',[
        ('search','https://www.bilibili.com/video/BVref','{"duration":130}'),
        ('official','https://official.example/video','{"duration":600}')])
    catalog=dict(families=[dict(id='event',official_urls=['https://official.example/video'],mirror_urls=[])],
        references=[dict(bvid='BVref',candidate_families=['event'])])
    gaps.apply_lineage(conn,catalog)
    result={i:json.loads(e) for i,e in conn.execute('SELECT id,extra FROM items')}
    assert result['search']['direct_dispatch'] is False
    assert result['search']['lineage_status']=='needs_media_match'
    assert result['official']['reference_match_status']=='needs_media_match'
    assert result['official']['duration']==600
    before=list(conn.execute('SELECT * FROM items'))
    gaps.apply_lineage(conn,catalog)
    assert list(conn.execute('SELECT * FROM items'))==before


def test_reference_fallback_requires_exact_id_and_author(monkeypatch):
    monkeypatch.setattr(gaps,'get_api',lambda _: (_ for _ in ()).throw(RuntimeError('unavailable')))
    monkeypatch.setattr(gaps.monitor.BilibiliSearchSource,'_fetch_via_api',lambda *_:[
        dict(bvid='BVref',up='园园滚雪球',title='林园原声',duration=29,pubdate=1788652800)])
    d=gaps.reference_metadata('BVref',dict(name='园园滚雪球',mid=1700344493))
    assert d['dur']==29 and d['metadata_provenance']=='bilibili_search_exact_id_author'
    import pytest
    with pytest.raises(ValueError):
        gaps.reference_metadata('BVdifferent',dict(name='园园滚雪球',mid=1700344493))


def test_visual_proof_reclassifies_old_query_tag_and_blocks_unreviewed_material():
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE items(id TEXT,url TEXT,extra TEXT)')
    url='https://m.weibo.cn/detail/5344215793927135'
    conn.execute('INSERT INTO items VALUES(?,?,?)',('old',url,'{"source_family":"phoenix_2026_09","direct_dispatch":true}'))
    catalog=json.loads((Path(__file__).resolve().parents[1]/'linyuan/source_lineage.json').read_text())
    gaps.apply_lineage(conn,catalog)
    extra=json.loads(conn.execute('SELECT extra FROM items').fetchone()[0])
    assert extra['source_family']=='cruise_2025_09_visual'
    assert extra['reference_match_status']=='not_phoenix_2026_09'
    assert extra['direct_dispatch'] is False and extra['source_role']=='catalog_only'


def test_real_primary_clip_catalogue_never_implicitly_grants_reuse():
    catalog=json.loads((Path(__file__).resolve().parents[1]/'linyuan/source_lineage.json').read_text())
    family=next(f for f in catalog['families'] if f['id']=='phoenix_2026_09')
    clip=family['primary_clip_catalog'][0]
    row=gaps.primary_clip_item(clip,family['id'])
    extra=json.loads(row['extra'])
    assert row['url']=='https://finance.ifeng.com/c/8wW9TEtieKg'
    assert extra['origin_role']=='official_publisher' and extra['has_video']
    assert extra['duration']==145.92 and extra['direct_dispatch'] is False
    assert extra['source_role']=='catalog_only'
    assert extra['primary_media_evidence']['media_integrity']=='passed'


def test_normal_configured_session_is_used_before_public_metadata_request(monkeypatch):
    import platform_collections
    calls=[]
    class Client:
        def __init__(self,raw):assert raw=='test session'
        def call(self,path,**kw):calls.append((path,kw));return dict(bvid='BVtest')
    monkeypatch.setenv('BILIBILI_COOKIES','test session')
    monkeypatch.setattr(platform_collections,'Client',Client)
    monkeypatch.setattr(gaps.monitor,'http_get',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('no anonymous fallback')))
    assert gaps.get_api('/x/web-interface/view?bvid=BVtest')['bvid']=='BVtest'
    assert calls==[('/x/web-interface/view',dict(public=True,params=dict(bvid='BVtest')))]


def test_known_catalogue_survives_live_api_failure_without_claiming_live_success(monkeypatch):
    monkeypatch.setattr(gaps,'get_api',lambda *a: (_ for _ in ()).throw(RuntimeError('blocked')))
    parent,status,issue=gaps.research_parent('BV11H5NzCEWY')
    assert status=='verified_catalog_snapshot' and issue['error']=='RuntimeError'
    assert len(parent['pages'])==60 and sum(p['duration'] for p in parent['pages'])==85640
    assert parent['rights']['no_reprint']==1
    import pytest
    with pytest.raises(RuntimeError):gaps.research_parent('BVunknown')


def test_offline_reconciliation_cannot_erase_failed_online_probe():
    previous=dict(checked_at=100,research_collections=[dict(bvid='BVknown',pages=60)],
        errors=[dict(stage='research_collection_live_refresh',error='HTTPError')])
    report=dict(checked_at=200,errors=[])
    gaps.preserve_online_audit(report,previous)
    assert report['errors']==previous['errors'] and report['errors_scope']=='last_online_probe'
    assert report['last_online_check']['checked_at']==100
    assert report['research_collections']==previous['research_collections']
    later=dict(checked_at=300,errors=[])
    gaps.preserve_online_audit(later,report)
    assert later['last_online_check']['checked_at']==100 and later['errors']==previous['errors']
