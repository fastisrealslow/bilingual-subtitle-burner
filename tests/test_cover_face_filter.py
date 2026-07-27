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


def test_faceless_frames_are_dropped(monkeypatch):
    # 候选够多时，没脸的帧一定要丢：a 是 0%，不该出现在送 vision 的名单里
    ratios = {f"q{i}.jpg": 0.12 for i in range(C.FALLBACK_KEEP)}
    ratios["a.jpg"] = 0.0
    monkeypatch.setattr(C, "largest_face_ratio", lambda p: ratios[p])
    monkeypatch.setattr(C, "_get_face_cascade", lambda: object())

    paths = list(ratios)
    kept_p, _ = C.filter_frames_by_face(paths, [float(i) for i in range(len(paths))])
    assert "a.jpg" not in kept_p
    assert len(kept_p) == C.FALLBACK_KEEP


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


def test_below_threshold_frames_fall_back_to_top_faces(monkeypatch):
    # 854 宽的中景机位实测最大脸占比只有 ~4.7%，整条片子都卡在 5% 下方，
    # 「全部送 vision」等于白付预筛的 CPU 一分钱不省
    ratios = {f"f{i}.jpg": 0.04 - i * 0.001 for i in range(20)}
    monkeypatch.setattr(C, "largest_face_ratio", lambda p: ratios[p])
    monkeypatch.setattr(C, "_get_face_cascade", lambda: object())

    paths = list(ratios)
    times = [float(i) for i in range(len(paths))]
    kept_p, kept_t = C.filter_frames_by_face(paths, times)

    assert len(kept_p) == C.FALLBACK_KEEP
    assert set(kept_p) == set(paths[:C.FALLBACK_KEEP])   # 脸最大的前 N 帧
    assert kept_t == sorted(kept_t)                      # 仍按时间序送 vision


def test_frames_without_any_face_still_fall_back_to_full_list(monkeypatch):
    monkeypatch.setattr(C, "largest_face_ratio", lambda p: 0.0)
    monkeypatch.setattr(C, "_get_face_cascade", lambda: object())
    paths, times = ["a.jpg", "b.jpg", "c.jpg"], [1.0, 2.0, 3.0]
    assert C.filter_frames_by_face(paths, times) == (paths, times)


def test_single_qualifying_frame_is_topped_up_with_runners_up(monkeypatch):
    # 只有 1 帧过线时不能只送这 1 帧：vision 没得挑，只能矮子里拔将军。
    # 过线的 b 必须在，剩下的按脸大小补齐到 FALLBACK_KEEP，仍按时间序。
    ratios = {"a.jpg": 0.04, "b.jpg": 0.09, "c.jpg": 0.03,
              "d.jpg": 0.02, "e.jpg": 0.0}
    monkeypatch.setattr(C, "largest_face_ratio", lambda p: ratios[p])
    monkeypatch.setattr(C, "_get_face_cascade", lambda: object())

    kept_p, kept_t = C.filter_frames_by_face(list(ratios), [1.0, 2.0, 3.0, 4.0, 5.0])
    assert kept_p == ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]   # e 没脸，仍然丢掉
    assert kept_t == [1.0, 2.0, 3.0, 4.0]
