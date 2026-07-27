"""scripts/hardsub_probe.py：逐条 cue 定位源片硬字幕带的上沿。

合成帧覆盖判据的四类边界（干净背景 / 整行白 / 上半部分的台标 / 多行文字块），
外加一条真跑 ffmpeg 的抽帧路径。
"""

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import hardsub_probe as HP                       # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None

W, H = 854, 480
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def frame(path: Path, boxes, bg: int = 24) -> str:
    """造一张灰底帧，boxes 是 (y0, y1, x0, x1) 的白块列表。"""
    a = np.full((H, W), bg, np.uint8)
    for y0, y1, x0, x1 in boxes:
        a[y0:y1, x0:x1] = 255
    Image.fromarray(a).save(path)
    return str(path)


# ── 单帧定位 ──────────────────────────────────────────────────────────────────

def test_finds_the_top_edge_of_a_regular_subtitle_band(tmp_path):
    """常规对白字幕：y=408~456，上沿就是 408。"""
    f = frame(tmp_path / "a.png", [(408, 456, 180, 674)])
    assert HP.band_top_y(f) == 408


def test_merges_a_two_line_block_and_returns_the_upper_lines_top(tmp_path):
    """大字号引言板排两行，上沿必须是**上面那行**的顶边。

    只躲开下面那行等于没躲：run 30263406353 成片 00:45 处，中文正好压在
    引言板第二行「AS POSSIBLE」上，就是因为摆位没把整块板算进去。
    """
    f = frame(tmp_path / "b.png", [(330, 362, 100, 750), (392, 424, 250, 600)])
    assert HP.band_top_y(f) == 330


def test_clean_frame_finds_nothing(tmp_path):
    assert HP.band_top_y(frame(tmp_path / "c.png", [])) is None


def test_full_width_white_rows_are_not_text(tmp_path):
    """整行白是白底 PPT / 闪白转场，不是文字，占比上限要挡住它。"""
    f = frame(tmp_path / "d.png", [(300, 470, 0, W)])
    assert HP.band_top_y(f) is None


def test_text_in_the_upper_half_is_ignored(tmp_path):
    """上半部分只有台标时不该报位置 —— 讲者面部和图表轴标签都在那儿。"""
    f = frame(tmp_path / "e.png", [(100, 140, 20, 200)])
    assert HP.band_top_y(f) is None


def test_picks_the_lowest_band_when_two_are_far_apart(tmp_path):
    """下半部分有两块相距很远的文字时，字幕是最靠下那块。"""
    f = frame(tmp_path / "f.png", [(260, 290, 40, 300),      # 图表标注
                                   (408, 456, 180, 674)])    # 字幕
    assert HP.band_top_y(f) == 408


def test_a_few_stray_bright_pixels_are_not_a_band(tmp_path):
    """孤立噪点：只有 3 列亮像素，低于占比下限。"""
    f = frame(tmp_path / "g.png", [(420, 440, 400, 403)])
    assert HP.band_top_y(f) is None


# ── 跨帧取最靠上 ──────────────────────────────────────────────────────────────

def test_probe_takes_the_topmost_top_across_frames(tmp_path, monkeypatch):
    """一条 cue 里字幕换过位置时，只有按最靠上那次摆位才全程不叠字。"""
    monkeypatch.setattr(HP, "extract_cue_frames",
                        lambda *a, **k: ["f0", "f1", "f2"])
    monkeypatch.setattr(HP, "band_top_y",
                        lambda f, **k: {"f0": 408, "f1": 330, "f2": 412}[f])
    assert HP.probe_cue_band_top("v.mp4", 0.0, 5.0, str(tmp_path)) == 330


def test_probe_returns_none_when_no_frame_has_a_band(tmp_path, monkeypatch):
    monkeypatch.setattr(HP, "extract_cue_frames", lambda *a, **k: ["f0", "f1"])
    monkeypatch.setattr(HP, "band_top_y", lambda f, **k: None)
    assert HP.probe_cue_band_top("v.mp4", 0.0, 5.0, str(tmp_path)) is None


def test_probe_survives_frames_that_all_failed_to_extract(tmp_path, monkeypatch):
    monkeypatch.setattr(HP, "extract_cue_frames", lambda *a, **k: [])
    assert HP.probe_cue_band_top("v.mp4", 0.0, 5.0, str(tmp_path)) is None


# ── 抽帧 ──────────────────────────────────────────────────────────────────────

def test_zero_length_cue_extracts_nothing(tmp_path):
    assert HP.extract_cue_frames("v.mp4", 3.0, 3.0, str(tmp_path)) == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg")
def test_extract_cue_frames_honours_the_frame_budget(tmp_path):
    """全片几十条 cue，每条抽几帧是成本参数，必须真的按数量走。"""
    video = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"color=c=gray:s={W}x{H}:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", str(video)],
        check=True, capture_output=True)

    assert len(HP.extract_cue_frames(str(video), 0.0, 5.0, str(tmp_path),
                                     frames=2, prefix="two")) == 2
    assert len(HP.extract_cue_frames(str(video), 0.0, 5.0, str(tmp_path),
                                     frames=5, prefix="five")) == 5


# ── 真跑 ffmpeg：两种高度的硬字幕带 ───────────────────────────────────────────

def make_two_height_source(path: Path) -> None:
    """前 6s 小字号一行在 y=412，后 6s 大字号两行在 y=330 / y=392。"""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"color=c=0x181818:s={W}x{H}:d=12,"
         f"drawtext=fontfile={FONT}:text='PATIENCE IS NOT A VIRTUE':"
         f"fontsize=30:fontcolor=white:x=(w-text_w)/2:y=412:enable='lt(t\\,6)',"
         f"drawtext=fontfile={FONT}:text='EVERYTHING SHOULD BE MADE AS SIMPLE':"
         f"fontsize=44:fontcolor=white:x=(w-text_w)/2:y=330:enable='gte(t\\,6)',"
         f"drawtext=fontfile={FONT}:text='AS POSSIBLE':"
         f"fontsize=44:fontcolor=white:x=(w-text_w)/2:y=392:enable='gte(t\\,6)'",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "8",
         "-c:a", "aac", str(path)],
        check=True, capture_output=True)


@pytest.mark.skipif(not HAS_FFMPEG or not Path(FONT).is_file(),
                    reason="需要 ffmpeg 和 DejaVu 字体")
def test_probe_tracks_the_hardsub_moving_up_over_time(tmp_path):
    """同一条源片，两个时段的硬字幕高度不同，探测必须各报各的。"""
    video = tmp_path / "src.mp4"
    make_two_height_source(video)

    early = HP.probe_cue_band_top(str(video), 0.0, 6.0, str(tmp_path))
    late = HP.probe_cue_band_top(str(video), 6.0, 12.0, str(tmp_path))

    assert early == 412
    assert late == 330
    assert late < early, "引言板那段的上沿必须比对白段更靠上"
