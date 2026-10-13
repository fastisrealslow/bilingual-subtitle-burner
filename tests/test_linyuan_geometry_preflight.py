"""Real failed source geometry; a preview can never approve an entire video."""
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
sys.path.insert(0,str(ROOT/'linyuan/fc'))
from live_tracking import crop_box,geometry_obstruction,preflight_geometry
import index as fc


def fixture():
    return json.loads((ROOT/'tests/fixtures/linyuan_0916_geometry.json').read_text())


def test_real_clean_windows_are_found_without_reducing_face_margins():
    data=fixture()
    for row in data['frames'][:-1]:
        face=row['face'];fx,fy,fw,fh=face
        x,y,w,h=crop_box(face,data['width'],data['height'],exclusions=data['marks'])
        assert w>=316 and h>=235
        assert x+8<=fx and fx+fw<=x+w-8
        assert y+max(8,fh*.22)<=fy and fy+fh<=y+h-2
        assert all(not(x<c*1280 and x+w>a*1280 and y<d*720 and y+h>b*720)
                   for a,b,c,d in data['marks'])


def test_real_later_frame_remains_rejected_despite_workable_opening():
    data=fixture();face=data['frames'][-1]['face']
    assert geometry_obstruction(face,1280,720,data['marks'])['source_mark']
    with pytest.raises(ValueError):crop_box(face,1280,720,exclusions=data['marks'])


def test_nonoverlapping_crop_cannot_silently_cut_off_the_head():
    assert geometry_obstruction((100,4,120,160),640,480)['required_region'][1]<0
    with pytest.raises(ValueError):crop_box((100,4,120,160),640,480)


class Capture:
    def __init__(self,readable=True):self.position=0;self.readable=readable;self.seeks=[]
    def set(self,prop,value):self.position=int(value);self.seeks.append(self.position);return True
    def read(self):return self.readable,np.full((720,1280,3),self.position%255,np.uint8)


def test_late_obstruction_is_recorded_before_encoding_and_decoder_is_rewound(tmp_path):
    data=fixture();cap=Capture();output=tmp_path/'tracked.mp4'
    def face(_):return data['frames'][0 if cap.position<3500 else -1]['face']
    with pytest.raises(ValueError,match='整段取景预检'):
        preflight_geometry(cap,2891,4178,30,face,data['marks'],output)
    assert cap.position==2891 and not output.exists()
    proof=json.loads(output.with_suffix('.json').read_text())
    assert proof['passed'] is False and proof['final_quality_approved'] is False
    assert proof['failure_source_time']>100
    assert (tmp_path/'tracked.evidence/preflight-failure.jpg').is_file()


@pytest.mark.parametrize('readable,face',[(True,None),(False,None),(True,(600,160,200,250))])
def test_no_obstruction_or_missing_observations_never_approve_output(tmp_path,readable,face):
    cap=Capture(readable)
    result=preflight_geometry(cap,120,600,30,lambda _:face,[],tmp_path/'tracked.mp4')
    assert result['outcome']=='requires_full_verification'
    assert not result['final_quality_approved'] and cap.position==120
    assert len(result['samples'])==12


def test_repaired_geometry_recovers_once_with_original_raw_evidence(monkeypatch):
    entry=dict(slug='source',failed=True,source_quality_rejected=True,ts=100,
        source_url='source',source_check_attempts=2,last_error='原画无法通过真人画面清理门禁')
    state=dict(dispatched=[entry],published={},rejected=[dict(slug='source')])
    run=dict(id=35064242491,conclusion='failure',head_sha='old')
    monkeypatch.setattr(fc,'gh',lambda *a,**k:dict(files=[dict(filename='linyuan/live_tracking.py')]))
    monkeypatch.setattr(fc,'save_state',lambda *a:None)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    assert fc._recover_changed_production_rule(state,entry,run)
    assert entry['source_check_run_id']==run['id'] and entry['source_check_attempts']==2
    assert state['rejected']==[dict(slug='source')]
    entry['failed']=True
    assert not fc._recover_changed_production_rule(state,entry,run)


def test_uniform_source_top_bar_is_avoided_without_cutting_face(tmp_path):
    class BarCapture(Capture):
        def read(self):
            frame=np.full((484,856,3),120,np.uint8)
            frame[:34]=0
            return True,frame
    cap=BarCapture();face=(400,98,128,160)
    result=preflight_geometry(cap,17200,1200,30,lambda _:face,[],tmp_path/'tracked.mp4')
    assert result['source_border_exclusions']==[[0,0,1,34/484]]
    assert result['source_border_geometry']['crop']==[856,450,0,34]
    x,y,w,h=crop_box(face,856,484,exclusions=result['source_border_exclusions'])
    assert y>=34 and y+max(8,face[3]*.22)<=face[1]
    assert min(w,h)>=235 and x+8<=face[0] and face[0]+face[2]<=x+w-8
    assert cap.position==17200 and not result['final_quality_approved']


def test_intermittent_dark_region_is_not_measured_as_constant_padding(tmp_path):
    class ChangingCapture(Capture):
        calls=0
        def read(self):
            self.calls+=1
            frame=np.full((484,856,3),120,np.uint8)
            if self.calls<12:frame[:34]=0
            return True,frame
    result=preflight_geometry(ChangingCapture(),0,600,30,lambda _:None,[],tmp_path/'tracked.mp4')
    assert result['source_border_exclusions']==[]
    assert not result['final_quality_approved']


def test_large_dark_region_does_not_authorize_aggressive_crop(tmp_path):
    class DarkCapture(Capture):
        def read(self):return True,np.zeros((484,856,3),np.uint8)
    result=preflight_geometry(DarkCapture(),0,600,30,lambda _:None,[],tmp_path/'tracked.mp4')
    assert result['source_border_exclusions']==[]
    assert result['source_border_geometry']['applied'] is False
    assert not result['final_quality_approved']


def test_corner_text_does_not_hide_verified_black_strip_near_subject(tmp_path):
    class CornerTextCapture(Capture):
        def read(self):
            frame=np.full((484,856,3),120,np.uint8)
            frame[:34]=0
            frame[16:34,:100]=150
            return True,frame
    face=(400,98,128,160)
    result=preflight_geometry(CornerTextCapture(),0,1200,30,lambda _:face,[],tmp_path/'tracked.mp4')
    corridor=result['source_border_geometry']['subject_corridor']
    assert corridor['crop'][3]==34 and len(corridor['source_frames'])==12
    assert corridor['exclusions'][0][0]>0 and corridor['exclusions'][0][2]<1
    x,y,w,h=crop_box(face,856,484,exclusions=result['source_border_exclusions'])
    assert y>=34 and y+max(8,face[3]*.22)<=face[1]


@pytest.mark.parametrize('reason,published,changed',[
    ('人物身份不匹配',False,True),('原画无法通过真人画面清理门禁',True,True),
    ('原画无法通过真人画面清理门禁',False,False)])
def test_geometry_recovery_does_not_reopen_unrelated_or_published_material(monkeypatch,reason,published,changed):
    entry=dict(slug='source',failed=True,ts=100,source_url='source',last_error=reason)
    state=dict(dispatched=[entry],published={'source':{'bvid':'BVreceipt'}} if published else {})
    monkeypatch.setattr(fc,'gh',lambda *a,**k:dict(files=[dict(filename='linyuan/live_tracking.py' if changed else 'log.html')]))
    assert not fc._recover_changed_production_rule(state,entry,dict(id=8,conclusion='failure',head_sha='old'))
    assert entry['failed']
