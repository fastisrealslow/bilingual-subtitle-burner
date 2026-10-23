import json
from pathlib import Path
import sqlite3
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import monitor_v2 as M
from native_parent_catalog import parent_seeds


def database(tmp_path,rows):
    path=tmp_path/'catalog.db'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE items(id TEXT,source TEXT,title TEXT,url TEXT,author TEXT,publish_time TEXT,extra TEXT)')
        for index,row in enumerate(rows):
            bvid=row.pop('bvid','BV1hS421X7Xd')
            values=dict(id='item'+str(index),source='bilibili_search',title='林园最新北大演讲完整版',
                url='https://www.bilibili.com/video/'+bvid,author='真实上传者',publish_time='2024-08-17',
                extra=dict(bvid=bvid,duration=6996))
            values.update(row)
            if not isinstance(values['extra'],str):values['extra']=json.dumps(values['extra'])
            c.execute('INSERT INTO items VALUES(:id,:source,:title,:url,:author,:publish_time,:extra)',values)
    return path


def test_live_native_pages_are_discovered_without_static_seed_growth_or_more_requests(tmp_path,monkeypatch):
    path=database(tmp_path,[{}]);before=path.read_bytes();monkeypatch.setattr(M,'DB_PATH',path)
    evidence=json.loads((ROOT/'linyuan/operations/native-long-page-metadata-20261023.json').read_text())
    parent=next(r for r in evidence['pages'] if r['bvid']=='BV1hS421X7Xd');calls=[]
    def request(url,**kw):
        calls.append(url);assert url.endswith('BV1hS421X7Xd')
        return json.dumps(dict(code=0,data=parent))
    monkeypatch.setattr(M,'http_get',request)
    rows=M.BilibiliCollectionSource(dict(expand_overlimit_parents=True,seed_batch_size=1),{}).fetch(None)
    assert len(calls)==1 and len(rows)==5 and path.read_bytes()==before
    assert [json.loads(r['extra'])['cid'] for r in rows]==[p['cid'] for p in parent['pages'][:5]]
    assert all(120<=json.loads(r['extra'])['duration']<=5400 for r in rows)


@pytest.mark.parametrize('row',[
    dict(author='园园滚雪球'),dict(author='园来滚雪球'),dict(author=''),
    dict(title='别人解说林园的投资故事'),dict(title='林园AI合成访谈'),
    dict(title='林园演讲录音完整版'),dict(source='competitor_reference'),
    dict(title='林园最新演讲（音频）'),
    dict(extra=dict(bvid='BV1hS421X7Xd',duration=6996,no_reprint=1)),
    dict(extra=dict(bvid='BV1hS421X7Xd',duration=6996,direct_dispatch=False)),
    dict(extra=dict(bvid='BV1hS421X7Xd',duration=6996,source_role='catalog_only')),
    dict(extra=dict(bvid='BV1hS421X7Xd',duration=6996,page=2)),
    dict(extra=dict(bvid='BV1hS421X7Xd',duration=5400)),
    dict(url='https://www.bilibili.com/video/BV1hS421X7Xd?p=2'),
    dict(url='https://example.com/video/BV1hS421X7Xd'),dict(extra='broken'),
])
def test_no_reference_restriction_non_live_child_or_bad_identity_becomes_parent(tmp_path,row):
    assert parent_seeds(database(tmp_path,[dict(row)]))==[]


def test_missing_database_is_read_only_and_static_seeds_are_not_repeated(tmp_path):
    missing=tmp_path/'missing.db';assert parent_seeds(missing)==[] and not missing.exists()
    db=database(tmp_path,[{}]);assert parent_seeds(db,existing={'BV1hS421X7Xd'})==[]
    rows=parent_seeds(db);assert len(rows)==1
    assert rows[0]['media_verified'] is False and rows[0]['editorial_approved'] is False


def test_unattempted_exact_parents_precede_completed_lookups_with_hard_cap(tmp_path):
    db=database(tmp_path,[dict(bvid='BV'+str(i).zfill(10)) for i in range(70)])
    attempted={'BV'+str(i).zfill(10) for i in range(10)}
    rows=parent_seeds(db,attempted=attempted,limit=1000)
    assert len(rows)==64 and all(r['bvid'] not in attempted for r in rows[:60])


def test_optional_catalog_error_does_not_block_existing_seed_rotation(tmp_path,monkeypatch):
    db=tmp_path/'broken.db';db.write_bytes(b'not sqlite');monkeypatch.setattr(M,'DB_PATH',db)
    monkeypatch.setattr(M,'http_get',lambda *a,**kw:json.dumps(dict(code=0,data=dict(
        owner=dict(name='原作者'),title='林园访谈',rights=dict(no_reprint=1),
        pages=[dict(page=1,cid=42,duration=1600,part='第一集')]))))
    rows=M.BilibiliCollectionSource(dict(expand_overlimit_parents=True,seeds=[dict(bvid='BV1hS421X7Xd')]),{}).fetch(None)
    assert len(rows)==1 and json.loads(rows[0]['extra'])['direct_dispatch'] is False


def test_live_view_can_deny_a_catalog_lead_and_keep_it_research_only(tmp_path,monkeypatch):
    monkeypatch.setattr(M,'DB_PATH',database(tmp_path,[{}]))
    monkeypatch.setattr(M,'http_get',lambda *a,**kw:json.dumps(dict(code=0,data=dict(
        owner=dict(name='真实上传者'),title='林园访谈',rights=dict(no_reprint=1),
        pages=[dict(page=1,cid=42,duration=1600,part='第一集')]))))
    rows=M.BilibiliCollectionSource(dict(expand_overlimit_parents=True,seed_batch_size=1),{}).fetch(None)
    assert len(rows)==1 and json.loads(rows[0]['extra'])['source_role']=='catalog_only'


@pytest.mark.parametrize('reverse',[False,True])
def test_weaker_duplicate_cannot_erase_a_known_parent_restriction(tmp_path,reverse):
    rows=[dict(extra=dict(bvid='BV1hS421X7Xd',duration=6996)),
          dict(extra=dict(bvid='BV1hS421X7Xd',duration=6996,no_reprint=1))]
    if reverse:rows.reverse()
    assert parent_seeds(database(tmp_path,rows))==[]
