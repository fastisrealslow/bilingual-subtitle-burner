"""Discover existing short-source formats without admitting archive bumpers."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import source_catalogue_duration as D
import monitor_v2 as M
from bilibili_series import episode_items
from seed_campaign import verify_seed

BVID = 'BV1NpBFYkEKx'


@pytest.fixture(autouse=True)
def current_production_duration_policy(monkeypatch):
    # Some legacy test modules select legacy120 during collection. Exercise
    # this discovery path under the deployed reference_v1 contract explicitly.
    monkeypatch.setattr(D,'MIN_SECONDS',20)


@pytest.mark.parametrize('seconds,allowed', [(19.99,False),(20,True),(119,True),
    (120,True),(1200,True),(5400,True),(5401,False),(float('nan'),False),
    (float('inf'),False),(True,False),('60',False)])
def test_standalone_catalogue_follows_the_existing_production_floor(seconds,allowed):
    assert D.eligible_duration(seconds) is allowed


def test_archive_chapters_and_legacy_policy_keep_their_existing_floor(monkeypatch):
    assert not D.eligible_duration(21,2)
    assert not D.eligible_duration(119,63)
    assert D.eligible_duration(120,2)
    monkeypatch.setattr(D,'MIN_SECONDS',120)
    assert not D.eligible_duration(119) and D.eligible_duration(120)


def payload(pages,no_reprint=0):
    return dict(code=0,data=dict(bvid=BVID,title='林园：谈投资的风险',
        owner=dict(name='实际作者',mid=123),rights=dict(no_reprint=no_reprint),
        pubdate=1700000000,pages=pages))


def page(seconds,number=1):
    return dict(page=number,cid=100+number,duration=seconds,part='林园谈投资')


def test_exact_short_metadata_retains_provenance_and_does_not_approve_media():
    row=verify_seed(dict(bvid=BVID),lambda *a,**kw:json.dumps(payload([page(65)])))
    assert row['status']=='verified_metadata' and row['pages'][0]['duration']==65
    assert row['metadata_provenance']=='bilibili_view_exact_id_author_pages'
    assert not row['media_verified'] and not row['editorial_approved']
    denied=verify_seed(dict(bvid=BVID),lambda *a,**kw:json.dumps(payload([page(65)],1)))
    assert denied['direct_dispatch'] is False and denied['source_role']=='catalog_only'


def test_collection_and_series_keep_short_standalone_sources_but_drop_bumpers(monkeypatch):
    def collect(pages,no_reprint=0):
        monkeypatch.setattr(M,'http_get',lambda *a,**kw:json.dumps(payload(pages,no_reprint)))
        return M.BilibiliCollectionSource(dict(seeds=[dict(bvid=BVID)]),{}).fetch(None)
    rows=collect([page(65)])
    assert len(rows)==1 and json.loads(rows[0]['extra'])['duration']==65
    assert json.loads(collect([page(65)],1)[0]['extra'])['direct_dispatch'] is False
    rows=collect([page(21),page(1800,2)])
    assert len(rows)==1 and json.loads(rows[0]['extra'])['page']==2
    e=dict(bvid=BVID,arc=dict(title='林园访谈',author=dict(name='实际作者')),
        pages=[page(65)])
    series=dict(ugc_season=dict(sections=[dict(episodes=[e])]))
    assert len(episode_items(series,M.duration_seconds,M.source_publish_time))==1
    e['pages']=[page(21),page(1800,2)]
    result=episode_items(series,M.duration_seconds,M.source_publish_time)
    assert len(result)==1 and json.loads(result[0]['extra'])['page']==2


def test_short_seed_does_not_become_a_long_experiment_mother():
    import publishing_experiment as E
    assert not E.long_candidate(dict(extra=dict(duration=119)))
    assert not E.long_candidate(dict(extra=dict(duration=1199)))
    assert E.long_candidate(dict(extra=dict(duration=1200)))
