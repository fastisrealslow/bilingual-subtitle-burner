"""Rank eligible inspected parts globally; cover preference cannot alter groups."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan/fc'),str(Path(__file__).resolve().parents[1]/'linyuan')]
import index as fc

T='field-story-v1'
NOW=datetime(2026,11,14,14,tzinfo=fc.experiment.CST).timestamp()


def part(index=0,*,profile='baseline',live=False,blocked=False):
    return dict(index=index,status='verified',sha256=str(index+1)*64,title=f'视频{index}{profile}{live}',
        render_mode='live_video_card',resolution=dict(width=1280,height=720),profile=profile,
        cover_person_image_source='verified_source_frame' if live else 'authority_reference',
        cover_person_image_verified=True,cover_style='editorial' if live else 'dark',blocked=blocked)


@pytest.fixture
def selection(monkeypatch):
    # Profile classification and media acceptance are independently tested by
    # the story-proof/full media suites; isolate ordering of accepted metadata.
    monkeypatch.setattr(fc.time,'time',lambda:NOW)
    monkeypatch.setattr(fc,'source_inventory',lambda *a,**k:dict(inventory_fresh=True))
    monkeypatch.setattr(fc.experiment,'rank',lambda p,plan:int(p['profile']!=plan['planned']))
    monkeypatch.setattr(fc,'inventory_publication_error',lambda p,e,s:'rejected' if p['blocked'] else None)
    def choose(records,processed=None,hour=14):
        stamp=datetime(2026,11,14,hour,tzinfo=fc.experiment.CST).timestamp()
        entries=[dict(slug=r['slug'],source_url='https://example.test/'+r['slug'],
                      processed_part_indices=(processed or {}).get(r['slug'],[])) for r in records]
        state=dict(dispatched=entries,published={},daily_publish=dict(date='2026-11-14',count=1,published_hours=[10]))
        old=deepcopy(state)
        result=fc.inventory_catchup_request(state,dict(artifacts=records),stamp)
        assert state==old
        return result
    return choose


def record(slug,parts,artifact=10):return dict(slug=slug,artifact_id=artifact,parts=parts)


def test_consumed_story_cannot_promote_its_baseline_sibling_ahead_of_real_story(selection):
    a=record('old',[part(profile=T,live=True),part(1)])
    b=record('real',[part(profile=T,live=True)],20)
    r=selection([a,b],processed={'old':[0]})
    assert (r['batch_slug'],r['artifact_id'])==('real',20)


def test_rejected_story_cannot_promote_baseline_sibling_ahead_of_another_valid_story(selection):
    a=record('blocked',[part(profile=T,live=True,blocked=True),part(1)])
    b=record('real',[part(profile=T,live=True)],20)
    assert selection([a,b])['batch_slug']=='real'


def test_baseline_fallback_prefers_verified_live_cover_without_claiming_treatment(selection):
    static=record('static',[part()]);live=record('live',[part(live=True)],20)
    r=selection([static,live])
    assert r['batch_slug']=='live' and r['expected_sha256']=='1'*64
    assert 'publishing_experiment' not in r  # Actual publisher creates the honest receipt.


def test_experiment_group_precedes_cover_preference(selection):
    story=record('story',[part(profile=T)])  # Missing cached cover metadata is not a group change.
    baseline=record('baseline',[part(live=True)],20)
    assert selection([baseline,story])['batch_slug']=='story'


def test_blocked_or_wrong_layout_frame_cannot_outrank_valid_static_fallback(selection):
    bad=part(live=True,blocked=True)
    wrong=part(1,live=True);wrong['resolution']=dict(width=720,height=1280)
    assert selection([record('blocked',[bad,wrong]),record('safe',[part()],20)])['batch_slug']=='safe'


def test_cover_preference_requires_verified_source_and_supported_style():
    live=part(live=True)
    assert fc.cover_source_rank(live)==0
    for mutation in [dict(cover_person_image_verified=False),dict(cover_person_image_source='authority_reference'),dict(cover_style='unknown')]:
        assert fc.cover_source_rank({**live,**mutation})==1


def test_part_index_prefers_real_cover_without_advancing_progress(monkeypatch):
    monkeypatch.setattr(fc.experiment,'rank',lambda p,plan:0)
    entry=dict(slug='same',published_parts=0)
    p0=part();p1=part(1,live=True)
    r=record('same',[p0,p1])
    assert fc.inventory_part_index(entry,10,[r],{},now=NOW,publication_state={})==1
    assert entry==dict(slug='same',published_parts=0)


@pytest.mark.parametrize('kwargs',[{},dict(now=NOW),dict(publication_state={})])
def test_manual_or_historical_selection_preserves_original_part_order(monkeypatch,kwargs):
    monkeypatch.setattr(fc.experiment,'rank',lambda p,plan:0)
    entry=dict(slug='same',published_parts=0)
    r=record('same',[part(),part(1,live=True)])
    assert fc.inventory_part_index(entry,10,[r],{},**kwargs)==0
