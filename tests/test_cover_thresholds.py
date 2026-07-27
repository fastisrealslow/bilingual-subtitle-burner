"""封面出片门槛与几何规则（steps/step7_cover.py）。"""

import json

import pytest

import step7_cover as COVER


def fake_box(monkeypatch, ratio, box, size=(1000, 1000)):
    monkeypatch.setattr(COVER, "largest_face_box",
                        lambda _p: None if ratio is None else (ratio, box, size))


# ── 几何规则 ────────────────────────────────────────────────────────────────

def test_centered_large_face_passes(monkeypatch):
    fake_box(monkeypatch, 0.09, (400, 200, 300, 300))
    ok, why, ratio = COVER.frame_geometry_verdict("f.jpg")
    assert ok and why == "ok" and ratio == 0.09


def test_no_face_rejected(monkeypatch):
    fake_box(monkeypatch, None, None)
    ok, why, _ = COVER.frame_geometry_verdict("f.jpg")
    assert not ok and why == "no_face"


def test_small_face_rejected(monkeypatch):
    fake_box(monkeypatch, 0.03, (400, 200, 170, 170))
    ok, why, _ = COVER.frame_geometry_verdict("f.jpg")
    assert not ok and why.startswith("face_too_small")


def test_face_below_top_ratio_rejected(monkeypatch):
    # 脸中心在 85% 高度，标题条会直接压上去
    fake_box(monkeypatch, 0.09, (400, 700, 300, 300))
    ok, why, _ = COVER.frame_geometry_verdict("f.jpg")
    assert not ok and why.startswith("face_too_low")


def test_face_in_watermark_corner_rejected(monkeypatch):
    # 左上角：x 和 y 同时落进水印区
    fake_box(monkeypatch, 0.09, (0, 0, 300, 300))
    ok, why, _ = COVER.frame_geometry_verdict("f.jpg")
    assert not ok and why == "face_in_watermark_corner"


def test_face_near_left_edge_but_vertically_safe_passes(monkeypatch):
    # 只贴左边、纵向在安全区 —— 不是四角，不该拒
    fake_box(monkeypatch, 0.09, (0, 300, 300, 300))
    ok, why, _ = COVER.frame_geometry_verdict("f.jpg")
    assert ok and why == "ok"


# ── 拒绝退出 ────────────────────────────────────────────────────────────────

def test_reject_cover_exits_two_with_structured_reason(capsys):
    with pytest.raises(SystemExit) as e:
        COVER.reject_cover("no_frame_meets_geometry", candidates=7)
    assert e.value.code == COVER.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["stage"] == "cover"
    assert payload["reason"] == "no_frame_meets_geometry"
    assert payload["candidates"] == 7


def test_geometric_picker_rejects_when_no_frame_qualifies(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o: True)
    monkeypatch.setattr(COVER, "image_brightness", lambda p: 120)
    monkeypatch.setattr(COVER, "frame_geometry_verdict",
                        lambda p, **k: (False, "no_face", 0.0))

    report = {}
    with pytest.raises(SystemExit) as e:
        COVER.pick_best_frame_geometric("v.mp4", 0, 60, str(tmp_path),
                                        report=report)
    assert e.value.code == COVER.EXIT_QUALITY
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "no_frame_meets_geometry"
    assert report["cover_vlm_passed"] is False


def test_geometric_picker_returns_sharpest_passing_frame(monkeypatch, tmp_path):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o: True)
    monkeypatch.setattr(COVER, "image_brightness", lambda p: 120)
    monkeypatch.setattr(COVER, "frame_geometry_verdict",
                        lambda p, **k: (True, "ok", 0.09))
    # 清晰度按文件名末位递增，最后一帧最清晰
    monkeypatch.setattr(COVER, "image_sharpness",
                        lambda p: float(p.rsplit("_", 1)[1].split(".")[0]))

    report = {}
    best = COVER.pick_best_frame_geometric("v.mp4", 0, 60, str(tmp_path),
                                           sample_interval=10, report=report)
    # 采样窗口 6~54s、步长 10 → 5 帧，末帧清晰度最高
    assert best.endswith("gframe_004.jpg")
    assert report["cover_vlm_passed"] is False
    assert report["cover_geometric_rejections"] == []


def test_vlm_path_rejects_after_too_many_failures(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o: True)
    monkeypatch.setattr(COVER, "image_brightness", lambda p: 120)
    monkeypatch.setattr(COVER, "filter_frames_by_face", lambda p, t, **k: (p, t))
    # 每帧都被判为主持人，累计 6 张 > MAX_VLM_REJECTIONS
    monkeypatch.setattr(COVER, "call_vision_llm",
                        lambda key, model, paths, speaker, desc="": [
                            {"frame": i + 1, "person": "主持人", "cover_score": 2,
                             "reason": "不是主讲人"} for i in range(len(paths))])

    report = {}
    with pytest.raises(SystemExit) as e:
        COVER.pick_best_frame_vision(
            "v.mp4", 0, 100, "芒格", "sk-test", "Qwen/Qwen3-VL-8B-Instruct",
            str(tmp_path), sample_interval=10, speaker_color="other",
            report=report)
    assert e.value.code == COVER.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "no_frame_passed_vlm"
    assert payload["rejected"] > COVER.MAX_VLM_REJECTIONS
    assert report["cover_vlm_passed"] is False
    assert len(report["cover_vlm_rejections"]) > COVER.MAX_VLM_REJECTIONS


def test_vlm_path_records_rejections_even_when_a_frame_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o: True)
    monkeypatch.setattr(COVER, "image_brightness", lambda p: 120)
    monkeypatch.setattr(COVER, "image_sharpness", lambda p: 1000.0)
    monkeypatch.setattr(COVER, "filter_frames_by_face", lambda p, t, **k: (p, t))

    def vision(key, model, paths, speaker, desc=""):
        out = [{"frame": 1, "person": "主讲人", "cover_score": 9, "reason": "好"}]
        out += [{"frame": i + 1, "person": "双人", "cover_score": 3, "reason": "两个人"}
                for i in range(1, len(paths))]
        return out

    monkeypatch.setattr(COVER, "call_vision_llm", vision)

    report = {}
    best = COVER.pick_best_frame_vision(
        "v.mp4", 0, 60, "芒格", "sk-test", "Qwen/Qwen3-VL-8B-Instruct",
        str(tmp_path), sample_interval=10, speaker_color="other", report=report)

    assert best is not None
    assert report["cover_vlm_passed"] is True
    assert report["cover_vlm_rejections"]      # 不合格的帧照样留档
