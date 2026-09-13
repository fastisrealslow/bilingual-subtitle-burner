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
