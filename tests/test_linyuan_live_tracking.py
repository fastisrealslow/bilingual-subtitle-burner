import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from live_tracking import crop_box,complete_face


def test_same_source_gallery_requires_direct_primary_match_without_identity_drift(tmp_path):
    from live_tracking import verified_identity_gallery,source_reference_samples
    for i in (1,2,3):(tmp_path/f'identity_{i}.jpg').write_bytes(bytes([i]))
    report=dict(passed=True,visual_identity=dict(same_person_frames=[1,2,2,'../3',True,99],different_person_frames=[3]))
    paths=source_reference_samples(tmp_path,report)
    assert [p.name for p in paths]==['identity_1.jpg','identity_2.jpg']
    assert source_reference_samples(tmp_path,{**report,'passed':False})==[]
    # The second face resembles the admitted side pose but not the original
    # reference. It cannot enter the bank through that intermediate match.
    values={'identity_1.jpg':['side'],'identity_2.jpg':['other']}
    scores={('primary','side'):.58,('primary','other'):.20,('side','other'):.90}
    features,proof=verified_identity_gallery('primary',paths,lambda p:values[p.name],
        lambda a,b:scores[(a,b)],.363)
    assert features==['primary','side']
    assert proof[1]['accepted_primary_scores']==[]
    assert all(len(p['sha256'])==64 for p in proof)


@pytest.mark.parametrize('face',[(885,200,90,104),(860,210,70,74)])
def test_883_retains_more_source_pixels_before_rejecting_small_crop(face):
    x,y,w,h=crop_box(face,1280,640)
    assert w>=316 and h>=235 and 632/w<=2 and 470/h<=2
    fx,fy,fw,fh=face
    assert x<fx and y<fy and x+w>fx+fw and y+h>fy+fh


def test_small_source_still_cannot_be_upscaled_past_two():
    with pytest.raises(ValueError):crop_box((50,50,60,60),300,200)


def test_actual_801_frame_keeps_valid_shot_scale_and_new_cut_fits_between_marks():
    import json
    from live_tracking import shot_crop
    from stable_framing import StableFraming
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_801_crop.json').read_text())
    for i,row in enumerate(data['frames']):
        framing=StableFraming(30)
        framing.box=tuple(row['last']);framing.pending_cut=bool(i)
        box=shot_crop(framing,row['face'],data['width'],data['height'],0,row['marks'])
        x,y,w,h=box;fx,fy,fw,fh=row['face']
        assert w>=316 and h>=235
        assert x+8<=fx and fx+fw<=x+w-8
        assert y+fh*.22<=fy and fy+fh<=y+h-2
        assert all(not(x<c*1920 and x+w>a*1920 and y<d*1080 and y+h>b*1080)
                   for a,b,c,d in row['marks'])
        if i==0:assert box==tuple(row['last'])
        else:assert box[2]<1264  # Original 2.1x crop cannot fit the clean corridor.


def test_right_corner_mark_moves_crop_sideways_without_cutting_head():
    from live_tracking import avoid_overlays
    face=(730,140,350,360)
    box=(420,4,1262,938)
    logo=(1480/1920,35/1080,1840/1920,135/1080)
    fixed=avoid_overlays(box,face,1920,1080,[logo])
    assert fixed[2:]==box[2:] and fixed[1]==box[1]
    assert fixed[0]+fixed[2]<1480 and fixed[0]<=face[0]


def test_stable_pan_cannot_reintroduce_a_source_mark():
    from stable_framing import StableFraming
    f=StableFraming(30);f.box=(420.,4.,1262.,938.)
    face=(730,140,350,360);logo=(1480/1920,35/1080,1840/1920,135/1080)
    box=f.update((420,4,1262,938),face,1920,1080,30,exclusions=[logo])
    assert box[0]+box[2]<1480
    assert f.proof()['crop_samples'][-1]['crop']==list(box)


def test_overlay_covering_the_face_cannot_be_cropped_away():
    from live_tracking import avoid_overlays
    with pytest.raises(ValueError,match='保留完整人脸'):
        avoid_overlays((0,0,640,480),(180,120,200,200),640,480,[(.3,.3,.6,.6)])


def test_source4_larger_closeup_can_reframe_without_lowering_face_margins():
    from live_tracking import shot_crop, avoid_overlays
    from stable_framing import StableFraming
    # Actual failed frame re-detected with the production 960px YuNet path.
    # The 398x296 locked shot cannot contain the newly larger 221x271 face.
    face=(829.0,419.7,221.0,270.9)
    framing=StableFraming(30)
    framing.box=(1100.,452.,398.,296.)
    marks=[(.198,.203,.246,.293),(.742,.653,.815,.786)]
    with pytest.raises(ValueError):
        avoid_overlays(framing.box,face,1920,1080,marks)
    fixed=shot_crop(framing,face,1920,1080,528,marks)
    assert avoid_overlays(fixed,face,1920,1080,marks)==fixed
    assert fixed[2]>398 and fixed[3]>296
    assert framing.proof()['cut_frames']==[]  # not falsely called a detected cut
    assert len(framing.proof()['geometry_reframes'])==1
    for n in range(529,560):
        next_box=shot_crop(framing,face,1920,1080,n,marks)
        assert next_box[2:]==fixed[2:]
    assert len(framing.proof()['geometry_reframes'])==1
    with pytest.raises(ValueError):
        shot_crop(framing,face,1920,1080,560,[(.4,.35,.6,.7)])


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


@pytest.mark.parametrize('initial_face,face_width',[(True,120),(False,120),(True,80)])
def test_mid_render_failure_keeps_exact_source_frames(tmp_path,monkeypatch,initial_face,face_width):
    import cv2
    import numpy as np
    import json
    from types import SimpleNamespace
    from live_tracking import render_tracked
    # This case exercises the full decoder's failure evidence; preflight has
    # its own identity/rewind tests and must not consume this stateful mock.
    monkeypatch.setattr('live_tracking.preflight_geometry',lambda *a:None)
    source=tmp_path/'source.mp4'
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(640,480))
    for i in range(60):writer.write(np.full((480,640,3),i*3,dtype=np.uint8))
    writer.release()
    reference=tmp_path/'reference.png'
    cv2.imwrite(str(reference),np.zeros((480,640,3),dtype=np.uint8))
    face=np.array([[160,120,120,160,180,160,240,160,200,200,180,240,240,240,.99]],dtype=np.float32)
    face[0,2]=face_width

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
    with pytest.raises(ValueError,match='缺少人脸|达不到80%'):
        render_tracked(source,1,4,output,reference,('detector','recognizer'))
    proof=json.loads(output.with_suffix('.json').read_text())
    assert proof['passed'] is False
    assert not output.exists()
    # The exact remaining-frame bound now proves failure before the old
    # two-second no-face timeout. Evidence must still name the actual frame.
    accepted_initial=initial_face and face_width>=96
    assert proof['failure_source_time']==pytest.approx(1.9 if accepted_initial else 1.8)
    assert proof['encoded_frames']==proof['decoded_frames']==(10 if accepted_initial else 9)
    assert proof['consecutive_no_face_seconds']==pytest.approx(.8 if initial_face and face_width<96 else .9)
    assert proof['identity_matched_below_min_size_frames']==int(initial_face and face_width<96)
    assert proof['largest_identity_matched_short_edge']==(face_width if initial_face else 0)
    directory=tmp_path/proof['evidence_directory']
    assert cv2.imread(str(directory/'failure.jpg')).shape==(480,640,3)
    assert proof['samples'] and all((directory/x['file']).is_file() for x in proof['samples'])


def test_interview_keeps_broll_frames_and_distinguishes_verified_participants(tmp_path,monkeypatch):
    import cv2,numpy as np,json
    from types import SimpleNamespace
    from live_tracking import render_tracked
    source=tmp_path/'interview.mp4';reference=tmp_path/'guest.png';host=tmp_path/'host.png'
    cv2.imwrite(str(reference),np.full((480,640,3),20,dtype=np.uint8))
    cv2.imwrite(str(host),np.full((480,640,3),100,dtype=np.uint8))
    writer=cv2.VideoWriter(str(source),cv2.VideoWriter_fourcc(*'mp4v'),10,(640,480))
    for n in range(100):writer.write(np.full((480,640,3),20 if n<40 else 100 if n<80 else 180+n-80,dtype=np.uint8))
    writer.release()
    face=np.array([[160,120,120,160,180,160,240,160,200,200,180,240,240,240,.99]],dtype=np.float32)
    detector=SimpleNamespace(setInputSize=lambda *a:None,
        detect=lambda frame:(None,None if frame.mean()>160 else face))
    recognizer=SimpleNamespace(alignCrop=lambda frame,f:frame,
        feature=lambda frame:0 if frame.mean()<60 else 1,
        match=lambda a,b,*args:1. if a==b else 0.)
    monkeypatch.setattr(cv2,'FaceDetectorYN',SimpleNamespace(create=lambda *a,**k:detector))
    monkeypatch.setattr(cv2,'FaceRecognizerSF',SimpleNamespace(create=lambda *a:recognizer))
    output=tmp_path/'tracked.mp4'
    proof=render_tracked(source,0,10,output,reference,('detector','recognizer'),
        context_crop=(0,0,640,400),participant_reference=host)
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
    monkeypatch.setattr(graphics,'GRAPHICS',{'test':dict(reviewed_start=0,reviewed_end=10,illustration_range=(8,10),spans=[(3,4,'财务思维'),(8,8.1,'财务思维')])})
    monkeypatch.setattr(graphics,'topic_card',lambda path,text,**kw:cv2.imwrite(str(path),np.full((470,632,3),250,dtype=np.uint8)))
    cues=[dict(start_sec=3,end_sec=4,zh='这是财务思维199元113元28亿元151亿元')]
    out=graphics.clean_interview_graphics(source,proof,cues,'test',tmp_path/'cards')
    assert out['source_timeline_preserved'] and not out['source_frames_preserved']
    assert out['matched_frames']==70 and out['editorial_card_frames']==11
    assert out['verified_face_ratio']==.7
    assert out['reformatted_illustration_frames']==19
    assert out['replaced_source_illustration_frames']==1
    assert out['context_picture_frames']==19
    assert out['illustration_cards'][0]['values']['2013']['profit_yi_yuan']==151
    assert all(not 3<=t<4 for t in out['target_sample_times'])
    cap=cv2.VideoCapture(str(source))
    assert cap.get(cv2.CAP_PROP_FRAME_COUNT)==100 and cap.get(cv2.CAP_PROP_FPS)==10
    for n in [29,30,39,40,79,80,99]:
        cap.set(cv2.CAP_PROP_POS_FRAMES,n);ok,frame=cap.read();assert ok
        assert (frame.mean()>240)==(30<=n<40 or n>=80)
    cap.release()
    graphics.GRAPHICS['test']['spans']=[(2,4,'财务思维')]
    with pytest.raises(ValueError,match='真人动态不足70%'):
        graphics.clean_interview_graphics(source,proof,cues,'test',tmp_path/'reject')
    with pytest.raises(ValueError,match='超出已复检画面范围'):
        graphics.clean_interview_graphics(source,{**proof,'duration':11},cues,'test',tmp_path/'outside')
