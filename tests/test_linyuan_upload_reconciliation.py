"""Expired intents must neither stop all daily posts nor invent experiment receipts."""
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
from unittest.mock import Mock
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as fc
import upload_recovery as recovery
import bili_archive_status as archive

START=1794017055
NOW=START+8000
BV='BV1Abcdefghi'


def entry(**changes):
    intent=dict(title='林园：一个有来源的标题',slug='upload',part_index=0,artifact_id=123,
        source_sha256='b'*64,source_segments=[dict(start=20,end=100)],duration_sec=80,
        fingerprints=dict(sha256='a'*64),editorial_profile='field-story-v1',
        publishing_experiment=dict(planned='field-story-v1',actual='field-story-v1',
            compliant=True,slot='2026-11-07 10'),scheduled_slot='2026-11-07 10',
        render_mode='live_video_card')
    return dict(slug='upload',ts=START,uploading=True,uploading_ts=START,
        upload_title=intent['title'],upload_receipt_intent=intent,upload_part_index=0,
        upload_artifact_id=123,upload_parts_total=2,upload_expected_sha256='a'*64,
        source_url='https://www.bilibili.com/video/BV1OtherSource',**changes)


def snapshot(e=None, **changes):
    videos=[] if e is None else [dict(bvid=BV,mid=fc.OWNER_MID,title=e['upload_title'],
        ctime=START+20,duration=80,public=True)]
    return dict(verification_origin='authenticated-creator-read',owner_mid=fc.OWNER_MID,
        api_code=0,scan_complete=True,owner_verified=True,videos=videos,**changes)


@pytest.fixture(autouse=True)
def no_writes(monkeypatch):
    monkeypatch.setattr(fc,'save_state',Mock(return_value=True))


@pytest.mark.parametrize('age',[0,2700-1])
def test_active_lease_never_queries_or_replays(age):
    st=dict(dispatched=[entry()]);read=Mock(side_effect=AssertionError('active upload'))
    out=recovery.reconcile(fc,st,now=START+age,lookup=read)
    assert out['pending']==1 and not out['changed']
    read.assert_not_called();fc.save_state.assert_not_called()


@pytest.mark.parametrize('changes',[dict(api_code=-101),dict(scan_complete=False),
    dict(http_status=412),dict(owner_mid='another-account'),dict(owner_verified=False),
    dict(response_error='archive_list_missing')])
def test_unavailable_or_incomplete_read_is_never_absence(changes):
    e=entry();proof=snapshot();proof.update(changes);st=dict(dispatched=[e])
    out=recovery.reconcile(fc,st,now=NOW,lookup=lambda _:proof)
    assert out['pending']==1 and e['uploading'] and not e.get('publication_quarantined')
    assert not st.get('published')


def test_absent_expired_legacy_attempt_is_isolated_without_media_failure_or_retry():
    e=entry();e.pop('upload_receipt_intent');st=dict(dispatched=[e]);read=Mock(return_value=snapshot())
    out=recovery.reconcile(fc,st,now=NOW,lookup=read)
    assert out['quarantined']==1 and not out['pending']
    assert e['publication_quarantined'] and not e.get('uploading') and not e.get('failed')
    assert e['uploading_ts']==START and e['upload_title'] and not st.get('published')
    assert e['upload_reconciliation']['status']=='absent_quarantined'
    assert fc.makeup_request_error(dict(batch_slug='new',artifact_id=9,batch_remaining=1,
        expected_sha256='f'*64,makeup_slot='2026-11-07 10'),st,NOW) is None
    # Permanent isolation follows the file, exact title and source across slugs.
    assert recovery.quarantine_error(dict(sha256='a'*64),dict(slug='copy'),st)
    assert recovery.quarantine_error(dict(title=e['upload_title']),dict(slug='copy'),st)
    assert recovery.quarantine_error({},dict(slug='copy',source_url=e['source_url']+'?p=1'),st)
    assert recovery.quarantine_error({},dict(slug='unrelated',source_url='https://weibo.com/123'),st) is None
    recovery.reconcile(fc,st,now=NOW+1000,lookup=read)
    assert read.call_count==1


def test_legacy_waits_past_function_timeout_but_finished_cli_does_not_wait_two_hours():
    e=entry();st=dict(dispatched=[e]);now=START+2800
    assert recovery.reconcile(fc,st,now=now,lookup=lambda _:snapshot())['pending']==1
    e['upload_process_result']=dict(completed_at=START+1620,outcome='timeout')
    out=recovery.reconcile(fc,st,now=now+301,lookup=lambda _:snapshot())
    assert out['quarantined']==1


def test_previous_parts_cli_result_cannot_close_a_new_upload_attempt():
    e=entry();e['upload_process_result']=dict(completed_at=START-1,returncode=0)
    out=recovery.reconcile(fc,dict(dispatched=[e]),now=START+2800,lookup=lambda _:snapshot())
    assert out['pending']==1 and e['uploading'] and not e.get('publication_quarantined')


@pytest.mark.parametrize('mutate',[
    lambda v:v.update(title='林园：一个有来源的标题，但是另一条'),
    lambda v:v.update(ctime=START-500),lambda v:v.update(duration=120),
    lambda v:v.update(mid=1),lambda v:v.update(public=False)])
def test_prefix_old_archive_wrong_duration_owner_and_pending_visibility_do_not_restore(mutate):
    e=entry();proof=snapshot(e);mutate(proof['videos'][0]);st=dict(dispatched=[e])
    recovery.reconcile(fc,st,now=NOW,lookup=lambda _:proof)
    assert not st.get('published')
    if proof['videos'][0]['title']==e['upload_title']:assert e['uploading']


def test_exact_creator_receipt_restores_ranges_experiment_slot_and_daily_count_once():
    e=entry();st=dict(dispatched=[e],daily_publish=dict(date='2026-11-07',count=1,published_hours=[14]))
    out=recovery.reconcile(fc,st,now=NOW,lookup=lambda _:snapshot(e))
    p=st['published']['upload']['parts'][0]
    assert out['recovered']==1 and not out['pending']
    assert p['bvid']==BV and p['fingerprints']['sha256']=='a'*64 and p['source_segments']
    assert p['publishing_experiment']['compliant'] and p['scheduled_slot']=='2026-11-07 10'
    assert st['daily_publish']['count']==2 and st['daily_publish']['published_hours']==[14,10]
    assert e['published_parts']==1 and not e.get('uploading')
    recovery.reconcile(fc,st,now=NOW+500,lookup=Mock(side_effect=AssertionError('no replay')))
    assert st['daily_publish']['count']==2 and len(st['published']['upload']['parts'])==1


def test_prior_day_recovery_does_not_erase_todays_budget():
    e=entry();st=dict(dispatched=[e],daily_publish=dict(date='2026-11-08',count=3,published_hours=[10,14,16]))
    recovery.reconcile(fc,st,now=NOW+86400,lookup=lambda _:snapshot(e))
    assert st['daily_publish']['count']==3 and st['daily_publish']['date']=='2026-11-08'


def test_title_only_or_cross_bound_intent_cannot_fabricate_experiment_receipt():
    for field in ['upload_receipt_intent','upload_artifact_id','upload_part_index']:
        e=entry();e.pop(field);st=dict(dispatched=[e])
        recovery.reconcile(fc,st,now=NOW,lookup=lambda _:snapshot(e))
        assert e['uploading'] and not st.get('published')
        assert e['upload_reconciliation']['status']=='identity_found_metadata_missing'


def test_reconciliation_save_failure_stops_before_inventory_or_upload(monkeypatch):
    st=dict(dispatched=[entry()])
    monkeypatch.setattr(fc,'load_state',lambda:st)
    monkeypatch.setattr(fc.upload_recovery,'reconcile',lambda *a:dict(persist_failed=True))
    monkeypatch.setattr(fc,'gh',Mock(side_effect=AssertionError('no inventory or upload')))
    assert fc.publish_catchup({})==dict(published=0,upload_reconciliation_not_saved=1)


def test_pending_upload_is_reported_as_upload_block_not_empty_stock(monkeypatch):
    monkeypatch.setattr(fc,'load_state',lambda:dict(dispatched=[entry()]))
    monkeypatch.setattr(fc.upload_recovery,'reconcile',lambda *a:dict(pending=1))
    monkeypatch.setattr(fc,'gh',Mock(side_effect=AssertionError('not a stock failure')))
    assert fc.publish_catchup({})==dict(published=0,upload_reconciliation_pending=1)


@pytest.mark.parametrize('payload,complete',[
    (dict(code=0,data=dict(arc_audits=[])),True),
    (dict(code=0,data={}),False),(dict(code=-101),False),
    (dict(code=0,data=dict(arc_audits=[dict(Archive={})]*50)),False)])
def test_creator_absence_requires_real_exhausted_archive_list(monkeypatch,payload,complete):
    class Opener:
        def open(self,*a,**kw):return io.StringIO(json.dumps(payload))
    monkeypatch.setattr(archive.urllib.request,'build_opener',lambda *a:Opener())
    cookies=json.dumps(dict(DedeUserID=str(fc.OWNER_MID),SESSDATA='test-only'))
    proof=archive.archive_status([],fc.OWNER_MID,cookies,expected_titles=['exact'],max_pages=2)
    assert proof['scan_complete']==complete and proof['found_count']==0


def test_creator_response_from_another_owner_cannot_release_any_upload(monkeypatch):
    payload=dict(code=0,data=dict(arc_audits=[dict(Archive=dict(mid=999,title='other'))]))
    class Opener:
        def open(self,*a,**kw):return io.StringIO(json.dumps(payload))
    monkeypatch.setattr(archive.urllib.request,'build_opener',lambda *a:Opener())
    proof=archive.archive_status([],fc.OWNER_MID,
        json.dumps(dict(DedeUserID=str(fc.OWNER_MID),SESSDATA='test-only')),expected_titles=['exact'])
    assert not proof['scan_complete'] and not proof['owner_verified']
    assert proof['response_error']=='archive_owner_mismatch'
