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
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o, crop=None: True)
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
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o, crop=None: True)
    monkeypatch.setattr(COVER, "image_brightness", lambda p: 120)
    monkeypatch.setattr(COVER, "frame_geometry_verdict",
                        lambda p, **k: (True, "ok", 0.09))
    # 清晰度按文件名末位递增，最后一帧最清晰
    monkeypatch.setattr(COVER, "image_sharpness",
                        lambda p: float(p.rsplit("_", 1)[1].split(".")[0]))

    report = {}
    best = COVER.pick_best_frame_geometric("v.mp4", 0, 60, str(tmp_path),
                                           candidates=5, report=report)
    # 5 个候选，末帧清晰度最高
    assert best.endswith("gframe_004.jpg")
    assert report["cover_vlm_passed"] is False
    assert report["cover_geometric_rejections"] == []


def test_vlm_path_rejects_after_too_many_failures(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o, crop=None: True)
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
            str(tmp_path), candidates=10, speaker_color="other",
            report=report)
    assert e.value.code == COVER.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "no_frame_passed_vlm"
    assert payload["rejected"] > COVER.MAX_VLM_REJECTIONS
    assert report["cover_vlm_passed"] is False
    assert len(report["cover_vlm_rejections"]) > COVER.MAX_VLM_REJECTIONS


def test_vlm_path_records_rejections_even_when_a_frame_passes(monkeypatch, tmp_path):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o, crop=None: True)
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
        str(tmp_path), candidates=10, speaker_color="other", report=report)

    assert best is not None
    assert report["cover_vlm_passed"] is True
    assert report["cover_vlm_rejections"]      # 不合格的帧照样留档


# ── 候选帧池 ────────────────────────────────────────────────────────────────
# run 30259127265：只采了 5 帧、预筛后只剩 1 帧送 vision，MAX_VLM_REJECTIONS=5
# 这条「不合格超过 5 个才拒」的门槛根本凑不满，等于没设。

def test_default_candidate_pool_is_at_least_24():
    assert COVER.DEFAULT_COVER_CANDIDATES >= 24


def test_sampling_yields_requested_count_within_margins():
    times = COVER.sample_frame_times(100.0, 200.0, 24)
    assert len(times) == 24
    # 首尾各避开 3s 的转场/黑帧区
    assert times[0] >= 100.0 + COVER.EDGE_MARGIN_SEC
    assert times[-1] <= 200.0 - COVER.EDGE_MARGIN_SEC
    assert times == sorted(times)


def test_sampling_is_uniform():
    times = COVER.sample_frame_times(0.0, 120.0, 12)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert max(gaps) - min(gaps) < 1e-9


def test_sampling_survives_clip_shorter_than_two_margins():
    # 4s 的片段装不下两侧各 3s 的 margin，不能返回空列表
    times = COVER.sample_frame_times(10.0, 14.0, 24)
    assert len(times) == 24
    assert all(10.0 <= t <= 14.0 for t in times)


def test_sampling_degenerate_zero_length_clip():
    assert COVER.sample_frame_times(5.0, 5.0, 24) == [5.0]


def test_vision_path_samples_full_candidate_pool(monkeypatch, tmp_path):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o, crop=None: True)
    monkeypatch.setattr(COVER, "image_brightness", lambda p: 120)
    monkeypatch.setattr(COVER, "image_sharpness", lambda p: 1000.0)
    seen = {}

    def spy(paths, times, **kw):
        seen["n"] = len(paths)
        return paths, times

    monkeypatch.setattr(COVER, "filter_frames_by_face", spy)
    monkeypatch.setattr(COVER, "call_vision_llm",
                        lambda key, model, paths, speaker, desc="": [
                            {"frame": 1, "person": "主讲人", "cover_score": 9,
                             "reason": "好"}])

    COVER.pick_best_frame_vision(
        "v.mp4", 0, 200, "芒格", "sk-test", "Qwen/Qwen3-VL-8B-Instruct",
        str(tmp_path), speaker_color="other")

    assert seen["n"] == COVER.DEFAULT_COVER_CANDIDATES


# ── 退出码 2 的报错要可操作 ─────────────────────────────────────────────────

def test_rejection_payload_carries_actionable_hint(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o, crop=None: True)
    monkeypatch.setattr(COVER, "image_brightness", lambda p: 120)
    monkeypatch.setattr(COVER, "filter_frames_by_face", lambda p, t, **k: (p, t))
    monkeypatch.setattr(COVER, "call_vision_llm",
                        lambda key, model, paths, speaker, desc="": [
                            {"frame": i + 1, "person": "双人", "cover_score": 4,
                             "reason": "两位人物均在背景中"} for i in range(len(paths))])

    with pytest.raises(SystemExit):
        COVER.pick_best_frame_vision(
            "v.mp4", 0, 200, "芒格", "sk-test", "Qwen/Qwen3-VL-8B-Instruct",
            str(tmp_path), candidates=8, speaker_color="other")

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "no_frame_passed_vlm"
    assert payload["candidates_evaluated"] == 8
    assert payload["best_score"] == 4
    assert "--cover-time-sec" in payload["hint"]
    assert "--no-vlm" in payload["hint"]


def test_geometric_rejection_also_carries_hint(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(COVER, "extract_frame", lambda v, t, o, crop=None: True)
    monkeypatch.setattr(COVER, "image_brightness", lambda p: 120)
    monkeypatch.setattr(COVER, "frame_geometry_verdict",
                        lambda p, **k: (False, "no_face", 0.0))

    with pytest.raises(SystemExit):
        COVER.pick_best_frame_geometric("v.mp4", 0, 60, str(tmp_path),
                                        candidates=8)

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["candidates_evaluated"] == 8
    assert "--cover-time-sec" in payload["hint"]


# ── 钉帧的人物校验 ──────────────────────────────────────────────────────────
# 自动选帧和 --cover-time-sec 必须共用同一条判定，阈值只有一处定义。

@pytest.mark.parametrize("person,score,expected", [
    ("主讲人", COVER.MIN_VLM_PASS_SCORE, True),
    ("主讲人", COVER.MIN_VLM_PASS_SCORE - 1, False),
    ("主持人", 10, False),
    ("双人", 10, False),
    ("其他", 10, False),
    ("主讲人", None, False),
    ("主讲人", "n/a", False),
])
def test_frame_passes_vlm_matches_the_documented_gate(person, score, expected):
    assert COVER.frame_passes_vlm(person, score) is expected


def test_verify_frame_person_reports_the_vlm_verdict(monkeypatch):
    sent = {}

    def fake_call(api_key, model, paths, speaker, speaker_desc=""):
        sent.update(paths=paths, speaker=speaker, model=model)
        return [{"frame": 1, "person": "其他", "cover_score": 2,
                 "reason": "画面里是黑板前的另一个人"}]

    monkeypatch.setattr(COVER, "call_vision_llm", fake_call)
    v = COVER.verify_frame_person("sk", "vlm-1", "pinned.jpg", "查理·芒格")

    assert sent["paths"] == ["pinned.jpg"]
    assert sent["speaker"] == "查理·芒格"
    assert v["person"] == "其他"
    assert v["cover_score"] == 2
    assert v["passed"] is False


def test_verify_frame_person_passes_a_good_frame(monkeypatch):
    monkeypatch.setattr(
        COVER, "call_vision_llm",
        lambda *a, **k: [{"frame": 1, "person": "主讲人",
                          "cover_score": COVER.MIN_VLM_PASS_SCORE,
                          "reason": "正面特写"}])
    assert COVER.verify_frame_person("sk", "vlm-1", "f.jpg", "芒格")["passed"]


def test_verify_frame_person_returns_none_when_vlm_says_nothing(monkeypatch):
    # 拿不到判定 ≠ 判定通过，交由调用方按外部依赖失败处理
    monkeypatch.setattr(COVER, "call_vision_llm", lambda *a, **k: [])
    assert COVER.verify_frame_person("sk", "vlm-1", "f.jpg", "芒格") is None
