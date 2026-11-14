"""Daily delivery persists without restarting or contaminating the old cohort."""
from copy import deepcopy
from datetime import datetime,timedelta
import hashlib
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan'),str(ROOT/'linyuan/fc')]
import publishing_experiment as E
import index as F
from daily_publication_audit import experiment_receipts,experiment_stock_check
from visual_selection import candidate_order,story_hint


def clock(value):return datetime.fromisoformat(value).replace(tzinfo=E.CST).timestamp()


def part(day,hour=14,**changes):
    key=f'{day} {hour:02d}';plan=E.production_assignment(makeup_slot=key)
    return dict(status='published',bvid='BV'+key,ts=clock(day+f'T{hour:02d}:05:00'),
        fingerprints=dict(sha256=hashlib.sha256(key.encode()).hexdigest()),
        publishing_experiment=dict(plan,actual=E.TREATMENT,compliant=True),**changes)


def state(parts):return dict(published=dict(batch=dict(parts=parts)))


def test_frozen_comparison_and_new_daily_policy_have_different_identities():
    stamp=clock('2026-11-17T21:00:00')
    assert E.assignment(stamp) is None and not E.preparing(stamp)
    plan=E.production_assignment(stamp)
    assert E.production_preparing(stamp) and plan['planned']==E.TREATMENT
    assert plan['delivery_policy_id']==E.DELIVERY_POLICY_ID
    assert 'experiment_id' not in plan and 'version' not in plan
    assert E.EXPERIMENT_ID=='field-story-30d-20261018' and E.SELECTION_REVISION==2026102201
    assert E.END_EXCLUSIVE=='2026-11-17' and E.VERSION==2026101801


@pytest.mark.parametrize('day',['2026-11-14','2026-11-17','2026-12-01'])
def test_alternating_daily_slots_are_kept_before_and_after_comparison(day):
    plans=[E.production_assignment(clock(day+f'T{h:02d}:00:00')) for h in E.HOURS]
    assert [p['planned'] for p in plans]==[E.BASELINE,E.TREATMENT,E.BASELINE,E.TREATMENT]
    assert [p['slot_number'] for p in plans]==[1,2,3,4]
    for plan in plans:assert E.delivery_receipt_matches(plan)


def test_future_receipt_is_not_registered_as_a_new_old_cohort_sample():
    from test_linyuan_publishing_experiment import treatment
    meta,_,_=treatment();before=deepcopy(meta);now=clock('2026-11-17T21:20:00')
    proof=E.receipt(meta,now)
    assert proof['actual']==E.TREATMENT and proof['compliant'] is True and meta==before
    row=part('2026-11-17',21);row['publishing_experiment']=proof
    frozen=E.observations([],{},state([row]),now)
    assert frozen['receipts']==[] and frozen['samples']==[]
    assert frozen['experiment_id']=='field-story-30d-20261018'


def test_zero_or_one_true_treatment_cannot_pass_even_when_stock_is_full():
    now=clock('2026-11-14T22:00:00');ready=dict(landscape=4,portrait=4)
    for rows in [[],[part('2026-11-14')]]:
        result=E.operational_status(state(rows),ready,now)
        assert result['alert']=='daily_treatment_gap' and result['status']=='needs_attention'
        assert result['daily_requirement']['gap_by_now']==2-len(rows)


def test_two_distinct_same_day_deliveries_are_required_but_do_not_prove_stability():
    rows=[part('2026-11-14',14),part('2026-11-14',21)];before=deepcopy(rows)
    result=E.daily_requirement(state(rows),clock('2026-11-14T22:00:00'))
    assert result['actual_treatment']==2 and result['today_passed']
    assert not result['stable_7_complete_days'] and rows==before


@pytest.mark.parametrize('change',[
    lambda p:p.update(status='uploading'),
    lambda p:p['publishing_experiment'].update(actual=E.BASELINE,compliant=False),
    lambda p:p['publishing_experiment'].update(experiment_id='other'),
    lambda p:p['publishing_experiment'].update(slot_number=1),
    lambda p:p.update(fingerprints={}),
    lambda p:p.update(ts=clock('2026-11-15T00:05:00')),
])
def test_pending_fallback_wrong_policy_missing_hash_and_cross_day_makeup_are_not_deliveries(change):
    row=part('2026-11-14');change(row)
    assert E.daily_requirement(state([row]),clock('2026-11-15T01:00:00'))['complete_days'][-1]['actual_treatment']==0


def test_duplicate_video_or_file_and_two_receipts_for_one_slot_do_not_fill_two_slots():
    a=part('2026-11-14');b=part('2026-11-14',21)
    for c in [deepcopy(a),{**b,'bvid':a['bvid']},{**b,'fingerprints':a['fingerprints']}]:
        result=E.daily_requirement(state([a,c]),clock('2026-11-14T22:00:00'))
        assert result['actual_treatment']<2 and not result['today_passed']


def test_stability_requires_seven_complete_days_without_clock_reset_or_passing_today_early():
    today=datetime(2026,11,24,tzinfo=E.CST);rows=[]
    for offset in range(1,8):
        day=(today-timedelta(days=offset)).date().isoformat()
        rows.extend(part(day,hour) for hour in (14,21))
    r=E.daily_requirement(state(rows),today.timestamp())
    assert r['stable_7_complete_days'] and len(r['complete_days'])==7
    assert not r['today_passed'] and r['expected_by_now']==0 and r['remaining_today']==2
    assert not E.daily_requirement(state(rows[1:]),today.timestamp())['stable_7_complete_days']


def test_stock_and_actual_receipts_keep_failing_after_the_old_comparison_end():
    now=datetime(2026,11,20,22,tzinfo=E.CST)
    assert experiment_receipts({},now)['active'] and not experiment_receipts({},now)['passed']
    assert not experiment_stock_check(dict(inventory_fresh=True,experiment_ready={}),now)['passed']
    assert E.production_deficits(dict(experiment_ready={}),{},[],now.timestamp())==dict(landscape=2,portrait=2)


def test_story_order_never_drops_a_candidate_or_moves_risky_pixels_ahead_of_clean_pixels():
    picks=[dict(editorial_rank=i,start=i,end=i) for i in range(4)];before=deepcopy(picks)
    images=[[dict(status='possible')],[dict(status='possible')],[dict(status='risk')],[dict(status='unknown')]]
    assert candidate_order(picks,images,[0,2,2,2])==[1,0,3,2]
    assert candidate_order(picks,images)==[0,1,3,2] and picks==before


def test_actual_source888_company_case_has_a_hint_without_altering_its_words_or_timestamps():
    cues=json.loads((ROOT/'tests/fixtures/linyuan_888_source_selection.json').read_text())['cues']
    before=deepcopy(cues)
    assert story_hint(cues,dict(start=76,end=87))>0 and cues==before
    for text in ['我们以后打算投资很多好公司。','投资应该长期坚持，因为企业的盈利很重要。']:
        assert story_hint([dict(start=0,end=80,text=text)],dict(start=0,end=0))==0


def test_known_small_original_never_uses_large_final_card_as_native_cover_evidence():
    sha='a'*64
    p=dict(status='verified',source_sha256=sha,mother_source_resolution=dict(width=790,height=470),
        resolution=dict(width=1080,height=1920))
    before=deepcopy(p)
    assert not F.experiment_mother_feasible(dict(parts=[p]),sha) and p==before
    assert F.experiment_mother_feasible(dict(parts=[p]),'b'*64)
    assert F.experiment_mother_feasible(dict(parts=[{**p,'mother_source_resolution':None}]),sha)
    assert F.experiment_mother_feasible(dict(parts=[{**p,'mother_source_resolution':dict(width=1920,height=1080)}]),sha)
