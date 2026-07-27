"""封面选帧的人脸预筛（steps/step7_cover.py）。"""

import numpy as np
import pytest

import step7_cover as C

cv2 = pytest.importorskip("cv2")


def _write(tmp_path, name, img):
    path = str(tmp_path / name)
    cv2.imwrite(path, img)
    return path


@pytest.fixture
def blank_frame(tmp_path):
    """纯灰底，级联检不出脸。"""
    img = np.full((360, 640, 3), 128, dtype=np.uint8)
    return _write(tmp_path, "blank.jpg", img)


def test_blank_frame_has_no_face(blank_frame):
    assert C.largest_face_ratio(blank_frame) == 0.0


def test_unreadable_path_returns_zero(tmp_path):
    assert C.largest_face_ratio(str(tmp_path / "missing.jpg")) == 0.0


def test_all_frames_rejected_falls_back_to_full_list(blank_frame):
    paths = [blank_frame, blank_frame, blank_frame]
    times = [1.0, 2.0, 3.0]
    kept_p, kept_t = C.filter_frames_by_face(paths, times)
    # 一帧都不合格时不能返回空，否则封面这一步直接空手而归
    assert kept_p == paths and kept_t == times


def test_qualifying_frames_are_kept_and_others_dropped(monkeypatch):
    ratios = {"a.jpg": 0.0, "b.jpg": 0.12, "c.jpg": 0.01, "d.jpg": 0.05}
    monkeypatch.setattr(C, "largest_face_ratio", lambda p: ratios[p])
    monkeypatch.setattr(C, "_get_face_cascade", lambda: object())

    kept_p, kept_t = C.filter_frames_by_face(list(ratios), [1.0, 2.0, 3.0, 4.0])
    # 阈值是 ≥5%，d 刚好卡在线上要保留，c 的 1% 要丢掉
    assert kept_p == ["b.jpg", "d.jpg"]
    assert kept_t == [2.0, 4.0]


def test_filter_is_noop_without_opencv(monkeypatch):
    monkeypatch.setattr(C, "_get_face_cascade", lambda: None)
    paths, times = ["a.jpg", "b.jpg"], [1.0, 2.0]
    assert C.filter_frames_by_face(paths, times) == (paths, times)


def test_empty_input():
    assert C.filter_frames_by_face([], []) == ([], [])


def test_cascade_loads_in_this_environment():
    # opencv-python-headless 自带 haarcascade 数据文件，装错包（如 opencv-contrib
    # 的精简变体）会让预筛静默失效，这里显式钉一下
    assert C._get_face_cascade() is not None


def test_ratio_is_computed_against_frame_area(tmp_path, monkeypatch):
    img = np.full((100, 200, 3), 128, dtype=np.uint8)
    path = _write(tmp_path, "sized.jpg", img)

    class FakeCascade:
        def detectMultiScale(self, gray, **kw):
            return [(0, 0, 10, 10), (0, 0, 40, 50)]   # 取最大的那个

    monkeypatch.setattr(C, "_get_face_cascade", lambda: FakeCascade())
    # 40*50 / (200*100) = 0.1
    assert C.largest_face_ratio(path) == pytest.approx(0.1)
