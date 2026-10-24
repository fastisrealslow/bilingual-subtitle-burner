"""A real P1/search ID collision must preserve exact metadata and denials."""
import json
from pathlib import Path
import sqlite3
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import monitor_v2 as M

BVID='BV12b421E7vz'


def row(extra):
    return dict(id='bilibili_search:'+BVID,source='bilibili_search',
        title='2024林园北大最新演讲（完整版）',
        url='https://www.bilibili.com/video/'+BVID,author='基金经理说',publish_time='',
        extra=json.dumps(dict(bvid=BVID,**extra)))


def stored():
    with sqlite3.connect(M.DB_PATH) as c:
        return json.loads(c.execute('SELECT extra FROM items WHERE id=?',
            ('bilibili_search:'+BVID,)).fetchone()[0])


@pytest.mark.parametrize('search_last',[False,True])
def test_exact_default_page_survives_both_scan_orders(tmp_path,monkeypatch,search_last):
    monkeypatch.setattr(M,'DB_PATH',tmp_path/'catalog.db');M.init_db()
    # Actual parent search reports 7212s; the independently downloaded P1 is
    # 1499.93s and has CID 1652126281. Metadata is not a media approval.
    search=row(dict(duration=7212,view_count=79177,metadata_status='known_duration'))
    exact=row(dict(page=1,cid=1652126281,duration=1499.93,
        collection_title='2024林园北大最新演讲（完整版）',metadata_status='known_duration'))
    rows=[exact,search] if search_last else [search,exact]
    assert len(M.upsert_items(rows))==1
    result=stored()
    assert (result['page'],result['cid'],result['duration'])==(1,1652126281,1499.93)
    assert not result.get('media_verified') and not result.get('editorial_approved')
    assert 'page' not in json.loads(search['extra'])  # Caller input unchanged.


def test_new_denial_still_wins_and_fresh_exact_page_can_change(tmp_path,monkeypatch):
    monkeypatch.setattr(M,'DB_PATH',tmp_path/'catalog.db');M.init_db()
    M.upsert_items([row(dict(page=1,cid=1652126281,duration=1499.93,metadata_status='known_duration'))])
    M.upsert_items([row(dict(duration=7212,no_reprint=1,metadata_status='known_duration'))])
    result=stored()
    assert result['duration']==1499.93 and result['no_reprint']==1
    assert result['direct_dispatch'] is False and result['source_role']=='catalog_only'
    M.upsert_items([row(dict(page=1,cid=1652126282,duration=1502,metadata_status='known_duration',no_reprint=0))])
    result=stored()
    assert result['cid']==1652126282 and result['duration']==1502
    assert result['no_reprint']==1 and result['direct_dispatch'] is False


@pytest.mark.parametrize('change',[
    dict(page=2),dict(page='1'),dict(cid=0),dict(cid=True),dict(duration=0),
    dict(duration=float('nan')),dict(duration=True),dict(metadata_status='unverified'),
    dict(bvid='BV16PWPeFEq7'),
])
def test_other_page_unverified_or_mismatched_identity_cannot_supply_p1(change):
    prior=dict(bvid=BVID,page=1,cid=1652126281,duration=1499.93,metadata_status='known_duration')
    prior.update(change)
    incoming=row(dict(duration=7212))['extra']
    assert M.retain_exact_page_metadata(incoming,json.dumps(prior),'bilibili_search:'+BVID)==incoming


def test_page_metadata_never_borrows_an_accepted_media_flag_or_lowers_a_long_page():
    prior=dict(bvid=BVID,page=1,cid=42,duration=6000,metadata_status='known_duration',
               media_verified=True,editorial_approved=True)
    result=json.loads(M.retain_exact_page_metadata(row(dict(duration=1500))['extra'],
        json.dumps(prior),'bilibili_search:'+BVID))
    assert result['duration']==6000
    assert 'media_verified' not in result and 'editorial_approved' not in result
