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
