import sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from stable_framing import StableFraming
from live_tracking import crop_box


def test_stationary_shot_never_zooms_or_pans_for_detector_noise():
    framing=StableFraming(30)
    frame=np.full((720,1280,3),60,dtype=np.uint8)
    boxes=[]
    for n in range(300):
        face=(400+(n%5)-2,200+(n%3)-1,160+(n%7)-3,180+(n%9)-4)
        framing.observe(frame,n)
        boxes.append(framing.update(crop_box(face,1280,720),face,1280,720,n))
    assert len(set(boxes))==1
    assert framing.proof()['held_frames']==299


def test_real_source_cut_reframes_immediately_but_not_face_box_outlier():
    framing=StableFraming(30)
    for n,(brightness,face) in enumerate([(20,(700,200,200,220)),
                                         (20,(250,200,100,110)),
                                         (180,(250,200,100,110))]):
        framing.observe(np.full((720,1280,3),brightness,dtype=np.uint8),n)
        box=framing.update(crop_box(face,1280,720),face,1280,720,n)
        if n==0:first=box
        if n==1:assert box[2:]==first[2:] and abs(box[0]-first[0])<4
    assert box==crop_box((250,200,100,110),1280,720)
    assert framing.proof()['cut_frames']==[2]


def test_walking_subject_uses_bounded_pan_and_fixed_scale():
    framing=StableFraming(30);boxes=[]
    for n in range(300):
        face=(250+n*.5,200,160,180)
        boxes.append(framing.update(crop_box(face,1280,720),face,1280,720,n))
    assert len({b[2:] for b in boxes})==1
    assert boxes[-1][0]>boxes[0][0]
    assert framing.proof()['max_pan_output_px']<=632*.12/30+.0001
    assert all(0<=x<=1280-w and 0<=y<=720-h for x,y,w,h in boxes)
