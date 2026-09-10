import sys
from pathlib import Path
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from source_geometry import black_border_crop


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
