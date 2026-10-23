"""Actual wide-shot writing must not mask a following clean close-up."""
import json
from pathlib import Path
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import produce_cn as P
from live_tracking import scene_cover_crop


def actual():
    return json.loads((ROOT/'tests/fixtures/linyuan_scene_cover_shot_text.json').read_text())


def shared_marks(r):
    w,h=r['resolution'];x,y,cw,ch=r['border_proof']['active_crop']
    marks=list(r['ocr']['logos'])+P.source_edge_text_exclusions(r['ocr']['evidence'])
    if x:marks.append((0,0,x/w,1))
    if y:marks.append((0,0,1,y/h))
    if x+cw<w:marks.append(((x+cw)/w,0,1,1))
    if y+ch<h:marks.append((0,(y+ch)/h,1,1))
    return marks


def test_actual_native_closeup_uses_its_own_side_writing_with_unchanged_geometry():
    r=actual();evidence=r['ocr']['evidence'];old=shared_marks(r)
    for row in evidence:
        a,b,c,d=row['rect']
        if row['confidence']>=.75 and (d<.30 or b>.80 or c<.20 or a>.80):
            old.append((max(0,a-.004),max(0,b-.004),min(1,c+.004),min(1,d+.004)))
    with pytest.raises(ValueError,match='没有避开来源文字'):
        scene_cover_crop(r['face_box'],*r['resolution'],old)
    marks=shared_marks(r)+P.source_frame_edge_text_exclusions(evidence,r['frame_index'])
    x,y,w,h=scene_cover_crop(r['face_box'],*r['resolution'],marks)
    assert w>=960 and h>=540 and abs(w/h-16/9)<.01
    fx,fy,fw,fh=r['face_box']
    assert x+8<=fx and fx+fw<=x+w-8 and y+max(8,fh*.22)<=fy
    assert not any(x<c*1280 and x+w>a*1280 and y<d*720 and y+h>b*720 for a,b,c,d in marks)


def test_other_shots_keep_shared_corner_and_bottom_protection():
    rows=[dict(frame=0,confidence=.9,rect=[.80,.04,.96,.09]),
          dict(frame=0,confidence=.9,rect=[.10,.90,.80,.98]),
          dict(frame=0,confidence=.9,rect=[.08,.36,.15,.42])]
    current=P.source_frame_edge_text_exclusions(rows,1)
    assert len(current)==2
    assert len(P.source_frame_edge_text_exclusions(rows,0))==3
    assert current[0][0]<.80 and current[0][2]>.96
    assert current[1][1]<.90 and current[1][3]>.98


def test_localization_never_rescues_tiny_native_face():
    r=actual();marks=shared_marks(r)+P.source_frame_edge_text_exclusions(r['ocr']['evidence'],r['frame_index'])
    with pytest.raises(ValueError,match='人物占比'):
        scene_cover_crop((573,138,31,42),*r['resolution'],marks)
