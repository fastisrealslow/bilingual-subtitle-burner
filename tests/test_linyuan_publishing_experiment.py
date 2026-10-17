"""Fixed slots, grounded evidence, real cohorts and quantity-preserving fallback."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan'),str(ROOT/'linyuan/fc')]
import publishing_experiment as E
import produce_cn as P
import index as F


def stamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=E.CST).timestamp()


def treatment():
    text='我当年买了这家公司。我看过它的产品，后来一直拿着。'
    cues=[dict(start=0,end=40,text=text)]
    choice=dict(index=0,kind='personal_story',subject_quote='这家公司',
                event_quote='我当年买了这家公司',detail_quote='我看过它的产品')
    selection=E.validate_choices(cues,[dict(start=0,end=0)],dict(choices=[choice]))[0]['story_selection']
    meta=dict(title='林园：我看过产品，买入后一直拿着',source_sha256='a'*64,
        fingerprints=dict(sha256='b'*64),cover_person_image_source='verified_source_frame',
        cover_person_image_verified=True,cover_proof=dict(sha256='c'*64,style='scene',
        no_added_text=True,feed_safe_face=True),resolution=dict(width=1920,height=1080),
        render_mode='direct',duration_sec=40,content_type='short_argument')
    return E.attach_proof(meta,cues,selection,1800),cues,choice


@pytest.mark.parametrize('hour,number,planned',[(10,1,E.BASELINE),(14,2,E.TREATMENT),
                                             (16,3,E.BASELINE),(21,4,E.TREATMENT)])
def test_four_fixed_slots_not_successful_publication_count(hour,number,planned):
    plan=E.assignment(stamp(f'2026-10-18T{hour:02d}:00:00'))
    assert plan['slot_number']==number and plan['planned']==planned
    assert F.MAX_PUBLISH_PER_DAY==4 and F.PUBLISH_HOURS==set(E.HOURS)


def test_thirty_complete_beijing_days_and_makeup_keeps_original_slot():
    assert E.assignment(stamp('2026-10-17T21:00:00')) is None
    assert E.assignment(stamp('2026-11-16T21:59:00'))['planned']==E.TREATMENT
    assert E.assignment(stamp('2026-11-17T10:00:00')) is None
    assert E.assignment(stamp('2026-10-18T23:00:00'),'2026-10-18 14')['slot_number']==2
    assert E.assignment(stamp('2026-10-18T11:00:00')) is None
    assert E.assignment(stamp('2026-10-18T14:00:00'),'2026-10-18 99') is None


def test_sunday_full_interview_moves_to_control_third_slot_only_during_trial():
    full=dict(content_type='full_interview',render_mode='direct')
    short=dict(content_type='short_argument',render_mode='direct',resolution=dict(width=1080,height=1920))
    assert F.content_fits_slot(full,{},stamp('2026-10-18T16:00:00'))
    assert not F.content_fits_slot(full,{},stamp('2026-10-18T21:00:00'))
    assert F.content_fits_slot(short,{},stamp('2026-10-18T21:00:00'))
    assert F.content_fits_slot(full,{},stamp('2026-11-22T21:00:00'))


def test_profile_is_explicit_and_changes_copy_cache_identity(monkeypatch):
    monkeypatch.delenv('LINYUAN_EDITORIAL_PROFILE',raising=False)
    old=P._copy_style_identity('林园')
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    new=P._copy_style_identity('林园')
    assert old!=new and new['editorial_profile']==E.TREATMENT
    assert E.workflow_input(dict(editorial_profile=E.TREATMENT))==dict(editorial_profile=E.TREATMENT)
    assert E.workflow_input({})=={}
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE','typo')
    with pytest.raises(ValueError):E.profile()


@pytest.mark.parametrize('change',[dict(source_sha256='d'*64),dict(title='林园：另一个标题'),
    dict(fingerprints=dict(sha256='d'*64)),dict(cover_person_image_source='authority_reference'),
    dict(cover_proof=dict(style='editorial',sha256='c'*64,no_added_text=True,feed_safe_face=True))])
def test_old_cover_changed_media_or_changed_title_cannot_claim_treatment(change):
    meta,_,_=treatment()
    assert E.proof_error(meta) is None
    assert E.proof_error({**meta,**change})
    assert E.actual_profile({**meta,**change})==E.BASELINE


def test_short_mother_or_changed_retained_text_cannot_claim_long_story():
    meta,cues,_=treatment();selection=meta['experiment_proof']['selection']
    with pytest.raises(ValueError):E.attach_proof(meta,cues,selection,1199)
    with pytest.raises(ValueError):E.attach_proof(meta,[dict(text='其他原文')],selection,1800)


@pytest.mark.parametrize('change',[dict(index=8),dict(index=-1),dict(kind='generic_summary'),
    dict(event_quote='原文里没有这个事情'),dict(detail_quote='我当年买了这家公司')])
def test_classifier_cannot_invent_or_cross_borrow_evidence(change):
    _,cues,choice=treatment()
    with pytest.raises(ValueError):E.validate_choices(cues,[dict(start=0,end=0)],dict(choices=[{**choice,**change}]))


def test_empty_story_selection_is_not_a_quality_rejection_or_fake_treatment():
    assert E.validate_choices([dict(text='长期投资很重要')],[dict(start=0,end=0)],dict(choices=[]))==[]
    baseline=dict(title='林园：长期持有',fingerprints=dict(sha256='a'*64))
    r=E.receipt(baseline,stamp('2026-10-18T14:00:00'))
    assert r['planned']==E.TREATMENT and r['actual']==E.BASELINE and not r['compliant']
    assert r['reason']=='qualified_stock_fallback'


def test_inventory_prefers_correct_group_but_keeps_safe_fallback():
    meta,_,_=treatment()
    baseline={**meta,'editorial_profile':E.BASELINE}
    record=dict(slug='source',artifact_id=10,parts=[dict(index=i,status='verified',**m)
        for i,m in enumerate([baseline,meta])])
    entry=dict(slug='source')
    assert F.inventory_part_index(entry,10,[record],{},stamp('2026-10-19T14:00:00'))==1
    # 10:00 is portrait, so use portrait variants to test the control slot.
    for p in record['parts']:p['resolution']=dict(width=1080,height=1920)
    assert F.inventory_part_index(entry,10,[record],{},stamp('2026-10-19T10:00:00'))==0
    record['parts']=record['parts'][1:]
    assert F.inventory_part_index(entry,10,[record],{},stamp('2026-10-19T10:00:00'))==1


def test_ready_stock_and_actual_running_jobs_bound_experiment_admission():
    now=stamp('2026-10-17T18:00:00')
    stock=dict(experiment_ready=dict(portrait=1,landscape=1))
    st=dict(dispatched=[dict(slug='a',editorial_profile=E.TREATMENT,output_layout='landscape')])
    runs=[dict(display_title='中文源出片 · a',status='in_progress')]*2
    assert E.production_deficits(stock,st,runs,now)==dict(portrait=1,landscape=0)
    assert E.production_deficits(stock,st,[],stamp('2026-11-17T18:00:00'))==dict(portrait=0,landscape=0)
    assert E.production_deficits({},st,[],now)==dict(portrait=0,landscape=0)
    assert E.long_candidate(dict(extra=dict(duration=1200)))
    assert not E.long_candidate(dict(extra=dict(duration=30)))
    assert not E.long_candidate(dict(title='完整访谈'))


def state_for(meta,now,content_type=None):
    r=E.receipt(meta,now)
    if content_type:r['content_type']=content_type
    return dict(published=dict(source=dict(source_url='https://example.com/source',parts=[dict(
        bvid='BVsample',title=meta.get('title'),ts=now,publishing_experiment=r)])))


def test_same_age_observations_preserve_first_value_and_missing_is_null():
    meta,_,_=treatment();pub=stamp('2026-10-19T14:00:00');state=state_for(meta,pub)
    item=dict(bvid='BVsample',pubdate=pub,play=100,like=4)
    first=E.observations([item],{},state,pub+25*3600)
    second=E.observations([{**item,'play':500}],dict(publishing_experiment=first),state,pub+30*3600)
    assert first['samples']==second['samples']
    groups=first['groups']
    assert next(g for g in groups if g['planned']==E.TREATMENT and g['window']==24)['median']==100
    assert next(g for g in groups if g['window']==72)['median'] is None
    assert E.observations([],dict(publishing_experiment=first),state,pub+30*3600)['samples']==first['samples']
    late=E.observations([{**item,'play':700}],{},state,pub+169*3600)
    assert [r['window'] for r in late['samples']]==[168]


def test_fallback_and_weekly_full_are_visible_but_not_mixed_into_primary_groups():
    meta,_,_=treatment();pub=stamp('2026-10-18T21:00:00')
    item=dict(bvid='BVsample',pubdate=pub,play=100)
    full=E.observations([item],{},state_for(meta,pub,'full_interview'),pub+25*3600)
    assert full['samples'] and all(g['count']==0 for g in full['groups'])
    fallback=E.observations([item],{},state_for({},pub),pub+25*3600)
    assert fallback['fallback_count']==1 and all(g['count']==0 for g in fallback['groups'])
    assert E.observations([],{}, {},stamp('2026-11-17T09:00:00'))['review_due']


def test_requested_profile_without_proof_is_rejected_by_publication_gate():
    assert '实验' in F.artifact_quality_error(dict(editorial_profile=E.TREATMENT))


def test_recovery_and_runtime_packaging_keep_profile_module():
    from package_code import SOURCES
    assert 'publishing_experiment.py' in SOURCES
    config=F.production_config()
    assert config['publishing_experiment_version']==E.VERSION
    assert len(config['publishing_experiment_sha256'])==64
