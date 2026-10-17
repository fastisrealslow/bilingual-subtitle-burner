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


def test_story_copy_evidence_cannot_override_source_or_baseline(monkeypatch,tmp_path):
    meta,cues,_=treatment();evidence=meta['experiment_proof']['selection']
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.BASELINE)
    with pytest.raises(ValueError,match='绑定本段原文'):
        P.copywrite(cues,[0],'林园','访谈','',tmp_path,story_evidence=evidence)
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',E.TREATMENT)
    with pytest.raises(ValueError,match='绑定本段原文'):
        P.copywrite(cues,[0],'林园','访谈','',tmp_path,
                    story_evidence={**evidence,'detail_quote':'原文没有这个事实'})


def test_existing_verified_long_mothers_can_supply_once_without_mutating_stock(monkeypatch):
    monkeypatch.setattr(F.time,'time',lambda:stamp('2026-10-17T18:00:00'))
    monkeypatch.setattr(F,'source_inventory',lambda *a:dict(inventory_fresh=True))
    monkeypatch.setattr(F,'inventory_record_current',lambda _:True)
    monkeypatch.setattr(F,'inventory_publication_error',lambda *a:None)
    entry=dict(slug='old',source_url='https://www.bilibili.com/video/BVtest',ts=1)
    state=dict(dispatched=[entry]);old=deepcopy(state)
    record=dict(slug='old',run_id=123,parts=[dict(index=0,status='verified')])
    stock=dict(artifacts=[record])
    item=dict(page_url=entry['source_url'],extra=dict(duration=1800))
    assert list(F.experiment_reuse_candidates(state,stock,[item]))==[(entry,record)]
    assert state==old
    assert not list(F.experiment_reuse_candidates(state,stock,[{**item,'extra':dict(duration=1199)}]))
    assert not list(F.experiment_reuse_candidates(state,stock,[{**item,'extra':dict(duration=1800,no_reprint=1)}]))
    assert not list(F.experiment_reuse_candidates(state,stock,[{**item,'extra':dict(duration=1800,source_role='reference')}]))
    state['dispatched'].append(dict(slug='new',experiment_parent_slug='old',source_url=entry['source_url']))
    assert not list(F.experiment_reuse_candidates(state,stock,[item]))
    state['dispatched'].pop();record['parts'][0]['status']='rejected'
    assert not list(F.experiment_reuse_candidates(state,stock,[item]))


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


def test_real_model_selects_sentence_ids_and_program_extracts_exact_quotes():
    cues=[dict(text='我当年买了这家公司。后来我去看过它的产品。我就一直拿着。')]
    picks=[dict(start=0,end=0)]
    prompt,schema=E.selection_request(cues,picks)
    assert set(schema['properties'])=={'candidate_0'} and '不输出摘录文本' in prompt
    verdict=dict(candidate_0=dict(kind='personal_story',subject_sentence=0,event_sentence=0,detail_sentence=1))
    choice=E.validate_choices(cues,picks,verdict)[0]['story_selection']
    assert choice['event_quote']=='我当年买了这家公司。'
    assert choice['detail_quote']=='后来我去看过它的产品。'
    verdict['candidate_0']['detail_sentence']=99
    with pytest.raises(ValueError,match='sentence ID'):E.validate_choices(cues,picks,verdict)
    verdict['candidate_0']['kind']='skip'
    assert E.validate_choices(cues,picks,verdict)==[]


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


def test_scene_cover_uses_original_selected_pixels_not_small_tracked_window():
    assert P.cover_sample_context('mother.mp4','tracked-632x470.mp4',150,240,'crop=1280:720',
        natural_scene=True)==('mother.mp4',150,240,'crop=1280:720')
    assert P.cover_sample_context('mother.mp4','tracked-632x470.mp4',150,240,
        'crop=1280:720')==('tracked-632x470.mp4',0,90,'')


@pytest.mark.parametrize('profile',[E.BASELINE,E.TREATMENT])
def test_story_cannot_escape_into_static_portrait_stage_renderer(monkeypatch,tmp_path,profile):
    import stage_context
    class StageReached(Exception):pass
    class SceneReached(Exception):pass
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE',profile)
    monkeypatch.setattr(P,'automatic_only',lambda:False)
    monkeypatch.setattr(P,'argument_record_for_render',lambda *a:{})
    monkeypatch.setattr(P,'_download_speaker_reference',lambda *a:None)
    monkeypatch.setattr(P,'_local_face_models',lambda:None)
    def stage(*a,**kw):raise StageReached()
    def scene(*a,**kw):raise SceneReached()
    monkeypatch.setattr(stage_context,'plan',stage)
    monkeypatch.setattr(P,'reviewed_native_cleanup',scene)
    with pytest.raises(SceneReached if profile==E.TREATMENT else StageReached):
        P._produce_one('source',tmp_path,tmp_path,[dict(start=0,end=40,text='原文')],
            '林园','访谈','',False,1920,1080,'',prefer_live_video=True,
            preselected_picks=[dict(start=0,end=0,story_selection=dict(kind='company_case'))])


def test_weekly_long_mother_can_supply_verified_stories_without_consuming_its_full_interview():
    meta,_,_=treatment();meta['resolution']=dict(width=1080,height=1920)
    entry=dict(weekly_full_week='2026-W42',editorial_profile=E.TREATMENT)
    assert F.content_fits_slot(meta,entry,stamp('2026-10-18T21:00:00'))
    assert not F.content_fits_slot(meta,entry,stamp('2026-10-18T16:00:00'))
    assert not F.content_fits_slot({**meta,'editorial_profile':E.BASELINE},entry,
                                 stamp('2026-10-18T21:00:00'))


def test_recovery_and_runtime_packaging_keep_profile_module():
    from package_code import SOURCES
    assert 'publishing_experiment.py' in SOURCES
    config=F.production_config()
    assert config['publishing_experiment_version']==E.VERSION
    assert len(config['publishing_experiment_sha256'])==64
    assert config['publishing_experiment']['treatment_slots']==[2,4]


def test_general_stock_full_does_not_hide_missing_treatment_supply(monkeypatch):
    import catchup_from_inventory as C
    monkeypatch.setattr(F.time,'time',lambda:stamp('2026-10-17T18:00:00'))
    monkeypatch.setattr(F,'catchup_deficit',lambda _:0)
    stock=dict(inventory_fresh=True,daily_mix_usable=12,verified_landscape=2,
               verified_portrait=10,experiment_ready=dict(portrait=0,landscape=0))
    assert C.inventory_action({},stock)=='dispatch-source-inventory'
    stock['experiment_ready']=dict(portrait=2,landscape=2)
    assert C.inventory_action({},stock) is None


def test_quality_recovery_preserves_requested_treatment(monkeypatch):
    calls=[]
    def github(method,path,data=None,**kwargs):
        if method=='GET':return dict(workflow_runs=[])
        calls.append(data);return {}
    monkeypatch.setattr(F,'gh',github)
    monkeypatch.setattr(F,'save_state',lambda _:None)
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    monkeypatch.setattr(F,'publisher_code_is_current',lambda:True)
    entry=dict(slug='story',source_url='https://example.com/video',editorial_profile=E.TREATMENT,
               output_layout='landscape')
    assert F._request_quality_reprocess({},entry,'story','retry')
    assert calls[0]['inputs']['editorial_profile']==E.TREATMENT
    assert calls[0]['inputs']['output_layout']=='landscape'


def test_makeup_prefers_actual_treatment_over_earlier_baseline_inventory(monkeypatch):
    now=stamp('2026-10-19T18:00:00')
    monkeypatch.setattr(F.time,'time',lambda:now)
    meta,_,_=treatment()
    baseline={**meta,'editorial_profile':E.BASELINE}
    st=dict(dispatched=[dict(slug=s,source_url='https://example.com/'+s)
                        for s in ('baseline','story')],published={},
            daily_publish=dict(date='2026-10-19',count=2,published_hours=[10,16]))
    payload=dict(quality_gate_version=F.QUALITY_GATE_VERSION,
        editorial_policy_version=F.editorial.VERSION,updated_at=now,artifacts=[
            dict(slug=s,artifact_id=i+1,parts=[dict(index=0,status='verified',sha256='b'*64,**m)])
            for i,(s,m) in enumerate([('baseline',baseline),('story',meta)])])
    request=F.inventory_catchup_request(st,payload,now)
    assert request['batch_slug']=='story' and request['makeup_slot']=='2026-10-19 14'


def test_daily_ledger_uses_receipts_not_search_index_for_publication_count():
    meta,_,_=treatment();pub=stamp('2026-10-19T14:00:00')
    report=E.observations([],{},state_for(meta,pub),pub+60)
    today=next(d for d in report['daily'] if d['date']=='2026-10-19')
    assert today['published']==1 and today['slots_due']==2
    assert report['samples']==[] and all(g['median'] is None for g in report['groups'])
    assert all(g['median'] is None for g in report['by_slot'])


def test_unresolved_upload_intent_is_visible_but_never_counted_as_published():
    pub=stamp('2026-10-19T14:00:00')
    state=dict(dispatched=[dict(slug='ambiguous',uploading=True,uploading_ts=pub,
                               upload_experiment=E.assignment(pub))])
    report=E.observations([],{},state,pub+25*3600)
    assert len(report['pending_uploads'])==1 and not report['receipts'] and not report['samples']
    assert all(d['published']==0 for d in report['daily'])


def test_transient_state_read_failure_cannot_erase_months_observations():
    meta,_,_=treatment();pub=stamp('2026-10-19T14:00:00')
    first=E.observations([dict(bvid='BVsample',pubdate=pub,play=100)],{},state_for(meta,pub),pub+25*3600)
    missing=E.observations([],dict(publishing_experiment=first),{},pub+30*3600)
    assert missing['receipts']==first['receipts'] and missing['samples']==first['samples']


def paired_versions():
    clip,_,_=treatment()
    family=E.family_metadata(clip['source_sha256'],1800)
    clip.update(long_story_family=family,segments=[dict(start=100,end=140)])
    full=dict(long_story_family=family,source_sha256=clip['source_sha256'],content_type='full_interview',
              fingerprints=dict(sha256='d'*64),segments=[dict(start=0,end=1800)])
    return clip,full


def test_explicit_full_and_story_pair_is_symmetric_not_a_blanket_dedup_bypass():
    clip,full=paired_versions()
    assert E.full_clip_pair(clip,full) and E.full_clip_pair(full,clip)
    assert not E.full_clip_pair(clip,clip) and not E.full_clip_pair(full,full)
    assert not E.full_clip_pair(clip,{**full,'fingerprints':clip['fingerprints']})
    assert not E.full_clip_pair(clip,{**full,'source_sha256':'e'*64})
    assert not E.full_clip_pair(clip,{**full,'long_story_family':None})
    assert not E.full_clip_pair(clip,{**full,'fingerprints':{}})
    assert not E.full_clip_pair({**clip,'fingerprints':{}},full)
    assert E.full_clip_pair({**clip,'fingerprints':{}},full,preflight=True)
    assert not E.full_clip_pair({**clip,'segments':[dict(start=1700,end=1900)]},full)
    assert not E.full_clip_pair({**clip,'editorial_profile':E.BASELINE},full)


def test_full_partner_is_filtered_but_repeated_story_and_unknown_history_still_block():
    clip,full=paired_versions()
    old_full={**full,'bvid':'BVfull','source_segments':full['segments']}
    state=dict(published=dict(mother=dict(source_url='same-source',parts=[old_full])))
    filtered=E.comparison_state(clip,state)
    assert not filtered['published'] and state['published']
    assert F.editorial.source_reuse_error(clip,'same-source',filtered) is None
    old_clip={**clip,'bvid':'BVstory','source_segments':clip['segments']}
    state['published']['mother']['parts'].append(old_clip)
    filtered=E.comparison_state(clip,state)
    assert filtered['published']['mother']['parts']==[old_clip]
    assert F.editorial.source_reuse_error(clip,'same-source',filtered)
    old_full.pop('segments');old_full.pop('source_segments')
    assert not E.full_clip_pair(clip,old_full)


def test_inventory_counts_paired_full_and_story_but_not_duplicate_story(monkeypatch):
    clip,full=paired_versions();now=stamp('2026-10-19T10:00:00')
    monkeypatch.setattr(F.time,'time',lambda:now)
    clip['resolution']=dict(width=1080,height=1920)
    full.update(title='完整访谈',render_mode='direct',resolution=dict(width=1920,height=1080))
    for p in (clip,full):p['fingerprints']['transcript_ngrams']=[str(i) for i in range(10)]
    state=dict(dispatched=[dict(slug='mother',weekly_full_week='2026-W43',
                               editorial_profile=E.TREATMENT,source_url='same-source')],published={})
    payload=dict(quality_gate_version=F.QUALITY_GATE_VERSION,editorial_policy_version=F.editorial.VERSION,
        updated_at=now,artifacts=[dict(slug='mother',artifact_id=1,parts=[dict(index=i,status='verified',
            sha256=p['fingerprints']['sha256'],**p) for i,p in enumerate([clip,full,clip])])])
    stock=F.source_inventory(state,payload)
    assert stock['verified_weekly_full']==1 and stock['verified_portrait']==1
    assert stock['experiment_ready']['portrait']==1
    assert E.comparison_state(clip,dict(published={}))==dict(published={})


def test_slow_upload_keeps_original_slot_without_creating_a_second_due_post():
    actual=stamp('2026-10-19T15:10:00');receipt=dict(bvid='BVslow',status='published',ts=actual)
    state=dict(published={})
    F.record_publication_slot(state,receipt,actual,planned_slot='2026-10-19 14')
    assert state['daily_publish']['published_hours']==[14]
    assert F.slot_published(state,stamp('2026-10-19T14:00:00'))
    assert not F.slot_published(state,stamp('2026-10-19T16:00:00'))
    assert not state.get('makeup_receipts')
    # The immutable receipt still owns the original slot if a daily summary
    # has to be reconstructed later. It never occupies another upload hour.
    state=dict(published=dict(mother=dict(parts=[receipt])))
    assert F.slot_published(state,stamp('2026-10-19T14:00:00'))
    assert not F.slot_published(state,stamp('2026-10-19T15:00:00'))
