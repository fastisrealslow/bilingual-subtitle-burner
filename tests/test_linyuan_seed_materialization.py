"""Verified metadata reaches candidates promptly without granting media approval."""
from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import monitor_v2 as monitor
import verified_seed_snapshot as snapshot
from materialize_verified_seeds import materialize

NOW=1794027600
BVID='BV1NpBFYkEKx'


def seed():
    return dict(bvid=BVID,title='林园完整访谈',observed_author='真实采访方',owner_mid=123,
        metadata_provenance='bilibili_view_exact_id_author_pages',source_role='mother_candidate',
        direct_dispatch=True,target_speaker_title_evidence=True,no_reprint=0,
        media_verified=False,editorial_approved=False,url='https://www.bilibili.com/video/'+BVID,
        checked_at=datetime.fromtimestamp(NOW,timezone.utc).isoformat(),
        pages=[dict(page=1,cid=12345,duration=1800,title='完整访谈')])


@pytest.fixture
def catalogue(tmp_path,monkeypatch):
    monkeypatch.setattr(monitor,'DB_PATH',tmp_path/'monitor_v2.db');monitor.init_db()
    monkeypatch.setattr(snapshot.time,'time',lambda:NOW)
    monkeypatch.setattr(monitor,'http_get',lambda *a,**kw:pytest.fail('No additional provider requests'))
    (tmp_path/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[seed()])))
    return tmp_path


def test_new_seed_enters_exact_page_catalogue_before_next_network_scan(catalogue):
    result=materialize(catalogue)
    assert result['new_pages']==1 and result['materialized_parents']==1
    assert result['accepted_mp4_count']==0 and not result['production_dispatch']
    with sqlite3.connect(monitor.DB_PATH) as connection:
        key,extra=connection.execute('SELECT id,extra FROM items').fetchone()
    extra=json.loads(extra)
    assert key=='bilibili_search:'+BVID and extra['cid']==12345 and extra['duration']==1800
    assert extra['direct_dispatch'] and not extra['media_verified'] and not extra['editorial_approved']
    assert materialize(catalogue)['upserted_pages']==0


def test_older_page_denial_survives_fresh_seed_metadata(catalogue):
    monitor.upsert_items([dict(id='bilibili_search:'+BVID+':p2',source='bilibili_collection',
        title='林园完整访谈',url=seed()['url']+'?p=2',author='真实采访方',publish_time='',
        extra=json.dumps(dict(bvid=BVID,no_reprint=1,direct_dispatch=False)))])
    materialize(catalogue)
    with sqlite3.connect(monitor.DB_PATH) as connection:
        rows=connection.execute('SELECT extra FROM items').fetchall()
    assert len(rows)==2 and all(json.loads(r[0])['direct_dispatch'] is False for r in rows)


@pytest.mark.parametrize('change',[dict(no_reprint=1),dict(checked_at='2026-10-01T00:00:00+00:00'),
    dict(diagnostic_only=True),dict(owner_mid=275211725),dict(direct_dispatch=False)])
def test_stale_restricted_reference_or_diagnostic_observation_does_not_enter(catalogue,change):
    data=seed();data.update(change)
    (catalogue/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=[data])))
    assert materialize(catalogue)['upserted_pages']==0


def test_unreadable_catalogue_is_not_recreated_as_empty(catalogue):
    monitor.DB_PATH.write_text('corrupt')
    with pytest.raises(sqlite3.DatabaseError):materialize(catalogue)
    assert monitor.DB_PATH.read_text()=='corrupt'
