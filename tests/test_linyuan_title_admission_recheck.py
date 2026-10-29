"""Actual cached holds need exact title-only provenance, never a global unlock."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import monitor_v2 as M
from fc import index as F
from verified_seed_snapshot import verified_title_admission_recheck


@pytest.fixture
def old_cache(tmp_path,monkeypatch):
    report=json.loads((ROOT/'linyuan/operations/long-event-metadata-recheck-20261028.json').read_text())
    ids={r['bvid'] for r in report['rows']}
    seeds=[e for e in json.loads((ROOT/'linyuan/bilibili_seeds.json').read_text())['seeds'] if e['bvid'] in ids]
    now=max(datetime.fromisoformat(e['checked_at']).timestamp() for e in seeds)+1
    monkeypatch.setattr(M.time,'time',lambda:now)
    monkeypatch.setattr(M,'DB_PATH',tmp_path/'catalog.db')
    prior=deepcopy(report['prior_catalogue_rows'])
    for row in prior:row['extra']=json.dumps(row['extra'])
    M.init_db();M.upsert_items(prior)
    (tmp_path/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=seeds)))
    return tmp_path,seeds,now


def catalogue():
    with sqlite3.connect(M.DB_PATH) as conn:
        conn.row_factory=sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM items')]


def test_actual_five_held_cache_rows_reach_real_admission_only_after_bound_recheck(old_cache):
    base,seeds,now=old_cache
    state=dict(dispatched=[],published={},rejected=[],pending_retry=[])
    assert F.pick(catalogue(),state,20)==[]
    assert M.refresh_title_admission(base)==5
    assert M.refresh_title_admission(base)==0
    admitted=F.pick(catalogue(),state,20)
    assert len(admitted)==5 and all(F.experiment.long_candidate(c) for c in admitted)
    for row in catalogue():
        extra=json.loads(row['extra'])
        assert extra['direct_dispatch'] is True and extra['no_reprint']==0
        assert extra['media_verified'] is extra['editorial_approved'] is False
    assert state['published']=={}


@pytest.mark.parametrize('changes',[
    dict(no_reprint=1),dict(source_role='reference'),dict(reuse_hold_reason='explicit restriction'),
    dict(exclusion_reason='origin_research_only'),dict(cid=999),dict(duration=50),
])
def test_denial_research_hold_or_changed_media_identity_cannot_be_reclassified(old_cache,changes):
    base,seeds,now=old_cache;identity=catalogue()[0]['id']
    with sqlite3.connect(M.DB_PATH) as conn:
        extra=json.loads(conn.execute('SELECT extra FROM items WHERE id=?',(identity,)).fetchone()[0])
        extra.update(changes);conn.execute('UPDATE items SET extra=? WHERE id=?',(json.dumps(extra),identity))
    assert M.refresh_title_admission(base)==4
    row=next(r for r in catalogue() if r['id']==identity)
    extra=json.loads(row['extra']);assert extra['direct_dispatch'] is False


def test_old_holds_without_prior_seed_proof_or_with_expired_observations_stay_closed(old_cache,monkeypatch):
    base,seeds,now=old_cache
    for e in seeds:
        broken=deepcopy(e);broken['title_admission_recheck']['previous_seed_sha256']='0'*64
        assert verified_title_admission_recheck(broken,now)==[]
        assert verified_title_admission_recheck(e,now+6*3600)==[]
        e.pop('title_admission_recheck')
    (base/'bilibili_seeds.json').write_text(json.dumps(dict(seeds=seeds)))
    assert M.refresh_title_admission(base)==0
