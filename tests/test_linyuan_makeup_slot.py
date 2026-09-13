"""The one authorized missed slot cannot consume another slot or replay an old BV."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as fc
from publish_approved import exact_receipt


TEN=1789264800
REQUEST=dict(batch_slug='new',artifact_id=123,batch_remaining=1,
             expected_sha256='a'*64,makeup_slot='2026-09-13 10')


def test_named_slot_timestamp_and_idempotency_are_exact():
    assert datetime.fromtimestamp(TEN,timezone.utc).isoformat()=='2026-09-13T02:00:00+00:00'
    assert fc.makeup_request_error(REQUEST,{},TEN+7200) is None
    assert fc.makeup_request_error(REQUEST,{},TEN-1)=='makeup_not_due'
    for key,value in [('makeup_slot','2026-09-13 14'),('batch_remaining',2),('artifact_id',0),('expected_sha256','wrong')]:
        assert fc.makeup_request_error({**REQUEST,key:value},{},TEN+7200)
    assert fc.makeup_request_error(REQUEST,{'dispatched':[{'slug':'other','uploading':True}]},TEN+7200)=='makeup_upload_unresolved'


def test_makeup_at_fourteen_charges_ten_and_leaves_landscape_slot_free():
    receipt=dict(status='published',bvid='BV1234567890',ts=TEN+4*3600)
    st={'published':{'new':{'parts':[receipt]}}}
    fc.record_publication_slot(st,receipt,TEN+4*3600,REQUEST['makeup_slot'])
    assert st['daily_publish']==dict(date='2026-09-13',count=1,published_hours=[10])
    assert fc.slot_published(st,TEN)
    assert not fc.slot_published(st,TEN+4*3600)
    assert fc.catchup_deficit(st,TEN+4*3600)==1
    assert fc.makeup_request_error(REQUEST,st,TEN+4*3600)=='makeup_already_completed'
    # A normal landscape publication must never satisfy the earlier ten-slot debt.
    other={'published':{'wide':{'parts':[dict(status='published',bvid='BV0987654321',ts=TEN+4*3600)]}},
           'daily_publish':dict(date='2026-09-13',count=1,published_hours=[14])}
    assert fc.makeup_request_error(REQUEST,other,TEN+4*3600) is None


def test_later_day_makeup_counts_actual_day_without_occupying_its_ten_slot():
    receipt=dict(status='published',bvid='BV1234567890',ts=TEN+86400)
    st={'daily_publish':dict(date='2026-09-13',count=3),'published':{'new':{'parts':[receipt]}}}
    fc.record_publication_slot(st,receipt,TEN+86400,REQUEST['makeup_slot'])
    assert st['daily_publish']==dict(date='2026-09-14',count=1)
    assert not fc.slot_published(st,TEN+86400)
    assert fc.slot_published(st,TEN)


def test_makeup_preserves_daily_cap_before_reading_artifacts(monkeypatch):
    monkeypatch.setattr(fc.time,'time',lambda:TEN+7200)
    st={'daily_publish':dict(date='2026-09-13',count=4),
        'dispatched':[dict(slug='new')],'published':{}}
    monkeypatch.setattr(fc,'load_state',lambda:st)
    monkeypatch.setattr(fc,'save_state',lambda _:None)
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda _:0)
    monkeypatch.setattr(fc,'gh',lambda *a,**k:pytest.fail('Daily cap must stop before artifacts/upload'))
    assert fc.publish_handler(REQUEST)=={'published':0}


def test_original_slot_still_requires_portrait_live_and_protects_sunday_mother():
    live=dict(render_mode='live_video_card',resolution=dict(width=720,height=1280))
    assert fc.content_fits_slot(live,{},TEN)
    assert not fc.content_fits_slot(live,{'weekly_full_week':'2026-W37'},TEN)
    assert not fc.content_fits_slot({**live,'content_type':'full_interview'},{},TEN)
    assert not fc.content_fits_slot({**live,'resolution':dict(width=1280,height=720)},{},TEN)
    assert fc.daily_mix_error({**live,'render_mode':'audio_card'}, {})


def test_exact_receipt_needs_matching_file_and_debt_not_old_slug_bvid():
    request={**REQUEST,'slug':'new'}
    part=dict(status='published',bvid='BV1234567890',fingerprints=dict(sha256='a'*64),makeup_slot=REQUEST['makeup_slot'])
    assert exact_receipt({'published':{'new':{'bvid':'BV1234567890'}}},request) is None
    st={'published':{'new':{'parts':[part]}}}
    assert exact_receipt(st,request)==part
    for key,value in [('makeup_slot',None),('bvid',''),('fingerprints',{'sha256':'b'*64}),('status','skipped')]:
        bad=deepcopy(st);bad['published']['new']['parts'][0][key]=value
        assert exact_receipt(bad,request) is None
