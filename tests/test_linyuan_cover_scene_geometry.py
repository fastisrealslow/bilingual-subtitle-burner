from pathlib import Path
import sys
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from cover_scene_geometry import border_exclusions
from live_tracking import scene_cover_crop


def pictures(count=7):
    frames=[np.full((1080,1920,3),100,dtype=np.uint8) for _ in range(count)]
    for frame in frames:
        frame[:28]=0;frame[-28:]=0
    return frames


def test_cover_proposes_only_repeated_measured_blank_edges_and_preserves_source():
    frames=pictures();before=[f.copy() for f in frames]
    marks,proof=border_exclusions(frames)
    assert marks==[(0,0,1,28/1080),(0,1052/1080,1,1)]
    assert proof['samples']==7 and proof['active_crop']==[0,28,1920,1024]
    assert proof['final_quality_approved'] is False
    assert all(np.array_equal(a,b) for a,b in zip(before,frames))
    x,y,w,h=scene_cover_crop((900,250,280,340),1920,1080,marks)
    assert y>=28 and y+h<=1052 and w>=960 and h>=540


def test_one_dark_shot_cannot_enlarge_a_source_crop():
    frames=pictures();frames[-1][:28]=100
    marks,proof=border_exclusions(frames)
    assert proof['active_crop'][1]==0
    assert not any(m[:3]==(0,0,1) for m in marks)


def test_large_black_regions_and_insufficient_samples_keep_existing_hard_rejection():
    with pytest.raises(ValueError,match='抽帧不足'):border_exclusions(pictures(5))
    frames=pictures()
    for frame in frames:frame[:200]=0
    with pytest.raises(ValueError,match='安全裁边范围'):border_exclusions(frames)


def test_bounded_tighter_proposal_preserves_face_and_minimum_source_pixels():
    face=(900,250,140,170)
    first=scene_cover_crop(face,1920,1080,[])
    second=scene_cover_crop(face,1920,1080,[],maximum_width=first[2]-64)
    assert 960<=second[2]<first[2] and second[3]>=540
    x,y,w,h=second
    assert x+8<=face[0] and face[0]+face[2]<=x+w-8
    assert y+max(8,face[3]*.22)<=face[1]
    with pytest.raises(ValueError,match='人物占比'):
        scene_cover_crop((900,250,90,110),1920,1080,[])


@pytest.mark.parametrize('size',[(1080,1640),(1080,1920),(1104,1080)])
def test_portrait_source_can_supply_a_native_wide_window_when_face_really_fits(size):
    face=(380,350,250,320)
    x,y,w,h=scene_cover_crop(face,*size,[])
    assert 960<=w<=size[0] and 540<=h<=size[1]
    assert 1.77<=w/h<=1.79 and x>=0 and y>=0
    assert x+w<=size[0] and y+h<=size[1]
    assert x+8<=face[0] and face[0]+face[2]<=x+w-8
    assert y+max(8,face[3]*.22)<=face[1]
    assert face[1]+face[3]<=y+h-8


def test_portrait_window_still_rejects_a_tall_face_overlays_or_insufficient_pixels():
    with pytest.raises(ValueError):scene_cover_crop((200,100,600,1000),1080,1920,[])
    with pytest.raises(ValueError):scene_cover_crop((380,350,250,320),1080,1920,[(0,0,1,1)])
    with pytest.raises(ValueError):scene_cover_crop((200,350,200,280),720,1280,[])
