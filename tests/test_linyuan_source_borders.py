import sys
from pathlib import Path
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from source_geometry import black_border_crop


def wide_preview(tmp_path):
    import cv2
    video = tmp_path / 'wide.mp4'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 1, (1280, 540))
    for _ in range(8):
        writer.write(np.full((540, 1280, 3), 100, dtype=np.uint8))
    writer.release()
    return video


def test_wide_interview_fits_16_9_without_scaling_or_vertical_crop(tmp_path):
    from source_geometry import landscape_crop_plan
    crop, proof = landscape_crop_plan(wide_preview(tmp_path), 1280, 540,
        lambda: lambda frame: [(550, 100, 160, 180)])
    assert crop == (960, 540, 160, 0)
    assert proof['samples'] == 8 and proof['applied']
    assert proof['final_quality_approved'] is False


def test_crop_moves_to_keep_a_speaker_near_the_edge(tmp_path):
    from source_geometry import landscape_crop_plan
    crop, _ = landscape_crop_plan(wide_preview(tmp_path), 1280, 540,
        lambda: lambda frame: [(170, 100, 160, 180)])
    assert crop == (960, 540, 10, 0)


@pytest.mark.parametrize('last_faces', [[], [(0, 100, 160, 180)],
    [(400, 100, 160, 180), (900, 100, 160, 180)]])
def test_late_camera_change_or_group_shot_keeps_original_view(tmp_path, last_faces):
    from source_geometry import landscape_crop_plan
    faces = iter([[(800, 100, 160, 180)]] * 7 + [last_faces])
    crop, proof = landscape_crop_plan(wide_preview(tmp_path), 1280, 540,
        lambda: lambda frame: next(faces))
    assert crop is None and not proof['applied']


@pytest.mark.parametrize('width,height', [(1280, 720), (720, 1280), (640, 480), (2560, 720)])
def test_existing_aspects_and_excessive_crops_do_not_start_face_inference(width, height):
    from source_geometry import landscape_crop_plan
    crop, proof = landscape_crop_plan('unused', width, height,
        lambda: pytest.fail('This source must keep its composition'))
    assert crop is None and not proof['applied']


def frame():
    image = np.full((1080, 1920, 3), 100, dtype=np.uint8)
    image[:, :64] = 0
    image[-12:] = 0
    return image


def test_actual_682_border_geometry_is_trimmed_without_padding():
    assert black_border_crop([frame() for _ in range(12)]) == (1856, 1068, 64, 0)


def test_one_dark_camera_shot_cannot_enlarge_the_crop():
    frames = [frame() for _ in range(12)]
    frames[3][:, :250] = 0
    assert black_border_crop(frames) == (1856, 1068, 64, 0)


def test_border_not_present_in_every_sample_is_not_cropped():
    frames = [frame() for _ in range(12)]
    frames[3][:] = 100
    assert black_border_crop(frames) == (1920, 1080, 0, 0)


def test_large_black_area_remains_a_quality_rejection():
    frames = [frame() for _ in range(12)]
    for image in frames:
        image[:, :800] = 0
    with pytest.raises(ValueError, match='安全裁边'):
        black_border_crop(frames)


def test_selected_interval_geometry_does_not_sample_an_unrelated_intro(tmp_path):
    import cv2
    from source_geometry import refine_native_crop,has_black_fill
    video=tmp_path/'source.mp4'
    writer=cv2.VideoWriter(str(video),cv2.VideoWriter_fourcc(*'mp4v'),10,(640,480))
    for i in range(40):
        image=np.full((480,640,3),100,dtype=np.uint8)
        if i>=20:image[:,:40]=0
        writer.write(image)
    writer.release()
    vf,w,h,proof=refine_native_crop(video,'null',640,480,tmp_path,minimum=360,start=2,duration=2)
    assert (w,h)==(600,480) and vf.endswith('crop=600:480:40:0')
    assert proof['source_start']==2 and proof['remaining_black_edge_hits']==0
    assert not has_black_fill(np.full((480,600,3),100,dtype=np.uint8))
