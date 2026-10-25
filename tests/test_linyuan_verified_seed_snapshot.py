"""Fresh catalogue reuse never substitutes for rights or real media gates."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import verified_seed_snapshot as V
import monitor_v2 as M

NOW=datetime(2026,10,25,4,tzinfo=timezone.utc).timestamp()
BVID='BV1AbCdEfG12'


def seed():
    return dict(bvid=BVID,url='https://www.bilibili.com/video/'+BVID,
        title='林园完整访谈',observed_author='目录测试作者',owner_mid=123,
        checked_at=datetime.fromtimestamp(NOW-60,timezone.utc).isoformat(),
        published_at='2020-01-01T00:00:00+00:00',
        metadata_provenance='bilibili_view_exact_id_author_pages',
        source_role='mother_candidate',direct_dispatch=True,no_reprint=0,
        target_speaker_title_evidence=True,media_verified=False,editorial_approved=False,
        pages=[dict(page=1,cid=1234,duration=1800,title='原生第一集')])


def test_fresh_exact_metadata_preserves_page_identity_and_grants_no_media_approval():
    source=seed();before=deepcopy(source);rows=V.items(source,NOW)
    assert len(rows)==1 and rows[0]['id']=='bilibili_search:'+BVID
    extra=json.loads(rows[0]['extra'])
    assert extra['cid']==1234 and extra['duration']==1800
    assert extra['metadata_checked_at']==source['checked_at']
    assert not extra['media_verified'] and not extra['editorial_approved']
    assert extra['rights_status']=='not_determined' and source==before


@pytest.mark.parametrize('changes',[
    dict(checked_at=datetime.fromtimestamp(NOW-21601,timezone.utc).isoformat()),
    dict(checked_at=datetime.fromtimestamp(NOW+61,timezone.utc).isoformat()),
    dict(checked_at='2026-10-25T03:00:00'),dict(checked_at=None),
    dict(metadata_provenance='search_snippet'),dict(direct_dispatch=False),
    dict(source_role='catalog_only'),dict(no_reprint=1),dict(no_reprint=None),
    dict(no_reprint=False),dict(owner_mid=1700344493),dict(owner_mid=275211725),
    dict(owner_mid=True),dict(observed_author=''),dict(title='林园好友谈投资'),
    dict(media_verified=True),dict(editorial_approved=True),dict(diagnostic_only=True),
    dict(url='https://example.test/not-this-video'),
    dict(pages=[dict(page=1,cid=0,duration=1800)]),
])
def test_stale_weak_restricted_or_diagnostic_metadata_cannot_enter_cache_route(changes):
    source=seed();source.update(changes)
    assert V.items(source,NOW)==[]


def test_policy_denial_overrides_even_a_recent_no_reprint_zero_snapshot(monkeypatch):
    monkeypatch.setattr(V,'restriction',lambda *a:'known restriction')
    assert V.items(seed(),NOW)==[]


def test_collection_reuses_only_fresh_metadata_without_requests_and_retains_old_denials(tmp_path,monkeypatch):
    monkeypatch.setattr(V.time,'time',lambda:NOW)
    monkeypatch.setattr(M,'http_get',lambda *a,**k:pytest.fail('Fresh exact metadata needs no repeated public lookup'))
    rows=M.BilibiliCollectionSource(dict(seeds=[seed()]),{}).fetch(None)
    assert len(rows)==1
    monkeypatch.setattr(M,'DB_PATH',tmp_path/'monitor.db');M.init_db()
    old={**rows[0],'extra':json.dumps(dict(bvid=BVID,no_reprint=1,direct_dispatch=False,
        source_role='catalog_only',metadata_status='known_duration'))}
    M.upsert_items([old]);M.upsert_items(rows)
    with M.sqlite3.connect(M.DB_PATH) as conn:
        actual=json.loads(conn.execute('select extra from items where id=?',(rows[0]['id'],)).fetchone()[0])
    assert actual['no_reprint']==1 and actual['direct_dispatch'] is False


def test_expired_snapshot_still_requires_the_normal_live_metadata_path(monkeypatch):
    source=seed();monkeypatch.setattr(V.time,'time',lambda:NOW+21601);calls=[]
    def fetch(url,**kw):
        calls.append(url)
        return json.dumps(dict(code=0,data=dict(bvid=BVID,title='林园访谈',
            owner=dict(name='当前作者'),rights=dict(no_reprint=1),
            pages=[dict(page=1,cid=9876,duration=1800,part='现在第一集')])))
    monkeypatch.setattr(M,'http_get',fetch)
    rows=M.BilibiliCollectionSource(dict(seeds=[source]),{}).fetch(None)
    assert len(calls)==1 and len(rows)==1
    extra=json.loads(rows[0]['extra'])
    assert extra['cid']==9876 and extra['no_reprint']==1 and extra['direct_dispatch'] is False


def test_new_exact_long_seed_reaches_catalogue_before_its_round_robin_turn(tmp_path,monkeypatch):
    monkeypatch.setattr(V.time,'time',lambda:NOW)
    monkeypatch.setattr(M,'DB_PATH',tmp_path/'missing.db')
    calls=[]
    def unavailable(url,**kw):
        calls.append(url)
        return json.dumps(dict(code=0,data=dict(owner=dict(name='普通作者'),pages=[])))
    monkeypatch.setattr(M,'http_get',unavailable)
    source=M.BilibiliCollectionSource(dict(seed_batch_size=1,
        seeds=[dict(bvid='BVordinary00'),seed()]),{})
    rows=source.fetch(None)
    assert [r['id'] for r in rows]==['bilibili_search:'+BVID]
    assert len(calls)==1 and 'BVordinary00' in calls[0]
    extra=json.loads(rows[0]['extra'])
    assert not extra['media_verified'] and not extra['editorial_approved']
    assert source.scan_state['bilibili_collection']['cursor']==1


def test_unreadable_old_catalogue_disables_shortcut_but_keeps_live_restrictions(tmp_path,monkeypatch):
    monkeypatch.setattr(V.time,'time',lambda:NOW)
    database=tmp_path/'broken.db';database.write_text('not a database')
    monkeypatch.setattr(M,'DB_PATH',database)
    calls=[]
    def fetch(url,**kw):
        calls.append(url)
        return json.dumps(dict(code=0,data=dict(bvid=BVID,title='林园访谈',
            owner=dict(name='当前作者'),rights=dict(no_reprint=1),
            pages=[dict(page=1,cid=9876,duration=1800,part='实时第一集')])))
    monkeypatch.setattr(M,'http_get',fetch)
    rows=M.BilibiliCollectionSource(dict(seeds=[seed()]),{}).fetch(None)
    assert len(calls)==1 and len(rows)==1
    extra=json.loads(rows[0]['extra'])
    assert extra['cid']==9876 and extra['no_reprint']==1 and extra['direct_dispatch'] is False
