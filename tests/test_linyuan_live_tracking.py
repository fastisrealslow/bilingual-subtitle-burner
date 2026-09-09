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
