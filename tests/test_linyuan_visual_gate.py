"""林园人物参考照核验与清理后 OCR 角标复检。"""

import sys
from pathlib import Path

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "linyuan"))
import produce_cn as P  # noqa: E402


@pytest.mark.parametrize("verdict,passed", [
    ({"same_person_frames": [1, 3, 5], "different_person_frames": [2, 4],
      "confidence": 0.91}, True),
    ({"same_person_frames": [], "different_person_frames": [1, 2, 3, 4, 5, 6],
      "confidence": 0.99}, False),
    ({"same_person_frames": [1], "different_person_frames": [2],
      "confidence": 0.99}, False),
    ({"same_person_frames": [1, 2], "different_person_frames": [3, 4, 5],
      "confidence": 0.99}, False),
    ({"same_person_frames": [1, 2, 3], "different_person_frames": [4],
      "confidence": 0.5}, False),
])
def test_identity_gate_is_fail_closed(verdict, passed):
    assert P.identity_verdict_passes(verdict, 6) is passed


def test_identity_gate_ignores_invalid_and_overlapping_frame_numbers():
    verdict = {"same_person_frames": [1, 2, 99],
               "different_person_frames": [1, 3, -1],
               "confidence": 0.9}
    assert P.identity_verdict_passes(verdict, 3) is True


def test_markdown_wrapped_identity_json_is_accepted():
    assert P._parse_json_object(
        '```json\n{"same_person_frames":[1,2],"confidence":0.9}\n```'
    )["same_person_frames"] == [1, 2]


def test_source_identity_rejects_a_video_full_of_other_people(monkeypatch,
                                                               tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"reference")
    frames = [tmp_path / f"f{i}.jpg" for i in range(6)]
    monkeypatch.setattr(P, "_download_speaker_reference",
                        lambda *args: reference)
    monkeypatch.setattr(P, "_sample_visual_frames",
                        lambda *args: (frames, [10, 20, 30, 40, 50, 60]))
    monkeypatch.setattr(P, "_call_identity_vlm", lambda *args: {
        "same_person_frames": [],
        "different_person_frames": [1, 2, 3, 4, 5, 6],
        "confidence": 0.99,
        "reason": "与参考照不是同一人",
    })
    with pytest.raises(P.VisualQualityError, match="人物不一致"):
        P.verify_source_identity(Path("bad.mp4"), tmp_path, "林园", "sk-test")


def test_source_identity_returns_only_a_verified_cover_time(monkeypatch,
                                                             tmp_path):
    reference = tmp_path / "reference.jpg"
    reference.write_bytes(b"reference")
    frames = [tmp_path / f"f{i}.jpg" for i in range(6)]
    monkeypatch.setattr(P, "_download_speaker_reference",
                        lambda *args: reference)
    monkeypatch.setattr(P, "_sample_visual_frames",
                        lambda *args: (frames, [10, 20, 30, 40, 50, 60]))
    monkeypatch.setattr(P, "_call_identity_vlm", lambda *args: {
        "same_person_frames": [2, 4, 6],
        "different_person_frames": [1, 3, 5],
        "best_cover_frame": 4,
        "confidence": 0.96,
        "watermark_texts": [],
        "reason": "脸部特征一致",
    })
    report = P.verify_source_identity(
        Path("good.mp4"), tmp_path, "林园", "sk-test")
    assert report["best_cover_time"] == 40
    assert report["same_person_frames"] == [2, 4, 6]


def test_corner_ocr_groups_persistent_boxes(monkeypatch, tmp_path):
    cv2 = pytest.importorskip("cv2")
    frames = []
    for i in range(3):
        fp = tmp_path / f"f{i}.jpg"
        cv2.imwrite(str(fp), np.full((480, 852, 3), 127, dtype=np.uint8))
        frames.append(fp)

    box = [[710, 30], [830, 30], [830, 55], [710, 55]]

    class OCR:
        def __call__(self, *args, **kwargs):
            return [box], None

    monkeypatch.setattr(P, "_ocr", lambda: OCR())
    found = P.detect_corner_logos_in_images(frames)
    assert len(found) == 1
    assert found[0][0] > 0.8
    assert found[0][1] < 0.1


def test_cover_uses_the_same_cleanup_filter(monkeypatch, tmp_path):
    calls = []

    class Done:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"frame")
        return Done()

    monkeypatch.setattr(P.subprocess, "run", fake_run)
    monkeypatch.setattr(P, "detect_corner_logos_in_images", lambda frames: [])

    # 后面的 PIL 会读到伪图片而失败；这里只验证每次抽帧都已经带清理滤镜。
    with pytest.raises(Exception):
        P.make_cover(Path("source.mp4"), 10, 20, "标题", "林园",
                     tmp_path / "cover.jpg",
                     video_filter="delogo=x=1:y=1:w=80:h=30,crop=852:440:0:0",
                     preferred_time=15)
    assert calls
    for cmd in calls:
        assert "-vf" in cmd
        assert cmd[cmd.index("-vf") + 1] == \
            "delogo=x=1:y=1:w=80:h=30,crop=852:440:0:0"
