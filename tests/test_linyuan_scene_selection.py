"""Physical scene text and complete answers survive conservative picture ranking."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
from scene_text import classify,localized
from visual_selection import candidate_order,rank
from source_selection import select


def actual_samples():
    root=ROOT/'tests/fixtures/scene_text_0916'
    data=json.loads((root/'observations.json').read_text())
    return [dict(frame=cv2.imread(str(root/s['image'])),boxes=s['boxes']) for s in data['samples']]


def test_actual_wall_text_moves_with_scene_but_all_three_watermarks_remain():
    samples=actual_samples();kept,proof=classify(samples)
    assert len(proof)>=3
    assert any(row['frame']==0 and row['box']==samples[0]['boxes'][3] for row in proof)
    for sample,boxes in zip(samples,kept):
        # Measured gold two-line mark and upper-right owner/platform mark.
        for box in sample['boxes'][:3]:assert box in boxes
    for row in proof:
        assert not row['final_quality_approved'] and len(row['evidence'])>=2
        assert all(e['background_inliers']>=8 for e in row['evidence'])


def test_static_background_is_inconclusive_not_a_clean_verdict():
    sample=actual_samples()[0];kept,proof=classify([sample]*3)
    assert proof==[] and kept==[sample['boxes']]*3


def test_scene_classifier_outage_keeps_every_ocr_box(monkeypatch,tmp_path):
    import produce_cn as p
    import scene_text
    frame=np.zeros((360,640,3),np.uint8)
    class Capture:
        def get(self,*a):return 30
        def set(self,*a):pass
        def read(self):return True,frame
        def release(self):pass
    box=[[100,288],[500,288],[500,308],[100,308]]
    monkeypatch.setattr(cv2,'VideoCapture',lambda *a:Capture())
    monkeypatch.setattr(p,'_ocr',lambda:lambda *a,**k:([box],None))
    def unavailable(*a):raise RuntimeError('scene matching interrupted')
    monkeypatch.setattr(scene_text,'classify',unavailable)
    src=tmp_path/'test.mp4'
    coverage=p.ocr_row_coverage(src,frames=3,strict=True)
    assert coverage[80:85]==[1.0]*5
    proof=p.scene_text_evidence(src)[0]
    assert proof['error'] and proof['scene_text']==[] and not proof['final_quality_approved']


def test_independently_moving_overlay_has_no_support_from_static_background():
    sample=actual_samples()[0];samples=[]
    for shift in (0,8,16):
        box=[[180+shift,180],[260+shift,188],[258+shift,204],[178+shift,196]]
        samples.append(dict(frame=sample['frame'],boxes=[box]))
    kept,proof=classify(samples)
    assert proof==[] and all(len(b)==1 for b in kept)


@pytest.mark.parametrize('box',[
    [[100,300],[500,300],[500,320],[100,320]], # subtitle safe area
    [[100,10],[500,35],[500,70],[100,45]], # editorial title
    [[480,10],[620,15],[620,35],[480,30]], # corner logo
    [[160,180],[240,180],[240,195],[160,195]], # no perspective evidence
])
def test_titles_subtitles_and_uncertain_text_never_become_scene_text(box):
    assert not localized(box,640,360)


def test_picture_ranking_keeps_whole_answers_and_unknown_backups():
    picks=[dict(start=i*10,end=i*10+9,editorial_rank=i) for i in range(8)]
    before=json.dumps(picks)
    rows=[[dict(status='risk',text_rows=30)] for _ in picks]
    rows[6]=[dict(status='possible',text_rows=2)]*6
    rows[7]=[dict(status='unknown')]
    order=candidate_order(picks,rows)
    assert order[:2]==[6,7] and sorted(order)==list(range(8))
    assert json.dumps(picks)==before


def test_incomplete_picture_sample_cannot_outrank_complete_clean_sample():
    picks=[dict(editorial_rank=0),dict(editorial_rank=1)]
    rows=[[dict(status='possible'),dict(status='unknown')],[dict(status='possible')]*6]
    assert candidate_order(picks,rows)==[1,0]


def test_preview_outage_preserves_all_candidates_and_writes_no_approval(tmp_path):
    cues=[dict(start=0,end=145,text='完整原话')]
    picks=[dict(start=0,end=0)]
    result=rank('missing.mp4',cues,picks,tmp_path,'missing.jpg',('missing','missing'),None,None)
    assert result==picks
    proof=json.loads((tmp_path/'visual-selection.json').read_text())
    assert proof['error'] and proof['final_quality_approved'] is False


def test_selector_exposes_later_complete_answers_without_borrowing_time():
    cues=[]
    for i in range(8):
        t=i*150
        cues.extend([dict(start=t,end=t+10,text='您对医药股有什么判断？'),
            dict(start=t+10,end=t+80,text='医药行业需求随着老龄化增长，我们长期持有这些企业。'),
            dict(start=t+80,end=t+145,text='但是投资仍然有风险，价格和需求都要看，不能只看过去。')])
    assert len(select(cues,whole_source=True,limit=6))==6
    picks=select(cues,whole_source=True,limit=None)
    assert len(picks)==8
    assert [(p['start'],p['end']) for p in picks]==[(i*3,i*3+2) for i in range(8)]


def test_automatic_pipeline_tries_seventh_answer_after_six_picture_failures(monkeypatch,tmp_path):
    import produce_cn as p
    import visual_selection
    import curated_editorial
    import stock_upgrade_plan
    src=tmp_path/'source.mp4';src.touch();cues=[]
    for i in range(8):
        t=i*150
        cues.extend([dict(start=t,end=t+10,text='您对医药股有什么判断？'),
            dict(start=t+10,end=t+80,text='医药行业需求随着老龄化增长，我们长期持有这些企业。'),
            dict(start=t+80,end=t+145,text='但是投资仍然有风险，价格和需求都要看，不能只看过去。')])
    monkeypatch.setattr(sys,'argv',['produce','--source',str(src),'--slug','backup',
        '--split-highlights','--require-live-video','--prefer-live-video'])
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true');monkeypatch.delenv('PUBLICATION_STATE_PATH',raising=False)
    monkeypatch.setattr(p,'BASE',tmp_path);monkeypatch.setattr(p,'TEXT_BACKEND','local')
    monkeypatch.setattr(p,'load_key',lambda:'')
    monkeypatch.setattr(p,'run_source_quality_gate',lambda *a:dict(passed=True,
        resolution=dict(width=1280,height=720),visual_identity={}))
    monkeypatch.setattr(p,'transcribe',lambda *a:cues)
    monkeypatch.setattr(curated_editorial,'source_ranges',lambda *a:None)
    monkeypatch.setattr(stock_upgrade_plan,'source_ranges',lambda *a:None)
    monkeypatch.setattr(p,'_download_speaker_reference',lambda *a:'reference')
    monkeypatch.setattr(p,'_local_face_models',lambda:('a','b'))
    monkeypatch.setattr(p,'_ocr',lambda:None)
    monkeypatch.setattr(visual_selection,'rank',lambda src,cues,picks,*a:picks)
    attempted=[]
    def produce(src,work,out,segment,*args,**kwargs):
        attempted.append((segment[0]['start'],segment[-1]['end']))
        if len(attempted)<=6:raise p.VisualQualityError('画面不能清理')
        return dict(title='原话',final='final_'+str(len(attempted))+'.mp4',duration_sec=145,
            resolution=dict(width=1280,height=720),render_mode='live_video',
            segments=[dict(start=segment[0]['start'],end=segment[-1]['end'])])
    monkeypatch.setattr(p,'produce_part_with_budget',produce)
    assert p.main()==0
    assert attempted==[(i*150,i*150+145) for i in range(8)]
    proof=json.loads((tmp_path/'deliver/backup/batch_report.json').read_text())
    assert proof['accepted']==2 and len(proof['rejected'])==6


def test_scene_fix_recovery_is_bounded_and_keeps_original_evidence(monkeypatch):
    sys.path.insert(0,str(ROOT/'linyuan/fc'));import index as fc
    entry=dict(slug='source',source_url='url',failed=True,ts=1,
               last_error='整段取景预检：来源角标覆盖必须保留的人脸',source_check_attempts=3)
    state=dict(dispatched=[entry],published={})
    monkeypatch.setattr(fc,'gh',lambda *a,**k:dict(files=[dict(filename='linyuan/visual_selection.py')]))
    monkeypatch.setattr(fc,'save_state',lambda *a:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    run=dict(id=123,head_sha='old',conclusion='failure')
    assert fc._recover_changed_production_rule(state,entry,run)
    assert entry['source_check_run_id']==123 and entry['source_check_attempts']==3
    entry['failed']=True
    assert not fc._recover_changed_production_rule(state,entry,run)
