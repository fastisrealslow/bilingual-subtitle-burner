"""林园人物参考照核验与清理后 OCR 角标复检。"""

import json
import shutil
import subprocess
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


def test_identity_prompt_counts_target_when_host_is_also_present(monkeypatch,
                                                                  tmp_path):
    reference = tmp_path / "reference.jpg"
    frame = tmp_path / "frame.jpg"
    reference.write_bytes(b"reference")
    frame.write_bytes(b"frame")
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return (b'{"choices":[{"message":{"content":"'
                    b'{\\"same_person_frames\\":[1],'
                    b'\\"different_person_frames\\":[],'
                    b'\\"uncertain_frames\\":[],'
                    b'\\"best_cover_frame\\":1,'
                    b'\\"confidence\\":0.95}"}}]}')

    def fake_urlopen(req, timeout):
        captured["payload"] = req.data.decode()
        return Response()

    monkeypatch.setattr(P.urllib.request, "urlopen", fake_urlopen)
    P._call_identity_vlm(reference, [frame], "林园", "sk-test")
    payload = json.loads(captured["payload"])
    prompt = payload["messages"][0]["content"][-1]["text"]
    assert "同时出现主持人" in prompt
    assert "只要目标人物也在场" in prompt
    assert "目标人物完全不在画面中" in prompt


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


def test_brand_watermark_filter_places_asset_at_top_right():
    vf = P.brand_overlay_filter("crop=1280:720:0:0", 1280, 720)
    assert "scale=192:-1" in vf
    assert "overlay=x=main_w-overlay_w-25:y=14" in vf
    assert "colorchannelmixer=aa=0.68" in vf
    assert P.brand_watermark_path().name == "yuanlai-snowball-watermark.png"


def test_brand_region_excludes_only_the_expected_top_right_area():
    assert P._inside_brand_watermark_region(
        (0.86, 0.03, 0.97, 0.12), 1280, 720)
    assert not P._inside_brand_watermark_region(
        (0.03, 0.03, 0.15, 0.10), 1280, 720)
    assert not P._inside_brand_watermark_region(
        (0.86, 0.70, 0.97, 0.80), 1280, 720)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
                    reason="需要 ffmpeg/ffprobe")
def test_ffmpeg_brand_overlay_preserves_video_and_audio(tmp_path):
    cv2 = pytest.importorskip("cv2")
    src = tmp_path / "source.mp4"
    out = tmp_path / "branded.mp4"
    frame = tmp_path / "frame.png"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=#202020:s=640x360:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(src),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
        "-loop", "1", "-framerate", "30", "-i",
        str(P.brand_watermark_path()),
        "-filter_complex", P.brand_overlay_filter("null", 640, 360),
        "-map", "[outv]", "-map", "0:a:0", "-c:v", "libx264",
        "-c:a", "aac", "-t", "2", "-shortest", str(out),
    ], check=True)
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,width,height", "-of", "csv=p=0", str(out),
    ], check=True, capture_output=True, text=True).stdout
    assert "video,640,360" in probe
    assert "audio" in probe
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-ss", "0.5", "-i",
        str(out), "-frames:v", "1", str(frame),
    ], check=True)
    image = cv2.imread(str(frame))
    background = image[40:100, 20:120].mean()
    watermark = image[8:90, 500:632]
    assert np.percentile(watermark, 95) > background + 30


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
