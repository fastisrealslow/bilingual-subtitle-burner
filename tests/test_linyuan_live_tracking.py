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
