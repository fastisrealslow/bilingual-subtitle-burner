"""封面选帧的裁切（steps/step7_cover.py）。

很多 YouTube/archive.org 源片底部烧死了英文硬字幕，不裁就会原样留在成品
封面上。除了成品那一帧，人脸预筛和 VLM 校验用的候选帧也必须用同一个裁切，
否则「预筛看到的画面」和「成品封面」根本不是同一张图。
"""

import shutil
import subprocess

import pytest
from PIL import Image

import step7_cover as C

HAS_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture
def recorded_ffmpeg(monkeypatch):
    """拦下 subprocess.run，记录 ffmpeg 参数并伪造一张够大的产物。"""
    calls = []

    class Done:
        returncode = 0

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        with open(cmd[-1], "wb") as f:
            f.write(b"\0" * 2048)
        return Done()

    monkeypatch.setattr(C.subprocess, "run", fake_run)
    return calls


# ── extract_frame ───────────────────────────────────────────────────────────

def test_crop_is_passed_to_ffmpeg(tmp_path, recorded_ffmpeg):
    assert C.extract_frame("v.mp4", 12.0, str(tmp_path / "f.jpg"),
                           crop="854:396:0:0")
    cmd = recorded_ffmpeg[0]
    assert "-vf" in cmd
    assert cmd[cmd.index("-vf") + 1] == "crop=854:396:0:0"


def test_no_crop_leaves_command_untouched(tmp_path, recorded_ffmpeg):
    assert C.extract_frame("v.mp4", 12.0, str(tmp_path / "f.jpg"))
    assert "-vf" not in recorded_ffmpeg[0]


def test_empty_crop_is_treated_as_no_crop(tmp_path, recorded_ffmpeg):
    C.extract_frame("v.mp4", 12.0, str(tmp_path / "f.jpg"), crop="")
    assert "-vf" not in recorded_ffmpeg[0]


# ── 候选帧走的是同一个 crop ──────────────────────────────────────────────────

def test_geometric_candidates_use_same_crop(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(C, "extract_frame",
                        lambda v, t, o, crop=None: seen.append(crop) or False)
    with pytest.raises(SystemExit):      # 一帧都没截出来，按挑不出封面处理
        C.pick_best_frame_geometric("v.mp4", 0.0, 60.0, str(tmp_path),
                                    candidates=4, crop="854:396:0:0")
    assert seen == ["854:396:0:0"] * 4


def test_vision_candidates_use_same_crop(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(C, "extract_frame",
                        lambda v, t, o, crop=None: seen.append(crop) or False)
    assert C.pick_best_frame_vision("v.mp4", 0.0, 60.0, "芒格", "sk-test",
                                    "m", str(tmp_path), candidates=4,
                                    crop="854:396:0:0") is None
    assert seen == ["854:396:0:0"] * 4


# ── 真跑一遍 ffmpeg ─────────────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_crop_really_removes_burned_subtitle_band(tmp_path):
    """下半部分带白色「硬字幕」的 854x480 源片，裁完只剩上方 396px 的干净画面。"""
    src = Image.new("RGB", (854, 480), (60, 60, 60))
    for y in range(408, 456):            # 实测芒格源片的英文字幕带位置
        for x in range(100, 754):
            src.putpixel((x, y), (255, 255, 255))
    still = tmp_path / "still.png"
    src.save(still)

    video = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loop", "1", "-i", str(still), "-t", "2",
         "-r", "10", "-c:v", "libx264", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True)

    uncropped = tmp_path / "full.jpg"
    assert C.extract_frame(str(video), 1.0, str(uncropped))
    full = Image.open(uncropped).convert("L")
    assert full.size == (854, 480)
    # 不裁的话字幕带就在成品里
    assert full.crop((0, 408, 854, 456)).getextrema()[1] > 200

    cropped = tmp_path / "cropped.jpg"
    assert C.extract_frame(str(video), 1.0, str(cropped), crop="854:396:0:0")
    out = Image.open(cropped).convert("L")
    assert out.size == (854, 396)
    # 裁完整张图都没有接近白色的像素 —— 字幕带整条被切掉了
    assert out.getextrema()[1] < 150
