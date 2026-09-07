"""Stock and cache regressions: a job/link must never masquerade as a clip."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import zipfile
from urllib.error import HTTPError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import mother_asr_cache
import source_supply
fc=source_supply.fc


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


def test_retired_recognizer_cannot_pass_on_metadata_flags():
    error=fc.artifact_quality_error(dict(quality_gate_version=fc.QUALITY_GATE_VERSION,asr_model='sensevoice'))
    assert 'CPU' in error and 'SenseVoice' in error


def test_mix_limit_and_used_parts_reduce_real_reserve():
    parts=[dict(index=i,status='verified',render_mode='live_video_card' if i<10 else 'audio_card') for i in range(20)]
    state=dict(dispatched=[dict(slug='mother',published_parts=0)],published={})
    assert fc.source_inventory(state,payload(parts))['daily_mix_usable']==12
    state['dispatched'][0]['published_parts']=5
    assert fc.source_inventory(state,payload(parts))['daily_mix_usable']==6
    p=payload(parts);p['updated_at']=1
    assert fc.source_inventory(state,p)['daily_mix_usable']==0


def test_quarantined_and_failed_mothers_do_not_count(monkeypatch):
    state=dict(dispatched=[dict(slug='mother')],published={})
    p=payload([dict(index=0,status='verified',render_mode='live_video_card')])
    monkeypatch.setattr(fc,'REVIEW_PAUSED_SLUGS',{'mother'})
    assert fc.source_inventory(state,p)['verified_live']==0


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


def test_compare_and_swap_conflict_does_not_dispatch(monkeypatch):
    def gh(method,path,*a,**kw):
        raise HTTPError(path,404 if method=='GET' else 409,'conflict',None,None)
    monkeypatch.setattr(fc,'gh',gh)
    assert fc.dispatch_handler()['admission_busy']==1


def test_stock_target_stops_source_expansion(monkeypatch):
    monkeypatch.setattr(fc,'load_state',lambda:dict(dispatched=[],published={}))
    monkeypatch.setattr(fc,'_collect_source_rejections',lambda st:0)
    monkeypatch.setattr(fc,'source_inventory',lambda st:dict(daily_mix_usable=12))
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
