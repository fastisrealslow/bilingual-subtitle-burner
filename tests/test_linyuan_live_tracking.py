import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from live_tracking import crop_box,complete_face


@pytest.mark.parametrize('face',[(120,85,250,270),(390,90,170,220)])
def test_profile_geometry_keeps_head_and_chin_margins(face):
    assert complete_face(face,632,470)


@pytest.mark.parametrize('face',[(120,0,250,270),(120,100,250,380),(-15,60,250,270),(500,60,250,270)])
def test_clipped_head_chin_and_sides_remain_rejected(face):
    assert not complete_face(face,632,470)


def test_camera_cut_reframes_target_instead_of_staying_on_background():
    before=(770,220,340,340)
    after=(240,180,180,180)
    boxes=[crop_box(f,1920,1080) for f in (before,after)]
    assert boxes[0][0]>boxes[1][0]+200
    for face,(x,y,w,h) in zip((before,after),boxes):
        fx,fy,fw,fh=face
        assert 0<=x<x+w<=1920 and 0<=y<y+h<=1080
        assert x<fx and y<fy-fh*.18 and x+w>fx+fw and y+h>fy+fh
        assert abs(w/h-632/470)<.01


def test_measured_station_logo_is_excluded_without_cutting_wide_shot_head():
    # Actual #647 source-frame YuNet box and source-quality logo rectangle.
    face=(305.6,194.1,106,130.1)
    logo=(.153645833,.119444444,.195833333,.135185185)
    original=crop_box(face,1920,1080)
    corrected=crop_box(face,1920,1080,exclusions=[logo])
    assert original[1]<logo[3]*1080
    assert corrected[1]>logo[3]*1080
    assert corrected[1]<=face[1]-face[3]*.22


@pytest.mark.parametrize('initial_face',[True,False])
def test_mid_render_failure_keeps_exact_source_frames(tmp_path,monkeypatch,initial_face):
    import cv2
    import numpy as np
    import json
    from types import SimpleNamespace
    from live_tracking import render_tracked
    source=tmp_path/'source.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(160,120))
    for i in range(60):writer.write(np.full((120,160,3),i*3,dtype=np.uint8))
    writer.release()
    reference=tmp_path/'reference.png'
    cv2.imwrite(str(reference),np.zeros((120,160,3),dtype=np.uint8))
    face=np.array([[40,30,30,40,45,40,60,40,50,50,45,60,60,60,.99]],dtype=np.float32)

    class Detector:
        calls=0
        def setInputSize(self,size):pass
        def detect(self,frame):
            self.calls+=1
            return None,face if self.calls==1 or (initial_face and self.calls==2) else None

    detector=Detector()
    recognizer=SimpleNamespace(alignCrop=lambda frame,face:frame,
        feature=lambda frame:frame,match=lambda *args:1.)
    monkeypatch.setattr(cv2,'FaceDetectorYN',SimpleNamespace(create=lambda *args,**kwargs:detector))
    monkeypatch.setattr(cv2,'FaceRecognizerSF',SimpleNamespace(create=lambda *args:recognizer))
    output=tmp_path/'tracked.mp4'
    with pytest.raises(ValueError,match='缺少人脸'):
        render_tracked(source,1,4,output,reference,('detector','recognizer'))
    proof=json.loads(output.with_suffix('.json').read_text())
    assert proof['passed'] is False
    assert not output.exists()
    assert proof['failure_source_time']==pytest.approx(3.1 if initial_face else 1.)
    assert proof['encoded_frames']==(21 if initial_face else 0)
    assert proof['decoded_frames']==(22 if initial_face else 1)
    assert proof['consecutive_no_face_seconds']==pytest.approx(2.1 if initial_face else .1)
    directory=tmp_path/proof['evidence_directory']
    assert cv2.imread(str(directory/'failure.jpg')).shape==(120,160,3)
    assert proof['samples'] and all((directory/x['file']).is_file() for x in proof['samples'])


def test_interview_keeps_broll_frames_and_distinguishes_verified_participants(tmp_path,monkeypatch):
    import cv2,numpy as np,json
    from types import SimpleNamespace
    from live_tracking import render_tracked
    source=tmp_path/'interview.mp4';reference=tmp_path/'guest.png';host=tmp_path/'host.png'
    cv2.imwrite(str(reference),np.full((120,160,3),20,dtype=np.uint8))
    cv2.imwrite(str(host),np.full((120,160,3),100,dtype=np.uint8))
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(160,120))
    for n in range(100):writer.write(np.full((120,160,3),20 if n<40 else 100 if n<80 else 180+n-80,dtype=np.uint8))
    writer.release()
    face=np.array([[40,30,30,40,45,40,60,40,50,50,45,60,60,60,.99]],dtype=np.float32)
    detector=SimpleNamespace(setInputSize=lambda *a:None,
        detect=lambda frame:(None,None if frame.mean()>160 else face))
    recognizer=SimpleNamespace(alignCrop=lambda frame,f:frame,
        feature=lambda frame:0 if frame.mean()<60 else 1,
        match=lambda a,b,*args:1. if a==b else 0.)
    monkeypatch.setattr(cv2,'FaceDetectorYN',SimpleNamespace(create=lambda *a,**k:detector))
    monkeypatch.setattr(cv2,'FaceRecognizerSF',SimpleNamespace(create=lambda *a:recognizer))
    output=tmp_path/'tracked.mp4'
    proof=render_tracked(source,0,10,output,reference,('detector','recognizer'),
        context_crop=(0,0,160,100),participant_reference=host)
    assert proof['passed'] and proof['source_frames_preserved']
    assert (proof['matched_frames'],proof['other_face_frames'],proof['no_face_frames'])==(40,40,20)
    assert proof['verified_face_ratio']==.8
    assert [x['role'] for x in proof['roles']]==['guest','participant','source_illustration']
    cap=cv2.VideoCapture(str(output));cap.set(cv2.CAP_PROP_POS_FRAMES,90);ok,frame=cap.read();cap.release()
    assert ok and 175<float(frame[220:250,300:330].mean())<200
    assert frame[:10].mean()>240  # white fit, not black padding or a frozen portrait
    assert all(t<4 for t in proof['target_sample_times'])


def test_editorial_cards_preserve_timeline_and_do_not_count_as_people(tmp_path,monkeypatch):
    import cv2,numpy as np
    import interview_graphics as graphics
    source=tmp_path/'tracked.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(632,470))
    for n in range(100):writer.write(np.full((470,632,3),40+n,dtype=np.uint8))
    writer.release()
    proof=dict(source_start=0,duration=10,frames=100,matched_frames=80,other_face_frames=0,
        roles=[dict(role='guest',start_frame=0,end_frame=80),dict(role='source_illustration',start_frame=80,end_frame=100)],
        target_reference_spans=[[0,80]],source_frames_preserved=True)
    monkeypatch.setattr(graphics,'GRAPHICS',{'test':dict(reviewed_start=0,reviewed_end=10,spans=[(3,4,'财务思维')])})
    monkeypatch.setattr(graphics,'topic_card',lambda path,text:cv2.imwrite(str(path),np.full((470,632,3),250,dtype=np.uint8)))
    cues=[dict(start_sec=3,end_sec=4,zh='这是财务思维')]
    out=graphics.clean_interview_graphics(source,proof,cues,'test',tmp_path/'cards')
    assert out['source_timeline_preserved'] and not out['source_frames_preserved']
    assert out['matched_frames']==70 and out['editorial_card_frames']==10
    assert out['verified_face_ratio']==.7
    assert all(not 3<=t<4 for t in out['target_sample_times'])
    cap=cv2.VideoCapture(str(source))
    assert cap.get(cv2.CAP_PROP_FRAME_COUNT)==100 and cap.get(cv2.CAP_PROP_FPS)==10
    for n in [29,30,39,40]:
        cap.set(cv2.CAP_PROP_POS_FRAMES,n);ok,frame=cap.read();assert ok
        assert (frame.mean()>240)==(30<=n<40)
    cap.release()
    graphics.GRAPHICS['test']['spans']=[(2,4,'财务思维')]
    with pytest.raises(ValueError,match='真人动态不足70%'):
        graphics.clean_interview_graphics(source,proof,cues,'test',tmp_path/'reject')
    with pytest.raises(ValueError,match='超出已复检画面范围'):
        graphics.clean_interview_graphics(source,{**proof,'duration':11},cues,'test',tmp_path/'outside')
