import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from source_publisher_marks import central_publisher_rects
from live_tracking import crop_box


def fixture():
    return json.loads((Path(__file__).parent/'fixtures/linyuan_source34_publisher.json').read_text())


def test_actual_central_publisher_can_be_avoided_with_full_face():
    d=fixture();marks=central_publisher_rects(d['evidence']);assert len(marks)==1
    assert marks[0][0]<.327 and marks[0][2]>.608 and marks[0][3]>.238
    for face in d['faces']:
        x,y,w,h=crop_box(face,d['width'],d['height'],exclusions=marks)
        fx,fy,fw,fh=face
        assert x<=fx and fx+fw<=x+w and y<=fy-fh*.22 and fy+fh<=y+h
        a,b,c,e=marks[0]
        assert x+w<=a*d['width'] or x>=c*d['width'] or y+h<=b*d['height'] or y>=e*d['height']


def test_only_recognized_publisher_in_current_shot_anchors_boxes():
    d=fixture();rows=d['evidence']
    assert not central_publisher_rects([r for r in rows if r['text']!='WEEKLYONSTOCKS'])
    assert not central_publisher_rects([{**r,'confidence':.6} for r in rows])
    assert not central_publisher_rects([{**r,'text':'普通背景文字'} for r in rows])
    assert not central_publisher_rects([{**r,'rect':[r['rect'][0],.4,r['rect'][2],.6]} for r in rows])
    # Close-up shots only retain the small corner logo, not the central wall.
    assert not central_publisher_rects([r for r in rows if r['rect'][2]<.25])


def test_publisher_boxes_are_not_added_to_global_segment_masks(tmp_path,monkeypatch):
    import produce_cn as P
    import numpy as np
    rows=fixture()['evidence']
    def measured(paths,**kwargs):
        (Path(paths[0]).parent/'corner_ocr.json').write_text(json.dumps(dict(evidence=rows)))
        return []
    monkeypatch.setattr(P,'detect_corner_logos_in_images',measured)
    frame=np.zeros((1080,1920,3),dtype=np.uint8)
    global_marks=P.selected_frame_logos(frame,tmp_path,1)
    shot_marks=P.selected_frame_logos(frame,tmp_path,1,shot_local=True)
    central=central_publisher_rects(rows)
    assert all(r not in global_marks for r in central)
    assert all(r in shot_marks for r in central)


def test_corner_calligraphy_below_ocr_is_measured_only_near_known_publisher():
    import numpy as np
    from source_publisher_marks import corner_publisher_rects
    frame=np.full((1080,1920,3),245,dtype=np.uint8)
    frame[80:151,100:379]=[10,20,190]
    frame[800:900,300:400]=[10,20,190]  # unrelated red object outside neighborhood
    anchor=dict(text='WEEKLYONSTOCKS',confidence=.97,rect=[.1536,.1194,.1958,.1352])
    marks=corner_publisher_rects([anchor],frame)
    assert len(marks)==1
    assert marks[0][1]<80/1080 and 151/1080<marks[0][3]<.15
    assert marks[0][0]<100/1920 and marks[0][2]>379/1920
    assert not corner_publisher_rects([{**anchor,'text':'普通文字'}],frame)
    assert not corner_publisher_rects([{**anchor,'confidence':.6}],frame)
    assert not corner_publisher_rects([anchor],np.full_like(frame,245))
    # Actual source34 face positions must retain the original headroom gate.
    for face in fixture()['faces']:
        x,y,w,h=crop_box(face,1920,1080,exclusions=marks)
        assert y<=face[1]-face[3]*.22
        assert y>=marks[0][3]*1080 or x>=marks[0][2]*1920


def test_normalized_measured_pixel_edge_does_not_lose_last_feasible_crop():
    # Actual 34 geometry: y=156 fits the publisher and the required headroom;
    # y=158 does not. The normalized pixel edge has a tiny float roundoff.
    face=fixture()['faces'][3]
    corner=(.0515625,.045370370370370366,.196875,.14444444444444446)
    central=central_publisher_rects(fixture()['evidence'])
    x,y,w,h=crop_box(face,1920,1080,exclusions=[corner,*central])
    assert y==156 and y+face[3]*.22<=face[1]
    import pytest
    with pytest.raises(ValueError):
        crop_box(face,1920,1080,exclusions=[(*corner[:3],158/1080),*central])
