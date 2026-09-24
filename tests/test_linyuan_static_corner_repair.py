import copy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from static_corner_repair import source_exclusions
from live_tracking import crop_box, geometry_obstruction


def proof():
    # Actual source39 failed short render: a locked 780x580 source crop.
    return dict(engine='yunet_sface_per_frame_cpu',passed=True,frames=1826,
        decoded_frames=1826,encoded_frames=1826,first_crop=[456,206,780,580],
        last_crop=[456,206,780,580],framing=dict(held_frames=1825,pan_frames=0,
            cut_frames=[],geometry_reframes=[],crop_samples=[dict(crop=[456,206,780,580])]))


BOX=[.2468354430379747,.17446808510638298,.3401898734177215,.2148936170212766]


def test_actual_wall_label_maps_to_source_and_a_clean_complete_face_crop_exists():
    marks=source_exclusions(proof(),[BOX],1920,1080)
    a,b,c,d=marks[0]
    assert a*1920==pytest.approx(646.53164557)
    assert c*1920==pytest.approx(723.348101266)
    # Actual first-frame target face measured in the failed 632x470 window.
    face=[456+265.26*780/632,206+108.35*580/470,190.36*780/632,257.69*580/470]
    assert geometry_obstruction(face,1920,1080,marks) is None
    x,y,w,h=crop_box(face,1920,1080,exclusions=marks)
    assert w>=316 and h>=235
    assert not (x<c*1920 and x+w>a*1920 and y<d*1080 and y+h>b*1080)
    fx,fy,fw,fh=face
    assert x+8<=fx and y+max(8,fh*.22)<=fy and x+w>=fx+fw+8 and y+h>=fy+fh+2


@pytest.mark.parametrize('change',[
    {'mode':'verified_interview_context_v1'}, {'passed':False}, {'encoded_frames':1800},
    {'last_crop':[458,206,780,580]}, {'framing':{}},
])
def test_uncertain_geometry_never_creates_source_exclusions(change):
    data=proof();data.update(change)
    assert source_exclusions(data,[BOX],1920,1080)==[]


@pytest.mark.parametrize('key,value',[
    ('held_frames',1824),('pan_frames',1),('cut_frames',[100]),
    ('geometry_reframes',[{'frame':300}]),('crop_samples',[{'crop':[500,200,780,580]}]),
])
def test_camera_changes_and_sparse_lock_evidence_are_not_mapped(key,value):
    data=proof();data['framing'][key]=value
    assert source_exclusions(data,[BOX],1920,1080)==[]


@pytest.mark.parametrize('box',[[0,0,float('nan'),.2],[-.1,0,.2,.2],[.3,.1,.2,.2]])
def test_invalid_ocr_rectangles_are_not_mapped(box):
    assert source_exclusions(proof(),[box],1920,1080)==[]
