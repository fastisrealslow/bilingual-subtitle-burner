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
import materialize_verified_seeds as M

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


def export_catalogue(base):
    with sqlite3.connect(monitor.DB_PATH) as connection:
        connection.row_factory=sqlite3.Row
        rows=[dict(row) for row in connection.execute('SELECT * FROM items')]
    path=base/'dashboard/data.json';path.parent.mkdir(exist_ok=True)
    for row in rows:row['extra']=json.loads(row['extra'])
    path.write_text(json.dumps(rows))


def admission_state(base,**changes):
    path=base/'.automation/fc_state.json';path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(dict(dispatched=[],published={},rejected=[],pending_retry=[],**changes)))
    return path


def test_changed_admissible_long_candidate_wakes_normal_admission_without_changing_state(catalogue):
    report=materialize(catalogue);export_catalogue(catalogue);path=admission_state(catalogue)
    before=path.read_bytes()
    assert M.admissible_long_count(catalogue,report['changed_candidate_ids'])==1
    assert M.admissible_long_count(catalogue,[])==0
    assert path.read_bytes()==before and report['accepted_mp4_count']==0


@pytest.mark.parametrize('failed',[True,False,None])
def test_old_failed_or_unknown_attempt_never_wakes_a_new_data_dispatch(catalogue,failed):
    report=materialize(catalogue);export_catalogue(catalogue)
    path=admission_state(catalogue)
    state=json.loads(path.read_text());state['dispatched']=[dict(key='bilibili_search:'+BVID,
        slug='old',source_url=seed()['url'],failed=failed)];path.write_text(json.dumps(state))
    before=path.read_bytes()
    assert M.admissible_long_count(catalogue,report['changed_candidate_ids'])==0
    assert path.read_bytes()==before


def test_new_short_page_does_not_wake_an_unrelated_long_page(catalogue):
    report=materialize(catalogue);export_catalogue(catalogue);admission_state(catalogue)
    path=catalogue/'dashboard/data.json';rows=json.loads(path.read_text())
    rows.append({**rows[0],'id':'changed-short','extra':{**rows[0]['extra'],'duration':600}})
    path.write_text(json.dumps(rows))
    assert M.admissible_long_count(catalogue,['changed-short'])==0
    assert M.admissible_long_count(catalogue,report['changed_candidate_ids'])==1


def test_preserved_parent_denial_does_not_emit_a_wakeup(catalogue):
    monitor.upsert_items([dict(id='bilibili_search:'+BVID+':p2',source='bilibili_collection',
        title='林园完整访谈',url=seed()['url']+'?p=2',author='真实采访方',publish_time='',
        extra=json.dumps(dict(bvid=BVID,no_reprint=1,direct_dispatch=False)))])
    report=materialize(catalogue);export_catalogue(catalogue);admission_state(catalogue)
    assert M.admissible_long_count(catalogue,report['changed_candidate_ids'])==0


@pytest.mark.parametrize('history',[{},dict(dispatched=[],published={}),dict(dispatched=None,published={},rejected=[])])
def test_missing_or_unknown_history_cannot_be_treated_as_a_new_source(catalogue,history):
    report=materialize(catalogue);export_catalogue(catalogue);path=admission_state(catalogue)
    path.write_text(json.dumps(history))
    assert M.admissible_long_count(catalogue,report['changed_candidate_ids'])==0


def test_cli_wakeup_output_is_true_only_after_a_real_changed_long_observation(catalogue,monkeypatch,tmp_path):
    admission_state(catalogue);monkeypatch.setattr(M,'BASE',catalogue)
    monkeypatch.setattr(monitor,'export_dashboard_data',lambda:export_catalogue(catalogue))
    output=tmp_path/'step-output';monkeypatch.setenv('GITHUB_OUTPUT',str(output))
    M.main();assert output.read_text().splitlines()[-1]=='admission_wakeup_required=true'
    M.main();assert output.read_text().splitlines()[-1]=='admission_wakeup_required=false'


@pytest.mark.parametrize('workflow',['linyuan-monitor.yml','linyuan-seed-growth-tick.yml'])
def test_standard_wakeup_runs_only_after_catalogue_commit_and_keeps_existing_clocks(workflow):
    import yaml
    data=yaml.safe_load((Path(__file__).resolve().parents[1]/'.github/workflows'/workflow).read_text())
    steps=next(iter(data['jobs'].values()))['steps']
    source=next(i for i,s in enumerate(steps) if s.get('id')=='seed_catalogue')
    commit=next(i for i,s in enumerate(steps) if s.get('id')=='persist_seed_catalogue')
    wake=next(i for i,s in enumerate(steps) if 'gh workflow run linyuan-dispatch-runner.yml' in s.get('run',''))
    assert source<commit<wake and 'admission_wakeup_required' in steps[wake]['if']
    assert 'persist_seed_catalogue.outcome' in steps[wake]['if']
    assert '--ref main' in steps[wake]['run'] and 'ALIYUN_AK' not in steps[wake].get('env',{})
    assert data['permissions']['actions']=='write' and data['concurrency']['group']=='monitor'
