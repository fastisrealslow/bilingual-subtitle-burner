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
