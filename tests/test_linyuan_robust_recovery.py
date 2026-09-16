"""Actual September 15 failures: safe download, boundaries and durable recovery."""
import copy
import io
import json
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
sys.path.insert(0,str(ROOT/'linyuan/fc'))
import ci_fetch_bilibili as fetch
import source_selection as selection
import index as fc


class Response(io.BytesIO):
    def __init__(self,data,headers,status=206):
        super().__init__(data);self.headers=headers;self.status=status


@pytest.mark.parametrize('changed',[False,True])
def test_etag_change_requires_full_prefix_proof_and_never_splices_different_bytes(tmp_path,monkeypatch,changed):
    old=b'a'*20000+b'b'*10000
    payload=(b'z'*20000+b'y'*10000) if changed else old
    out=tmp_path/'track';part=out.with_suffix('.part')
    part.write_bytes(old[:15000])
    out.with_suffix('.download.json').write_text(json.dumps(dict(
        identity=dict(source='mother',paths=['/track']),total=len(payload),etag='old')))
    ranges=[]
    class Opener:
        def open(self,req,timeout):
            r=req.headers.get('Range')
            if r:
                a,b=r.split('=')[1].split('-');a=int(a);b=int(b) if b else len(payload)-1
            else:a,b=0,len(payload)-1
            ranges.append((a,b))
            return Response(payload[a:b+1],{'Content-Range':f'bytes {a}-{b}/{len(payload)}',
                'Content-Length':str(b-a+1),'ETag':'new'},206 if r else 200)
    monkeypatch.setattr(fetch.time,'sleep',lambda _:None)
    fetch.download_one(Opener(),['https://cdn.test/track'],'mother',out)
    assert out.read_bytes()==payload
    assert (0,14999) in ranges  # All retained bytes were verified, not a sample.
    assert ((0,29999) in ranges)==changed


def test_prefix_probe_timeout_preserves_checkpoint_and_is_bounded(tmp_path,monkeypatch):
    out=tmp_path/'track';part=out.with_suffix('.part');part.write_bytes(b'a'*15000)
    out.with_suffix('.download.json').write_text(json.dumps(dict(
        identity=dict(source='mother',paths=['/track']),total=30000,etag='old')))
    calls=[]
    class Opener:
        def open(self,req,timeout):
            calls.append(req.headers['Range'])
            if req.headers['Range']=='bytes=0-14999':raise TimeoutError('temporary')
            return Response(b'a'*15000,{'Content-Range':'bytes 15000-29999/30000','ETag':'new'})
    monkeypatch.setattr(fetch.time,'sleep',lambda _:None)
    with pytest.raises(RuntimeError):fetch.download_one(Opener(),['https://cdn.test/track'],'mother',out)
    assert part.read_bytes()==b'a'*15000
    assert len(calls)==6 and not out.exists()


@pytest.mark.parametrize('number,interval',[(917,(141.88,294.84)),(923,(492.12,673.72)),
                                          (942,(0.16,124.92))])
def test_real_zero_candidate_mothers_recover_only_complete_source_intervals(number,interval):
    data=json.loads((ROOT/f'tests/fixtures/linyuan_{number}_source_selection.json').read_text())
    cues=data['cues'];original=copy.deepcopy(cues);diag={}
    picks=selection.select(cues,whole_source=True,limit=6,diagnostics=diag)
    assert len(picks)==1
    p=picks[0]
    assert (cues[p['start']]['start'],cues[p['end']]['end'])==interval
    assert selection.boundary_error(cues,p) is None
    assert cues==original and diag['accepted']==1
    if number==917:
        text=''.join(c['text'] for c in cues[p['start']:p['end']+1])
        assert '高股息' in text and '科技浪潮' not in text


def mp4_box(kind, payload, extended=False):
    if extended:
        return b'\0\0\0\1'+kind+(16+len(payload)).to_bytes(8,'big')+payload
    return (8+len(payload)).to_bytes(4,'big')+kind+payload


def movie_header(duration, version=0, scale=1000):
    width=8 if version else 4
    # Nonzero creation/modification times catch the old v1 offset bug.
    payload=bytes([version,0,0,0])+(123456789).to_bytes(width,'big')*2
    payload+=scale.to_bytes(4,'big')+duration.to_bytes(width,'big')
    return mp4_box(b'mvhd',payload+b'\0'*80)


@pytest.mark.parametrize('version,extended',[(0,False),(0,True),(1,False),(1,True)])
def test_long_trailing_moov_is_measured_without_scanning_media(tmp_path,version,extended):
    # #935's real 2421.262-second file has a 2,658,363-byte trailing moov.
    # Both 500 KB end scans missed mvhd and falsely returned zero seconds.
    path=tmp_path/'long.mp4'
    fake=movie_header(9000000)
    path.write_bytes(mp4_box(b'ftyp',b'isom0000')+
        mp4_box(b'mdat',fake+b'\0'*600000,extended)+
        mp4_box(b'moov',movie_header(2421262,version)+mp4_box(b'free',b'\0'*600000),extended))
    assert fc.mp4_duration(path)==pytest.approx(2421.262)


@pytest.mark.parametrize('payload',[
    mp4_box(b'mdat',movie_header(200000)),  # mvhd bytes outside moov are not evidence.
    mp4_box(b'moov',mp4_box(b'mvhd',b'\0'*8)),
    mp4_box(b'moov',movie_header(200000,scale=0)),
    mp4_box(b'moov',movie_header(2**32-1)),
    mp4_box(b'moov',movie_header(2**64-1,version=1)),
    mp4_box(b'moov',movie_header(200000,version=2)),
    (999999).to_bytes(4,'big')+b'moov'+movie_header(200000),
    b'\0\0\0\1moov\0',
    b'\0\0\0\4moov'+movie_header(200000),
])
def test_invalid_or_unknown_movie_duration_cannot_satisfy_publication(tmp_path,payload):
    path=tmp_path/'bad.mp4';path.write_bytes(payload)
    assert fc.mp4_duration(path)==0


def test_short_movie_keeps_actual_duration_despite_long_duration_bytes_in_media(tmp_path):
    path=tmp_path/'short.mp4'
    path.write_bytes(mp4_box(b'mdat',movie_header(2421262))+
        b'\0\0\0\0moov'+movie_header(100000))
    assert fc.mp4_duration(path)==100


def test_zero_candidates_explain_failure_without_using_another_question_for_duration():
    cues=[dict(start=0,end=5,text='您怎么看消费股？'),
          dict(start=5,end=95,text='消费企业要看需求，长期增长取决于产品。'),
          dict(start=95,end=160,text='您怎么看科技股？')]
    diag={}
    assert selection.select(cues,whole_source=True,diagnostics=diag)==[]
    assert diag['outcome']=='no_structural_candidate'
    assert diag['candidates'][0]['reason']=='duration_outside_120_330'


def test_known_fixed_failure_recovers_once_and_keeps_receipts_and_counters(monkeypatch):
    entry=dict(slug='mother',failed=True,source_quality_rejected=True,ts=100,
        source_url='https://www.bilibili.com/video/BVexample',last_error='原文中未找到满足120秒的连续候选',
        source_check_attempts=2)
    state=dict(dispatched=[entry],published={'other':{'bvid':'existing'}},rejected=[{'slug':'mother'}])
    run=dict(id=8,conclusion='failure',head_sha='old',updated_at='2026-09-15T10:00:00Z')
    monkeypatch.setattr(fc,'gh',lambda *a,**kw:dict(files=[dict(filename='linyuan/source_selection.py')]))
    monkeypatch.setattr(fc,'save_state',lambda *a:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    assert fc._recover_changed_production_rule(state,entry,run)
    assert not entry['failed'] and entry['source_check_run_id']==8
    assert entry['source_check_attempts']==2 and state['published']['other']['bvid']=='existing'
    entry['failed']=True
    assert not fc._recover_changed_production_rule(state,entry,run)


@pytest.mark.parametrize('reason,published,changed',[
    ('动态取景目标人物匹配不足80%',False,True),
    ('原文中未找到满足120秒的连续候选',True,True),
    ('原文中未找到满足120秒的连续候选',False,False)])
def test_unrelated_quality_published_or_unchanged_rules_never_reopen(monkeypatch,reason,published,changed):
    entry=dict(slug='mother',failed=True,ts=100,source_url='source',last_error=reason)
    state=dict(dispatched=[entry],published={'mother':{'bvid':'BVdone'}} if published else {})
    run=dict(id=8,conclusion='failure',head_sha='old')
    monkeypatch.setattr(fc,'gh',lambda *a,**k:dict(files=[dict(filename='linyuan/source_selection.py' if changed else 'log.html')]))
    assert not fc._recover_changed_production_rule(state,entry,run)
    assert entry['failed']


def test_download_failure_report_keeps_transfer_root_cause_when_embed_also_fails(tmp_path,monkeypatch):
    report=tmp_path/'source_quality.json'
    monkeypatch.setattr(fetch,'opener',lambda:object())
    monkeypatch.setattr(fetch,'via_view',lambda *a:123)
    monkeypatch.setattr(fetch,'via_pagelist',lambda *a:123)
    monkeypatch.setattr(fetch,'playurl',lambda *a:dict(video=['cdn'],audio=[],height=720))
    def download(*a,**k):raise RuntimeError('CDN content mismatch')
    def embed(*a,**k):raise RuntimeError('embed 页没有 __playinfo__')
    monkeypatch.setattr(fetch,'download',download)
    monkeypatch.setattr(fetch,'via_embed',embed)
    monkeypatch.setattr(sys,'argv',['fetch','--url','https://www.bilibili.com/video/BVtest',
        '--out',str(tmp_path/'video.mp4'),'--failure-report',str(report)])
    with pytest.raises(SystemExit):fetch.main()
    data=json.loads(report.read_text())
    assert 'CDN content mismatch' in data['reason']
    assert data['retryable'] and len(data['strategy_failures'])==3
    assert [x['stage'] for x in data['strategy_failures']]==['download','download','resolve']


def test_preview_crop_uses_reference_identity_instead_of_larger_right_hand_host(tmp_path,monkeypatch):
    import cv2,numpy as np
    from types import SimpleNamespace
    from live_tracking import reference_faces
    ref=tmp_path/'reference.png'
    cv2.imwrite(str(ref),np.zeros((64,64,3),dtype=np.uint8))
    guest=np.array([20,30,100,120]+[0]*10+[.99],dtype=np.float32)
    host=np.array([400,20,200,220]+[0]*10+[.99],dtype=np.float32)
    detector=SimpleNamespace(setInputSize=lambda *a:None,
        detect=lambda f:(None,np.array([guest]) if f.mean()==0 else np.array([guest,host])))
    recognizer=SimpleNamespace(alignCrop=lambda f,face:face,feature=lambda f:f[0],
        match=lambda a,b,*args:1. if a==b else 0.)
    monkeypatch.setattr(cv2,'FaceDetectorYN',SimpleNamespace(create=lambda *a,**k:detector))
    monkeypatch.setattr(cv2,'FaceRecognizerSF',SimpleNamespace(create=lambda *a,**k:recognizer))
    boxes=reference_faces([np.ones((480,640,3),dtype=np.uint8)]*3,ref,('detector','recognizer'))
    assert len(boxes)==3 and all(tuple(box)==tuple(guest[:4]) for box in boxes)
