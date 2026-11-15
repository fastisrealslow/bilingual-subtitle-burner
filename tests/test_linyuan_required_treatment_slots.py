"""A late T delivery must still have room after both normal controls publish."""
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib
import pytest
from test_linyuan_publishing_experiment import F,E,treatment
from test_linyuan_experiment_inventory_refresh import setup


def stamp(clock,day='2026-11-15'):
    return datetime.fromisoformat(day+'T'+clock).replace(tzinfo=E.CST).timestamp()


def control(wide=False):
    return dict(editorial_profile=E.BASELINE,render_mode='direct',content_type='short_argument',
        resolution=dict(width=1920,height=1080) if wide else dict(width=1080,height=1920))


def payload(records,now):
    return dict(quality_gate_version=F.QUALITY_GATE_VERSION,editorial_policy_version=F.editorial.VERSION,
                updated_at=now,artifacts=records)


@pytest.mark.parametrize('day',['2026-11-15','2026-11-20','2026-12-01'])
@pytest.mark.parametrize('hour',[14,21])
def test_required_slots_reject_ordinary_fallback_even_after_the_old_waiting_window(day,hour):
    now=stamp(f'{hour}:00:00',day);meta=control(hour==14);before=deepcopy(meta)
    assert not F.content_fits_slot(meta,{},now,weekly_fallback=True)
    record=dict(slug='C',artifact_id=123,parts=[dict(meta,index=0,status='verified')])
    assert F.inventory_part_index(dict(slug='C'),123,[record],{},now) is None
    assert meta==before and E.actual_profile(meta)==E.BASELINE


@pytest.mark.parametrize('hour',[14,21])
def test_historical_cohort_fallback_and_its_profile_are_not_rewritten(hour):
    now=stamp(f'{hour}:00:00','2026-11-14');meta=control(hour==14)
    assert F.content_fits_slot(meta,{},now)
    assert not F.required_treatment_slot(E.production_assignment(now))
    assert E.receipt(meta,now)['actual']==E.BASELINE
    assert E.EXPERIMENT_ID=='field-story-30d-20261018' and E.SELECTION_REVISION==2026102201


@pytest.mark.parametrize('clock,hour',[
    ('14:00:00',14),('15:40:00',14),('18:00:00',14),('23:59:59',14),
    ('21:00:00',21),('21:40:00',21),('21:55:00',21),('23:59:59',21)])
def test_missing_treatment_returns_immediately_and_reserves_original_slot_until_day_end(clock,hour):
    now=stamp(clock);plan=E.production_assignment(now,f'2026-11-15 {hour}')
    stock=dict(inventory_fresh=True,experiment_ready=dict(landscape=0,portrait=0))
    assert F.treatment_wait_until(stock,plan,now)==stamp('00:00:00','2026-11-16')


def test_automatic_publisher_keeps_backoff_without_scanning_or_uploading_a_control(monkeypatch):
    state,calls,now=setup(monkeypatch,14,'2026-11-15');before=deepcopy(state)
    monkeypatch.setattr(F,'source_inventory',lambda _:dict(inventory_fresh=True,
        experiment_ready=dict(landscape=0,portrait=0)))
    expected=dict(published=0,waiting_for_treatment=1,required_treatment_slot=1,
        treatment_deadline=stamp('00:00:00','2026-11-16'),inventory_refresh_requested=1)
    assert F.publish_handler()==expected
    assert F.publish_handler()=={**expected,'inventory_refresh_requested':0}
    assert len(calls)==1 and state.pop('inventory_refresh_requested_at')==now and state==before


def test_pending_afternoon_does_not_block_the_later_control_slot(monkeypatch):
    now=stamp('18:00:00');monkeypatch.setattr(F.time,'time',lambda:now)
    monkeypatch.setattr(F,'inventory_publication_error',lambda *a:None)
    entry=dict(slug='C',source_url='https://example.test/control')
    part=dict(control(),index=0,status='verified',sha256='a'*64,title='林园：普通稿')
    record=dict(slug='C',artifact_id=123,parts=[part]);data=payload([record],now)
    state=dict(dispatched=[entry],published={},daily_publish=dict(date='2026-11-15',count=1,published_hours=[10]))
    before=deepcopy(state);request=F.inventory_catchup_request(state,data,now)
    assert request['makeup_slot']=='2026-11-15 16' and request['batch_slug']=='C'
    assert request['weekly_full_fallback']  # Sunday control fallback remains available.
    assert state==before


def test_two_late_wide_stories_recover_both_slots_without_exceeding_daily_four(monkeypatch):
    now=[stamp('18:00:00')];monkeypatch.setattr(F.time,'time',lambda:now[0])
    # This tests scheduling and distinct receipts. Independent media, rights,
    # topic and source-window tests validate the parts before this boundary.
    monkeypatch.setattr(F,'inventory_publication_error',lambda *a:None)
    state=dict(dispatched=[],published={},daily_publish=dict(date='2026-11-15',count=2,published_hours=[10,16]))
    records=[]
    for i in range(2):
        meta,_,_=treatment();sha=hashlib.sha256(f'story-{i}'.encode()).hexdigest()
        meta['fingerprints']['sha256']=sha;meta['experiment_proof']['video_sha256']=sha
        meta['segments']=[dict(start=i*200,end=i*200+80)]
        meta.update(index=0,status='verified',sha256=sha)
        assert E.actual_profile(meta)==E.TREATMENT
        slug=f'T{i}';state['dispatched'].append(dict(slug=slug,source_url=f'https://example.test/{slug}'))
        records.append(dict(slug=slug,artifact_id=100+i,parts=[meta]))
    for i,(clock,hour) in enumerate([('18:00:00',14),('21:55:00',21)]):
        now[0]=stamp(clock);request=F.inventory_catchup_request(state,payload(records,now[0]),now[0])
        assert request['makeup_slot']==f'2026-11-15 {hour}'
        entry=next(e for e in state['dispatched'] if e['slug']==request['batch_slug'])
        record=next(r for r in records if r['slug']==entry['slug']);meta=record['parts'][0]
        receipt=dict(status='published',bvid=f'BVactual{i}',ts=now[0],fingerprints=meta['fingerprints'],
            publishing_experiment=E.receipt(meta,now[0],request['makeup_slot']))
        F.record_publication_slot(state,receipt,now[0],makeup_slot=request['makeup_slot'])
        F.mark_part_processed(entry,0);state['published'][entry['slug']]=dict(parts=[receipt],parts_total=1)
    assert state['daily_publish']['count']==F.MAX_PUBLISH_PER_DAY==4
    result=E.daily_requirement(state,now[0]);assert result['actual_treatment']==2 and result['today_passed']
    assert F.inventory_catchup_request(state,payload(records,now[0]),now[0]) is None
