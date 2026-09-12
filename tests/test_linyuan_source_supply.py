"""Stock and cache regressions: a job/link must never masquerade as a clip."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
from datetime import datetime,timezone
import zipfile
import io
from types import SimpleNamespace
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import mother_asr_cache
import source_supply
fc=source_supply.fc


def test_source_yield_counts_actual_unused_files_and_preserves_failure_stages():
    from source_outcomes import audit
    url='https://www.bilibili.com/video/BVarchive?p=2'
    entries=[dict(slug='old',source_url=url,failed=True,last_error='unclear transcript',
                  failure_stage='editorial-or-render'),
             dict(slug='new',source_url=url,published_parts=1),
             dict(slug='service',source_url=url+'0',last_error='timeout',
                  failure_stage='quality-service',failed=False)]
    parts=[dict(index=i,status='verified',render_mode='live_video_card',duration_sec=130,
                source_sha256='mother',sha256='file'+str(i)) for i in range(2)]
    record=dict(slug='new',parts=parts)
    state=dict(published={'old':dict(source_url=url,parts=[
        dict(bvid='A',source_sha256='mother',source_segments=[dict(start=0,end=140)]),
        dict(bvid='B',source_sha256='mother',source_segments=[dict(start=120,end=180)]),
    ])})
    result=audit(state,[record,record],entries,fc.processed_part_indices,set())
    row=next(r for r in result['mothers'] if r['source_key']=='BVarchive:p2')
    assert row['verified_available']==1 and row['verified_seconds']==130
    assert row['used_source_seconds']==180 and row['historical_receipts']==2
    group=result['families']['BVarchive']
    assert group['attempted_mothers']==2 and group['terminal_editorial-or-render']==1
    assert 'terminal_quality-service' not in group


def payload(parts):
    return dict(quality_gate_version=fc.QUALITY_GATE_VERSION,
                editorial_policy_version=fc.editorial.VERSION,updated_at=time.time(),
                artifacts=[dict(slug='mother',parts=parts)])


def test_jobs_and_unknown_links_are_not_ready_stock():
    state=dict(dispatched=[dict(slug='mother',ts=time.time())],published={})
    assert fc.source_inventory(state,payload([]))['daily_mix_usable']==0
    audit=source_supply.audit_materials([dict(source='bilibili_search',url='https://www.bilibili.com/video/BV1'),
        dict(source='competitor_reference',url='https://www.bilibili.com/video/BV2')])
    assert audit['bilibili_search']['duration_unknown']==1
    assert audit['competitor_reference']['reference_only']==1


def test_six_slots_include_several_source_families_without_losing_candidates():
    candidates=[dict(key=f'a{i}',author='same-uploader',extra=dict(collection_title='old archive',bvid='BVA'))
                for i in range(8)]
    candidates += [dict(key=f'b{i}',author='same-uploader',extra=dict(collection_title='other event',bvid='BVB'))
                   for i in range(3)]
    candidates += [dict(key=f'c{i}',author='official',extra={}) for i in range(2)]
    first=fc.diversify_source_candidates(candidates,6)
    assert [c['key'] for c in first]==['a0','a1','b0','b1','c0','c1']
    all_rows=fc.diversify_source_candidates(candidates,20)
    assert len(all_rows)==len(candidates)==len({c['key'] for c in all_rows})
    assert len(fc.diversify_source_candidates(candidates[:8],6))==6


def test_retired_recognizer_cannot_pass_on_metadata_flags():
    error=fc.artifact_quality_error(dict(quality_gate_version=fc.QUALITY_GATE_VERSION,asr_model='sensevoice'))
    assert 'CPU' in error and 'SenseVoice' in error
    error=fc.artifact_quality_error(dict(quality_gate_version=fc.QUALITY_GATE_VERSION,asr_model='qwen3',
        editorial_review=dict(standalone_opening=True,natural_ending=True,summary='流畅摘要')))
    assert '识别疑点复核' in error


def test_raw_evidence_reuse_does_not_import_review_or_another_source(tmp_path):
    from prepare_asr_runtime import restore_raw_qwen_evidence
    report=dict(source_video_sha256='mother',device='cpu',networking_during_inference=False,
        model_id='Qwen/Qwen3-ASR-0.6B',model_revision='asr',chunks=[dict(core_start=0,core_end=20)],
        alignment=dict(model_id='Qwen/Qwen3-ForcedAligner-0.6B',model_revision='align'))
    archive=tmp_path/'evidence.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('_tmp/asr_raw_chunks.json',json.dumps([report]))
        z.writestr('_tmp/editorial_review.json','{"passed":true}')
    revisions=dict(asr='asr',aligner='align')
    assert not restore_raw_qwen_evidence(archive,tmp_path/'wrong','different',revisions)
    assert restore_raw_qwen_evidence(archive,tmp_path/'right','mother',revisions)
    assert (tmp_path/'right/0/aligned.json').exists()
    assert not list((tmp_path/'right').rglob('editorial_review.json'))


def test_mix_limit_and_used_parts_reduce_real_reserve():
    parts=[dict(index=i,status='verified',render_mode='live_video_card' if i<10 else 'audio_card') for i in range(20)]
    state=dict(dispatched=[dict(slug='mother',published_parts=0)],published={})
    assert fc.source_inventory(state,payload(parts))['daily_mix_usable']==10
    state['dispatched'][0]['published_parts']=5
    assert fc.source_inventory(state,payload(parts))['daily_mix_usable']==5
    p=payload(parts);p['updated_at']=1
    assert fc.source_inventory(state,p)['daily_mix_usable']==0


def test_last_audio_card_uses_today_live_receipts_without_inflating_reserve():
    today=time.strftime('%Y-%m-%d',time.gmtime(time.time()+8*3600))
    state=dict(dispatched=[dict(slug='mother')],published={},
               daily_publish=dict(date=today,count=5,live_video_count=5))
    data=payload([dict(index=0,status='verified',render_mode='audio_card')])
    stock=fc.source_inventory(state,data)
    assert stock['daily_mix_usable']==0
    assert stock['publishable_now']==0
    state['daily_publish']['count']=6
    assert fc.source_inventory(state,data)['publishable_now']==0
    state['daily_publish']['date']='2020-01-01'
    assert fc.source_inventory(state,data)['publishable_now']==0


def test_quarantined_and_failed_mothers_do_not_count(monkeypatch):
    state=dict(dispatched=[dict(slug='mother')],published={})
    p=payload([dict(index=0,status='verified',render_mode='live_video_card')])
    monkeypatch.setattr(fc,'REVIEW_PAUSED_SLUGS',{'mother'})
    assert fc.source_inventory(state,p)['verified_live']==0


def test_audio_first_cannot_bury_live_or_count_a_finished_part_twice():
    entry=dict(slug='mother',published_parts=0,parts_total=3)
    state=dict(dispatched=[entry],published={})
    parts=[dict(index=i,status='verified',render_mode='audio_card' if i==0 else 'live_video_card')
           for i in range(3)]
    data=payload(parts)
    data['artifacts'][0]['artifact_id']=123
    choose=lambda daily:fc.inventory_part_index(entry,123,data['artifacts'],daily)
    assert choose({})==1
    fc.mark_part_processed(entry,1)
    assert entry['published_parts']==0
    assert choose({})==2
    assert fc.source_inventory(state,data)['verified_live']==1
    assert source_supply.inventory_counts(data['artifacts'],state)['verified_live']==1
    fc.mark_part_processed(entry,2)
    assert entry['published_parts']==0
    assert fc._has_unpublished_part(entry,state)
    assert choose({}) is None
    assert choose(dict(live_video_count=3)) is None
    fc.mark_part_processed(entry,0)
    assert entry['published_parts']==3
    assert not fc._has_unpublished_part(entry,state)
    assert not fc.source_inventory(state,data)['verified_audio_card']


def test_rejected_early_part_does_not_hide_inspected_later_part():
    entry=dict(slug='mother',published_parts=0)
    record=dict(slug='mother',artifact_id=123,parts=[
        dict(index=0,status='rejected',render_mode='live_video_card'),
        dict(index=1,status='verified',render_mode='live_video_card')])
    assert fc.inventory_part_index(entry,123,[record],{})==1
    # An inspection of a different artifact must not select unverified indices.
    assert fc.inventory_part_index(entry,456,[record],{})==0


def test_obsolete_reviews_reuse_raw_asr_but_never_destroy_usable_stock():
    entry=dict(slug='mother')
    state=dict(dispatched=[entry])
    reason='CPU Qwen 成片尚未通过逐字开场/结尾与识别疑点复核，不能仅凭旧摘要放行'
    record=dict(slug='mother',artifact_id=123,parts=[dict(index=0,status='rejected',reason=reason)])
    inventory=dict(artifacts=[record])
    assert list(fc.obsolete_review_candidates(state,inventory))==[(entry,record)]
    record['parts'].append(dict(index=1,status='verified'))
    assert not list(fc.obsolete_review_candidates(state,inventory))
    fc.mark_part_processed(entry,1)
    assert list(fc.obsolete_review_candidates(state,inventory))==[(entry,record)]
    entry['quality_reprocess_artifact_id']=123
    assert not list(fc.obsolete_review_candidates(state,inventory))
    entry.pop('quality_reprocess_artifact_id')
    entry['quality_retries']=2
    assert not list(fc.obsolete_review_candidates(state,inventory))


def test_inventory_event_replenishes_before_the_next_publish_window(monkeypatch):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
    import catchup_from_inventory as catchup
    monkeypatch.setattr(catchup.fc,'catchup_deficit',lambda state:2)
    stock=dict(inventory_fresh=True,daily_mix_usable=0)
    assert catchup.inventory_action({},stock)=='dispatch-source-inventory'
    stock['daily_mix_usable']=1
    assert catchup.inventory_action({},stock)=='publish-catchup'
    monkeypatch.setattr(catchup.fc,'catchup_deficit',lambda state:0)
    assert catchup.inventory_action({},stock)=='dispatch-source-inventory'
    stock.update(daily_mix_usable=12,verified_landscape=2)
    assert catchup.inventory_action({},stock) is None
    stock.update(inventory_fresh=False,daily_mix_usable=0)
    assert catchup.inventory_action({},stock) is None


def test_old_contiguous_progress_remains_compatible_with_sparse_progress():
    entry=dict(published_parts=2)
    fc.mark_part_processed(entry,4)
    fc.mark_part_processed(entry,4)
    assert entry['published_parts']==2
    fc.mark_part_processed(entry,2)
    assert fc.processed_part_indices(entry)=={0,1,2,4}
    assert entry['published_parts']==3
    fc.mark_part_processed(entry,3)
    assert entry['published_parts']==5


def test_publisher_uses_later_live_part_and_keeps_earlier_audio_artifact(monkeypatch):
    today=time.strftime('%Y-%m-%d',time.gmtime(time.time()+8*3600))
    entry=dict(slug='mother',published_parts=0,ts=time.time(),source_url='')
    state=dict(dispatched=[entry],published={},daily_publish=dict(date=today,count=0))
    metas=[dict(final=f'final_{i}.mp4',title=f'林园：完整观点{i}',
                render_mode='audio_card' if i==0 else 'live_video_card') for i in range(2)]
    reserve=payload([dict(index=i,status='verified',render_mode=m['render_mode'])
                     for i,m in enumerate(metas)])
    reserve['artifacts'][0]['artifact_id']=123
    calls=[]
    def gh(method,path,*args,**kwargs):
        calls.append((method,path))
        if method=='GET' and path.startswith('/actions/workflows/'):
            return dict(workflow_runs=[dict(id=456)])
        if method=='GET' and path=='/actions/runs/456/artifacts':
            return dict(artifacts=[dict(name='deliver-mother',id=123,expired=False,
                                       archive_download_url='https://example.test/archive')])
        if method=='GET' and path.startswith('/contents/'+fc.SOURCE_INVENTORY_KEY):
            return json.dumps(reserve).encode()
        raise AssertionError((method,path))
    def download(aid,index,dest):
        assert (aid,index)==(123,1)
        (dest/'meta.json').write_text(json.dumps(metas))
        (dest/'final_1.mp4').write_bytes(b'unit-test-video')
        return True
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc,'load_state',lambda:state)
    monkeypatch.setattr(fc,'save_state',lambda value:None)
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda value:0)
    monkeypatch.setattr(fc,'download_inventory_part',download)
    monkeypatch.setattr(fc,'mp4_duration',lambda path:180)
    monkeypatch.setattr(fc.editorial,'metadata_error',lambda *a:None)
    monkeypatch.setattr(fc,'artifact_quality_error',lambda *a:None)
    monkeypatch.setattr(fc,'artifact_subtitle_error',lambda *a:None)
    monkeypatch.setattr(fc,'find_content_duplicate',lambda *a:None)
    monkeypatch.setattr(fc,'find_recent_topic',lambda *a,**kw:None)
    monkeypatch.setattr(fc,'bili_find_duplicate',lambda *a:None)
    monkeypatch.setitem(sys.modules,'biliup',SimpleNamespace(__file__='unit-test-only'))
    monkeypatch.setattr(fc.subprocess,'run',lambda *a,**kw:SimpleNamespace(
        returncode=0,stdout='BV1234567890',stderr=''))
    assert fc.publish_handler(dict(force_publish=True))['published']==1
    assert entry['published_parts']==0
    assert entry['processed_part_indices']==[1]
    assert state['published']['mother']['parts'][0]['part_index']==1
    assert state['daily_publish']['count']==1
    assert state['daily_publish']['live_video_count']==1
    assert not any(method=='DELETE' for method,path in calls)


def test_admission_lease_stops_duplicate_dispatch(monkeypatch):
    lease=base64.b64encode(json.dumps(dict(owner='other',expires_at=time.time()+500)).encode()).decode()
    calls=[]
    def gh(method,path,*a,**kw):
        calls.append(method)
        return dict(sha='old',content=lease)
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc,'_dispatch_admitted',lambda *a: (_ for _ in ()).throw(AssertionError('duplicate admission')))
    assert fc.dispatch_handler()['admission_busy']==1
    assert calls==['GET']


def test_lease_release_retries_a_ref_conflict_with_fresh_ownership(monkeypatch):
    calls=[]
    def gh(method,path,payload=None,**kwargs):
        calls.append(method)
        if method=='GET':
            return dict(sha=str(len(calls)),content=base64.b64encode(json.dumps(
                dict(owner='own',expires_at=time.time()+60)).encode()).decode())
        if calls.count('PUT')==1:raise HTTPError(path,409,'concurrent ref update',None,None)
        assert payload['sha']=='3'
        return {}
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc.time,'sleep',lambda delay:None)
    assert fc.release_pipeline_lease('own','dispatch') is True
    assert calls==['GET','PUT','GET','PUT']


def test_failed_release_cannot_clear_a_new_owners_lock(monkeypatch):
    def gh(method,path,*args,**kwargs):
        assert method=='GET'
        return dict(sha='new',content=base64.b64encode(json.dumps(
            dict(owner='another',expires_at=time.time()+60)).encode()).decode())
    monkeypatch.setattr(fc,'gh',gh)
    assert fc.release_pipeline_lease('old','dispatch') is False


def test_compare_and_swap_conflict_does_not_dispatch(monkeypatch):
    def gh(method,path,*a,**kw):
        raise HTTPError(path,404 if method=='GET' else 409,'conflict',None,None)
    monkeypatch.setattr(fc,'gh',gh)
    assert fc.dispatch_handler()['admission_busy']==1


def test_catchup_only_fills_current_slot_and_respects_daily_four():
    now=datetime(2026,9,7,2,30,tzinfo=timezone.utc).timestamp()
    assert fc.catchup_deficit(dict(daily_publish=dict(date='2026-09-07',count=0)),now)==1
    assert fc.catchup_deficit(dict(daily_publish=dict(date='2026-09-07',count=1)),now)==0
    late=datetime(2026,9,7,15,30,tzinfo=timezone.utc).timestamp()
    assert fc.catchup_deficit(dict(daily_publish=dict(date='2026-09-07',count=6)),late)==0
    assert fc.catchup_deficit(dict(daily_publish=dict(date='2026-09-06',count=22)),now)==1


def test_removed_windows_never_catch_up_and_one_slot_cannot_burst():
    stamp=lambda hour:datetime(2026,9,9,hour-8,30,tzinfo=timezone.utc).timestamp()
    state=dict(daily_publish=dict(date='2026-09-09',count=0))
    for hour in (12,19,22):
        assert not fc.is_regular_publish_hour(stamp(hour))
        assert fc.catchup_deficit(state,stamp(hour))==0
    for hour in (10,14,16,21):
        assert fc.catchup_deficit(state,stamp(hour))==1
    state['daily_publish'].update(count=1,published_hours=[16])
    assert fc.catchup_deficit(state,stamp(16))==0
    assert fc.catchup_deficit(state,stamp(21))==1
    state['daily_publish']['count']=4
    assert fc.catchup_deficit(state,stamp(21))==0


def test_pre_migration_receipts_occupy_their_existing_slot():
    now=datetime(2026,9,9,8,30,tzinfo=timezone.utc).timestamp()
    state=dict(published={'old':dict(parts=[dict(bvid='BVexisting',ts=now-60)])})
    assert fc.slot_published(state,now)
    assert fc.catchup_deficit(state,now)==0
    state['published']['old']['parts'][0]['ts']=now-86400
    assert not fc.slot_published(state,now)


def test_sunday_evening_is_reserved_for_full_interview_without_clip_fallback():
    sunday=datetime(2026,9,13,13,0,tzinfo=timezone.utc).timestamp()
    full=dict(content_type='full_interview')
    assert fc.content_fits_slot(full,now=sunday)
    assert not fc.content_fits_slot({},now=sunday)
    assert not fc.content_fits_slot(full,now=sunday-5*3600)
    assert fc.content_fits_slot({},now=sunday-5*3600)
    assert not fc.content_fits_slot({},dict(weekly_full_week='2026-W37'),sunday-5*3600)


def test_weekly_full_request_requires_long_complete_source_and_no_active_request():
    now=datetime(2026,9,9,8,0,tzinfo=timezone.utc).timestamp()
    candidate=dict(title='林园完整访谈',extra=dict(duration=3400))
    week=fc.weekly_full_request(candidate,{},now)
    assert week=='2026-W37'
    state=dict(dispatched=[dict(slug='full',weekly_full_week=week)])
    assert fc.weekly_full_request(candidate,state,now)==''
    state['dispatched'][0]['failed']=True
    assert fc.weekly_full_request(candidate,state,now)==week
    candidate['extra']['duration']=180
    assert fc.weekly_full_request(candidate,{},now)==''
    candidate.update(title='林园访谈切片',extra=dict(duration=1800))
    assert fc.weekly_full_request(candidate,{},now)==''


def test_publisher_lease_blocks_timer_and_inventory_race(monkeypatch):
    lease=base64.b64encode(json.dumps(dict(expires_at=time.time()+500)).encode()).decode()
    monkeypatch.setattr(fc,'gh',lambda *a,**kw:dict(content=lease,sha='owned'))
    result=fc.run_with_lease('publish',lambda:(_ for _ in ()).throw(AssertionError('duplicate upload')))
    assert result['publisher_busy']==1


def test_stock_target_stops_source_expansion(monkeypatch):
    monkeypatch.setattr(fc,'load_state',lambda:dict(dispatched=[],published={}))
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda st:0)
    monkeypatch.setattr(fc,'source_inventory',lambda st:dict(daily_mix_usable=12,verified_landscape=2))
    monkeypatch.setattr(fc,'gh',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('unnecessary new job')))
    assert fc._dispatch_admitted()['reserve_full']==1


def test_mother_cache_binds_actual_source_and_cues(tmp_path):
    raw=tmp_path/'source';raw.mkdir();dest=tmp_path/'target'
    cues=b'[{"start":0,"end":1,"text":"verified hypothesis"}]'
    (raw/'cues_raw.json').write_bytes(cues)
    (raw/'asr_cache.json').write_text(json.dumps(dict(identity=dict(source_sha256='mother'),cues_sha256=hashlib.sha256(cues).hexdigest())))
    (raw/'editorial_review.json').write_text('{"approved":true}')
    assert not mother_asr_cache.transfer(raw,dest,'different-mother')
    assert mother_asr_cache.transfer(raw,dest,'mother')
    assert not (dest/'editorial_review.json').exists()
    (raw/'cues_raw.json').write_bytes(b'edited text')
    assert not mother_asr_cache.transfer(raw,dest,'mother')


def test_mother_cache_restores_external_alignment_as_raw_evidence(tmp_path):
    raw=tmp_path/'source';raw.mkdir();dest=tmp_path/'target'
    cues=b'[{"start":0,"end":1,"text":"original words"}]'
    (raw/'cues_raw.json').write_bytes(cues)
    (raw/'asr_cache.json').write_text(json.dumps(dict(identity=dict(source_sha256='mother'),cues_sha256=hashlib.sha256(cues).hexdigest())))
    reports=[dict(source_video_sha256='mother',chunks=[dict(text='original words')])]
    (raw/'asr_raw_chunks.json').write_text(json.dumps(reports))
    assert mother_asr_cache.transfer(raw,dest,'mother')
    assert json.loads((dest/'qwen_cpu/0/aligned.json').read_text())==reports[0]
    (dest/'qwen_cpu/aligned.json').write_text(json.dumps(reports[0]))
    assert mother_asr_cache.transfer(raw,dest,'mother')
    assert len(list((dest/'qwen_cpu').rglob('aligned.json')))==1
    # No review approval or identity bypass is created by the raw transfer.
    assert not (dest/'editorial_review.json').exists()


def test_sparse_artifact_part_download_preserves_required_ass(tmp_path,monkeypatch):
    meta=[dict(final='final_3.mp4',cover='cover_3.jpg',subtitle_files=['subtitles_3.ass'])]
    def download(aid,path,**kw):
        assert aid==77
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('meta.json',json.dumps(meta))
            z.writestr('final_3.mp4',b'actual selected video')
            z.writestr('cover_3.jpg',b'cover')
            z.writestr('subtitles_3.ass',b'actual ASS evidence')
            z.writestr('unrelated.mp4',b'do not extract')
    monkeypatch.setattr(fc,'download_reviewed_zip',download)
    assert fc.download_inventory_part(77,0,tmp_path)
    assert (tmp_path/'subtitles_3.ass').read_bytes()==b'actual ASS evidence'
    assert not (tmp_path/'unrelated.mp4').exists()


def test_different_collection_episodes_survive_title_dedup():
    rows=[dict(title='林园完整访谈相同系列名称',extra=dict(bvid='BVseries',cid=i,page=i),
               page_url=f'https://www.bilibili.com/video/BVseries?p={i}') for i in [1,2]]
    assert len(fc.dedup_by_title(rows))==2
    assert fc.video_id_of(rows[1]['page_url'],'')=='BVseries:p2'


def test_reuse_allows_new_arguments_but_not_overlap_or_unknown_legacy():
    url='https://www.bilibili.com/video/BVsource?p=2'
    old=dict(bvid='published',status='published',source_segments=[dict(start=100,end=240)],source_sha256='same')
    st=dict(published=dict(old=dict(source_url=url,parts=[old])))
    fresh=dict(source_sha256='same',segments=[dict(start=250,end=400)])
    assert fc.editorial.source_reuse_error(fresh,url+'&spm_id_from=tracking',st) is None
    fresh['segments']=[dict(start=200,end=350)]
    assert '重叠' in fc.editorial.source_reuse_error(fresh,url,st)
    fresh['source_sha256']='changed'
    assert '哈希已改变' in fc.editorial.source_reuse_error(fresh,url,st)
    # Another collection page must not inherit the first page's timeline.
    assert fc.editorial.source_reuse_error(fresh,url.replace('p=2','p=3'),st) is None
    del old['source_segments']
    assert '缺少' in fc.editorial.source_reuse_error(fresh,url,st)


def test_cross_url_same_mother_and_same_batch_overlap_are_also_rejected():
    old=dict(bvid='old',source_sha256='same',source_segments=[dict(start=0,end=180)])
    st=dict(published=dict(batch=dict(source_url='other-url',parts=[old])))
    meta=dict(source_sha256='same',segments=[dict(start=20,end=170)])
    assert '重叠' in fc.editorial.source_reuse_error(meta,'new-url',st)


def test_unavailable_checker_or_report_does_not_blacklist_mother(monkeypatch):
    state=dict(dispatched=[dict(slug='new',ts=1)],published={},rejected=[])
    artifact=dict(id=7,name='source-reject-new',expired=False,archive_download_url='https://example.invalid/report')
    def gh(method,path,*a,**kw):
        assert method=='GET'
        return dict(workflow_runs=[dict(id=8)]) if '/workflows/' in path else dict(artifacts=[artifact])
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc,'save_state',lambda st:None)
    def unavailable(*a,**kw):raise OSError('temporary transfer failure')
    monkeypatch.setattr(fc,'download_reviewed_zip',unavailable)
    assert fc._collect_source_rejections(state)==0
    assert not state['dispatched'][0].get('failed') and state['rejected']==[]
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('source_quality.json',json.dumps(dict(reason='人物 VLM 校验不可用：格式错误',retryable=True)))
    monkeypatch.setattr(fc,'download_reviewed_zip',lambda aid,path,**kw:Path(path).write_bytes(data.getvalue()))
    assert fc._collect_source_rejections(state)==0
    entry=state['dispatched'][0]
    assert entry['source_check_retry_after']>time.time()
    assert entry['source_check_report_id']==7 and not entry.get('source_quality_rejected')


def test_legacy_empty_selection_report_is_not_a_quality_verdict(monkeypatch):
    state=dict(dispatched=[dict(slug='timeout',ts=1)],published={},rejected=[])
    artifact=dict(id=9,name='production-reject-timeout',expired=False)
    def gh(method,path,*a,**kw):
        assert method=='GET'
        return dict(workflow_runs=[dict(id=10)]) if '/workflows/' in path else dict(artifacts=[artifact])
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc,'save_state',lambda st:None)
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:
        z.writestr('batch_report.json',json.dumps(dict(accepted=0,rejected=[],retryable=False)))
    monkeypatch.setattr(fc,'download_reviewed_zip',lambda aid,path,**kw:Path(path).write_bytes(data.getvalue()))
    assert fc._collect_source_rejections(state)==0
    entry=state['dispatched'][0]
    assert not entry.get('failed') and state['rejected']==[]
    assert entry['source_check_retry_after']>time.time()


def test_stock_motion_proof_cannot_move_to_other_video(tmp_path):
    video=tmp_path/'clip.mp4';video.write_bytes(b'exact encoded bytes')
    sha=hashlib.sha256(video.read_bytes()).hexdigest()
    meta=dict(source_sha256='source',fingerprints=dict(sha256=sha))
    record=dict(status='verified',source_sha256='source',sha256=sha,
                motion=dict(version=2026091201,passed=True))
    assert fc.restore_inventory_motion(meta,video,record)
    video.write_bytes(b'other video')
    assert not fc.restore_inventory_motion(meta,video,record)
    video.write_bytes(b'exact encoded bytes')
    assert not fc.restore_inventory_motion(meta,video,{**record,'source_sha256':'other'})
