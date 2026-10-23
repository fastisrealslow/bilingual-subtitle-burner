"""Over-limit parent totals must not hide their actual, bounded native pages."""
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import monitor_v2 as M
import seed_campaign


def seeds(count=40):
    return [dict(bvid='BV'+str(i)) for i in range(count)]


def test_tail_additions_get_bounded_once_priority_without_resetting_old_cursor(tmp_path):
    rows=seeds();rows[-2]['metadata_refresh_priority']=True;rows[-1]['metadata_refresh_priority']=True
    state={'_seed_scan':{'bilibili_collection':dict(cursor=10,seed_count=38)}}
    path=tmp_path/'scan.json'
    source=M.BilibiliCollectionSource(dict(seed_batch_size=6,_seed_scan_path=str(path)),state)
    first=list(source.collection_seed_batch(rows))
    assert [r['bvid'] for r in first]==['BV38','BV39','BV10','BV11','BV12','BV13']
    assert state['_seed_scan']['bilibili_collection']['cursor']==14
    # Simulate a later job restoring the durable state. The old pool progresses.
    restored={'_seed_scan':json.loads(path.read_text())}
    source=M.BilibiliCollectionSource(dict(seed_batch_size=6,_seed_scan_path=str(path)),restored)
    assert [r['bvid'] for r in source.collection_seed_batch(rows)]==['BV14','BV15','BV16','BV17','BV18','BV19']


def test_priority_is_checkpointed_before_request_even_if_job_is_interrupted(tmp_path):
    rows=seeds(3);rows[-1]['metadata_refresh_priority']=True
    path=tmp_path/'scan.json';state={'_seed_scan':{'bilibili_collection':dict(cursor=1,seed_count=3)}}
    source=M.BilibiliCollectionSource(dict(seed_batch_size=1,_seed_scan_path=str(path)),state)
    iterator=source.collection_seed_batch(rows)
    assert next(iterator)['bvid']=='BV2';iterator.close()
    saved=json.loads(path.read_text());assert saved['bilibili_collection']['cursor']==1
    restored=M.BilibiliCollectionSource(dict(seed_batch_size=1),{'_seed_scan':saved})
    assert [r['bvid'] for r in restored.collection_seed_batch(rows)]==['BV1']


def test_many_priority_rows_never_starve_the_regular_pool_or_exceed_request_cap():
    rows=seeds();[r.update(metadata_refresh_priority=True) for r in rows[-10:]]
    state={};source=M.BilibiliCollectionSource(dict(seed_batch_size=6),state)
    got=list(source.collection_seed_batch(rows))
    assert len(got)==6 and [r['bvid'] for r in got[-2:]]==['BV0','BV1']
    assert len({r['bvid'] for r in got})==6


def test_native_pages_use_real_cids_and_durations_not_parent_total(monkeypatch):
    evidence=json.loads((ROOT/'linyuan/operations/native-long-page-metadata-20261023.json').read_text())
    parents=evidence['pages'];rows=[]
    for parent in parents:
        assert parent['duration']>5400 and parent['code']==0 and parent['rights']['no_reprint']==0
        monkeypatch.setattr(M,'http_get',lambda *a,_parent=parent,**kw:json.dumps(dict(code=0,data=_parent)))
        rows.extend(M.BilibiliCollectionSource(dict(seeds=[dict(bvid=parent['bvid'])]),{}).fetch(None))
    assert len(rows)==10  # Last 21-second Buffett bumper is not a source page.
    hd_long=[]
    for row in rows:
        extra=json.loads(row['extra']);parent=next(p for p in parents if p['bvid']==extra['bvid'])
        page=next(p for p in parent['pages'] if p['page']==extra['page'])
        assert extra['cid']==page['cid'] and extra['duration']==page['duration']
        assert 120<=extra['duration']<=5400
        assert ('?p='+str(page['page']) in row['url'])==(page['page']>1)
        if page['duration']>=1200 and min(page['dimension']['width'],page['dimension']['height'])>=540:
            hd_long.append(row['id'])
    assert len(hd_long)==4


def test_priority_does_not_clear_restrictions_or_change_experiment(monkeypatch):
    monkeypatch.setattr(M,'http_get',lambda *a,**kw:json.dumps(dict(code=0,data=dict(
        owner=dict(name='真实作者'),rights=dict(no_reprint=1),title='林园访谈',
        pages=[dict(page=1,cid=123,duration=1600,part='第一集')]))))
    rows=M.BilibiliCollectionSource(dict(seeds=[dict(bvid='BVtest',metadata_refresh_priority=True)]),{}).fetch(None)
    extra=json.loads(rows[0]['extra']);assert extra['no_reprint']==1 and extra['direct_dispatch'] is False
    import publishing_experiment as E
    assert E.EXPERIMENT_ID=='field-story-30d-20261018' and E.MIN_MOTHER_SECONDS==1200
    assert E.VERSION==2026101801 and E.SELECTION_REVISION==2026102201


def test_added_parents_are_only_two_new_seeds_not_ten_stock_clips():
    data=json.loads((ROOT/'linyuan/bilibili_seeds.json').read_text())['seeds']
    rows=[r for r in data if r.get('discovery')=='overlimit_parent_native_page_audit_20261023']
    assert len(rows)==2 and len({r['bvid'] for r in rows})==2
    assert all(r['media_verified'] is False and r['editorial_approved'] is False for r in rows)
    assert all(r['origin_role']=='repost_or_lead' and r['rights_status']=='not_determined' for r in rows)
    assert seed_campaign.census()['total']>=1002
