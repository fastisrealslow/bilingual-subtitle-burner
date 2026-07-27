"""scripts/hardsub_probe.py：逐条 cue 定位源片硬字幕带的上沿。

合成帧覆盖判据的边界（干净背景 / 整行白 / 上半部分的台标 / 多行文字块 /
B-roll 亮画面 / 白纸印刷面 / 彩色实景 / 过厚的带），外加一条真跑 ffmpeg 的
抽帧路径。

合成的「文字」一律画成条纹而不是实心块，条纹的疏密按实测源片调：实心块在
行统计上和 B-roll 的亮画面一模一样，正是 CI run 30269220766 里被当成字幕带
的那种东西。各处引用的实测数值见 ``hardsub_probe`` 里两组阈值的注释。
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
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

W, H = 854, 480


def frame(path: Path, boxes, bg: int = 24, solid=(), period: int = 8,
          on: int = 2, colored=()) -> str:
    """造一张灰底帧。

    ``boxes`` 是 (y0, y1, x0, x1) 或 (y0, y1, x0, x1, period, on) 的**文字块**：
    填成 ``on`` 亮 / ``period-on`` 暗的竖条纹。条纹的文字感恒为 ``2 / on``，
    所以 on=2 得 1.0（对上实测小字号对白带的 0.96），on=6 得 0.33（对上实测
    大字号引言板的 0.31），on=13 得 0.15（对上实测白纸印刷面的 0.14）。
    ``solid`` 同样的四元组，填实心亮块 —— 用来模拟 B-roll 亮画面。
    ``colored`` 填条纹，但每 4 根亮条里有 1 根是强偏色的：白像素占比 75%、
    平均彩度 25，对上实测的彩色实景（钞票 88%/13.3、栏杆 91%/22.3）。
    """
    a = np.full((H, W, 3), bg, np.uint8)
    for y0, y1, x0, x1 in solid:
        a[y0:y1, x0:x1] = 255
    for box in boxes:
        y0, y1, x0, x1 = box[:4]
        p, k = box[4:] if len(box) > 4 else (period, on)
        for x in range(x0, x1):
            if (x - x0) % p < k:
                a[y0:y1, x] = 255
    for y0, y1, x0, x1 in colored:
        lit = 0
        for x in range(x0, x1):
            if (x - x0) % period < on:
                # (255, 255, 155) 的亮度 244 仍在 BRIGHT_LUMA 之上，彩度 100
                a[y0:y1, x] = (255, 255, 155) if lit % 4 == 0 else 255
                lit += 1
    Image.fromarray(a).save(path)
    return str(path)


# ── 文字感判别量 ──────────────────────────────────────────────────────────────
# 实测源片（854x480，219.7~224.0s）：真字幕行 0.8~1.2，B-roll 亮画面 0.02~0.29。

def test_textness_separates_text_strokes_from_a_continuous_bright_block():
    """同样的亮占比，文字和实景亮块的跳变数差一个数量级。"""
    text = np.zeros((1, W), bool)
    text[0, ::22] = True                 # 39 笔细画：亮占比 4.6%，跳变 78
    text[0, 1::22] = True                # 加粗到 2px：亮占比 9.1%，与实测同档

    block = np.zeros((1, W), bool)
    for x0 in (60, 300, 500, 700):       # 4 片连续亮块，亮占比 14.4%
        block[0, x0:x0 + 31] = True

    tx_text = HP.row_textness(text)[0]
    tx_block = HP.row_textness(block)[0]

    assert round(text.mean(), 3) == 0.091 and round(block.mean(), 3) == 0.145
    assert tx_text > 0.9, f"文字行的文字感掉到了 {tx_text:.3f}"
    assert tx_block < 0.1, f"亮块的文字感涨到了 {tx_block:.3f}"
    # 阈值必须夹在两者之间，且两侧都有余量
    assert tx_block < HP.MIN_TEXTNESS < tx_text


def test_textness_holds_up_when_there_is_barely_any_text():
    """222.5s 那帧亮占比只剩 0.5%，比值仍有 0.795 —— 字少了照样成立。"""
    row = np.zeros((1, W), bool)
    row[0, 100:104:2] = True
    row[0, 200:204:2] = True
    assert HP.row_textness(row)[0] >= HP.MIN_TEXTNESS


def test_all_dark_rows_have_no_textness():
    assert HP.row_textness(np.zeros((3, W), bool)).tolist() == [0.0, 0.0, 0.0]


def test_band_textness_is_weighted_by_bright_pixels_not_by_row(tmp_path):
    """整带的文字感必须按亮像素加权，不能逐行取均值。

    实测 t=560 那张「手按计算器 + 摊开的财务报表」：报表纸是实心亮面，可它
    的字距边缘凑得出零星几行高分行，逐行取均值是 0.80，按亮像素加权才如实
    反映主体（0.16）。这里造同样的形状 —— 一大片实心亮面加两行细纹边缘。
    """
    a = np.zeros((24, W), bool)
    a[6:18, 100:700] = True              # 实心纸面：跳变 2 / 行
    for y in (0, 1, 2, 21, 22, 23):      # 字距边缘：跳变很多，亮像素很少
        a[y, 100:700:6] = True

    rows = HP.row_textness(a)
    by_row = rows[a.any(axis=1)].mean()
    assert by_row > HP.MIN_TEXTNESS, "逐行均值被两行细纹拽过了线，正是要避开的"
    assert HP.band_textness(a) < HP.MIN_TEXTNESS


def test_whiteness_separates_hardsub_white_from_colourful_scenery():
    """真字幕的亮像素近乎无彩，实景的亮像素带着明显彩度。

    占比和均值这两个条件缺一不可，各有一类只有它拦得住的实景：
      - t=350 办公室亮块：每个像素的彩度都压在「算白」的门槛之下，白像素
        占比 99% 照样过线，是平均彩度 9.5 把它拦下来的。
      - t=80 亮块：绝大多数像素确实是白的，平均彩度只有 3.5，是白像素占比
        98%（差 1 个百分点）把它拦下来的。
    """
    white = np.tile(np.array([255, 255, 255], np.int16), (1, 50, 1))
    scene = np.tile(np.array([255, 180, 90], np.int16), (1, 50, 1))
    office = np.tile(np.array([255, 245, 235], np.int16), (1, 50, 1))
    speckled = white.copy()
    speckled[0, 0] = (255, 55, 55)       # 50 个里混进 1 个强偏色的
    lit = np.ones((1, 50), bool)

    for region, expect_pass in ((white, True), (scene, False),
                                (office, False), (speckled, False)):
        chroma = region.max(axis=2) - region.min(axis=2)
        ratio, mean = HP.band_whiteness(chroma, lit)
        passed = (ratio >= HP.MIN_WHITE_PIXEL_RATIO
                  and mean < HP.MAX_MEAN_CHROMA)
        assert passed is expect_pass, f"白度判成了 {ratio:.0%} / {mean:.1f}"


def test_whiteness_of_a_region_without_bright_pixels_is_zero():
    assert HP.band_whiteness(np.zeros((2, 2), np.int16),
                             np.zeros((2, 2), bool)) == (0.0, 0.0)


# ── 单帧定位 ──────────────────────────────────────────────────────────────────

def test_finds_the_top_edge_of_a_regular_subtitle_band(tmp_path):
    """常规对白字幕：y=408~456，上沿就是 408。"""
    f = frame(tmp_path / "a.png", [(408, 456, 180, 674)])
    assert HP.band_top_y(f) == 408


def test_merges_a_two_line_block_and_returns_the_upper_lines_top(tmp_path):
    """大字号引言板排两行，上沿必须是**上面那行**的顶边。

    只躲开下面那行等于没躲：run 30263406353 成片 00:45 处，中文正好压在
    引言板第二行「AS POSSIBLE」上，就是因为摆位没把整块板算进去。
    两行字形之间空 30 行，是实测引言板的行距。
    """
    f = frame(tmp_path / "b.png", [(330, 352, 100, 750), (382, 404, 250, 600)])
    assert HP.band_top_y(f) == 330


def test_clean_frame_finds_nothing(tmp_path):
    assert HP.band_top_y(frame(tmp_path / "c.png", [])) is None


def test_full_width_white_rows_are_not_text(tmp_path):
    """整行白是白底 PPT / 闪白转场，不是文字，占比上限要挡住它。"""
    f = frame(tmp_path / "d.png", [], solid=[(300, 470, 0, W)])
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
    f = frame(tmp_path / "g.png", [], solid=[(420, 440, 400, 403)])
    assert HP.band_top_y(f) is None


# ── 合理性闸门：不可信就报「没探到」，不硬报一个位置 ──────────────────────────

def test_a_continuous_bright_scene_is_not_mistaken_for_a_subtitle(tmp_path):
    """整片 B-roll 亮画面：亮占比落在区间内，但一行都不像文字。"""
    f = frame(tmp_path / "h.png", [], solid=[(240, 360, 60, 400)])
    scan = HP.scan_band(f)
    assert scan.top_y is None
    assert scan.reject == "no_text_rows"
    assert "文字感最高" in scan.detail


def test_a_band_thicker_than_the_cap_is_not_a_subtitle(tmp_path):
    """字幕带天生是薄的。120px 厚的东西哪怕像文字也不是字幕。"""
    f = frame(tmp_path / "i.png", [(250, 370, 60, 700)])
    scan = HP.scan_band(f)
    assert scan.top_y is None
    assert scan.reject == "band_too_thick"
    assert "超过上限 96px" in scan.detail


def test_the_quote_board_stays_under_the_thickness_cap(tmp_path):
    """定标另一侧：70px 的双行引言板必须照常被认下来。"""
    f = frame(tmp_path / "j.png", [(330, 352, 100, 750), (382, 400, 250, 600)])
    assert HP.scan_band(f).top_y == 330


def test_a_wide_gap_does_not_glue_two_unrelated_regions_together(tmp_path):
    """空隙 40 行时不许并成一条 —— 并了顶边就会被拽到上面那块去。"""
    f = frame(tmp_path / "k.png", [(300, 330, 60, 700), (370, 400, 180, 674)])
    assert HP.scan_band(f).top_y == 370


def test_a_large_font_quote_board_is_still_detected(tmp_path):
    """防阈值回调的锁：大字号引言板的文字感只有 0.33，必须仍被检出。

    实测 t=290 的爱因斯坦 B-roll 上压着两行大字号硬字幕，第二行
    y=342~363（21 行）整带文字感 0.31、白像素占比 100%、平均彩度 2.0。
    大字号笔画粗，单位亮像素摊到的跳变本来就比小字号少 —— 只拿小字号对白带
    （0.96）标定再把阈值提到 0.35，这一整块板就会被漏掉，中文正好压上去。
    """
    f = frame(tmp_path / "big.png", [(342, 363, 120, 730, 24, 6)])
    scan = HP.scan_band(f)

    assert scan.top_y == 342, f"大字号引言板被判成了 {scan.reject}：{scan.detail}"
    # 夹具确实落在实测那一档，不是靠画得比真板子更「像文字」蒙混过关
    bw = np.asarray(Image.open(f).convert("L"))[342:363] >= HP.BRIGHT_LUMA
    assert 0.28 <= HP.band_textness(bw) <= 0.38


def test_printed_text_on_white_paper_is_rejected(tmp_path):
    """白度过得了、文字感过不了：实测 t=560 摊开的财务报表。

    报表纸上的印刷数字白像素占比 100%、平均彩度 2.8~6.4，白度闸门拦不住，
    但纸面是实心亮面，整带文字感只有 0.06~0.16。

    这里照实测的形状造：几条实心纸面，中间夹着字距留出的细纹。细纹那几行
    逐行看确实像文字，能被选进候选带 —— 整带按亮像素加权才 0.16。
    """
    f = frame(tmp_path / "paper.png",
              [(308, 312, 120, 700), (320, 324, 120, 700),
               (332, 336, 120, 700)],
              solid=[(300, 308, 120, 700), (312, 320, 120, 700),
                     (324, 332, 120, 700)])
    scan = HP.scan_band(f)

    assert scan.top_y is None
    assert scan.reject == "band_not_texty"
    assert "整带文字感" in scan.detail


def test_colourful_scenery_is_rejected_even_when_it_looks_texty(tmp_path):
    """文字感过得了、白度过不了：实测的钞票 / 城市夜景 / 栏杆。

    这些实景的亮像素带着明显彩度（白像素占比 88%~91%、平均彩度 13~22），
    而硬字幕的白字近乎无彩。条纹画得和真字幕一样细，只有颜色不同。
    """
    f = frame(tmp_path / "scene.png", [], colored=[(300, 340, 120, 700)])
    scan = HP.scan_band(f)

    assert scan.top_y is None
    assert scan.reject == "band_not_white"
    assert "平均彩度" in scan.detail


def test_the_two_gates_are_orthogonal(tmp_path):
    """两道闸各挡一类假阳性，谁也替不了谁 —— 所以必须同时成立。"""
    paper = HP.scan_band(frame(
        tmp_path / "p2.png",
        [(308, 312, 120, 700), (320, 324, 120, 700), (332, 336, 120, 700)],
        solid=[(300, 308, 120, 700), (312, 320, 120, 700),
               (324, 332, 120, 700)]))
    scene = HP.scan_band(frame(tmp_path / "s2.png", [],
                               colored=[(300, 340, 120, 700)]))
    assert (paper.reject, scene.reject) == ("band_not_texty", "band_not_white")


def test_a_band_topping_out_above_the_midline_is_rejected(tmp_path):
    """兜底闸：文字感和厚度都放过了，位置仍不合理就报「没探到」。"""
    f = frame(tmp_path / "l.png", [(240, 262, 60, 700)])
    scan = HP.scan_band(f)
    assert scan.top_y is None
    assert scan.reject == "band_above_midline"
    assert "中线 240" in scan.detail


def test_reproduces_the_ci_run_30269220766_misjudgement(tmp_path):
    """线上误判复现：y=240~360 亮画面 + 40 行全黑 + y=416~432 真字幕。

    PR #6 把这三段并成一条带、顶边报到 240，MarginV 算出 264 越过画面中线，
    assemble 阶段退 2。现在必须只认最下面那条真字幕。
    """
    f = frame(tmp_path / "m.png",
              [(416, 432, 180, 674)],            # 真字幕带
              solid=[(240, 360, 60, 500)])       # B-roll 亮画面
    top = HP.band_top_y(f)
    assert top == 416, f"上沿报到了 {top}"
    assert top != 240
    # 480 - 416 + 24 = 88，离中线 240 很远，不会再走退 2 那条路
    assert 480 - top + 24 < 480 // 2


# ── 跨帧取最靠上 ──────────────────────────────────────────────────────────────

def test_probe_takes_the_topmost_top_across_frames(tmp_path, monkeypatch):
    """一条 cue 里字幕换过位置时，只有按最靠上那次摆位才全程不叠字。"""
    monkeypatch.setattr(HP, "extract_cue_frames",
                        lambda *a, **k: ["f0", "f1", "f2"])
    monkeypatch.setattr(HP, "scan_band", lambda f, **k: HP.BandScan(
        {"f0": 408, "f1": 330, "f2": 412}[f], None, ""))
    assert HP.probe_cue_band_top("v.mp4", 0.0, 5.0, str(tmp_path)) == 330


def test_probe_returns_none_when_no_frame_has_a_band(tmp_path, monkeypatch):
    monkeypatch.setattr(HP, "extract_cue_frames", lambda *a, **k: ["f0", "f1"])
    monkeypatch.setattr(HP, "scan_band",
                        lambda f, **k: HP.BandScan(None, "no_band", "空"))
    assert HP.probe_cue_band_top("v.mp4", 0.0, 5.0, str(tmp_path)) is None


def test_probe_reports_why_it_found_nothing(tmp_path, monkeypatch):
    """回落理由要能一路带到调用方的日志里，不能只剩一个 None。"""
    scans = iter([HP.BandScan(None, "no_text_rows", "31 个亮行没有一行像文字"),
                  HP.BandScan(None, "band_too_thick", "有 120px")])
    monkeypatch.setattr(HP, "extract_cue_frames", lambda *a, **k: ["f0", "f1"])
    monkeypatch.setattr(HP, "scan_band", lambda f, **k: next(scans))

    top, note = HP.probe_cue_band("v.mp4", 0.0, 5.0, str(tmp_path))
    assert top is None
    assert "no_text_rows：31 个亮行没有一行像文字" in note
    assert "band_too_thick：有 120px" in note


def test_probe_says_so_when_no_frame_came_out(tmp_path, monkeypatch):
    monkeypatch.setattr(HP, "extract_cue_frames", lambda *a, **k: [])
    top, note = HP.probe_cue_band("v.mp4", 0.0, 5.0, str(tmp_path))
    assert top is None and "一帧都没抽出来" in note


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
    """前 6s 小字号一行在 y=412，后 6s 大字号两行在 y=330 / y=362。

    照 PR #6 的原样用 DejaVuSans-Bold 的全大写文本 —— 源片 t=290 那块引言板
    确实是加粗全大写。只有两处按实测改：

      - 字号 44 → 30。实测那两行字形各占 19 / 21 行（y=314~333、y=342~363），
        44 号在 480 高的画面上要占 46 行，是真板子的两倍多。粗一倍的笔画把
        整带文字感从实测的 0.31 压到 0.18，比白纸印刷面（≤0.16）还低，那不
        是「大字号硬字幕」而是「实心亮面」。30 号量出来是 0.28，对得上。
      - 补 borderw=2 的黑边。硬字幕都是描边的，源片也是。

    两行之间隔 32px（实测是 9 行），仍在合并容忍度之内，整块 y=330~384 也在
    厚度上限之下。
    """
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
         f"color=c=0x181818:s={W}x{H}:d=12,"
         f"drawtext=fontfile={FONT}:text='PATIENCE IS NOT A VIRTUE':"
         f"fontsize=24:fontcolor=white:borderw=2:bordercolor=black:"
         f"x=(w-text_w)/2:y=412:enable='lt(t\\,6)',"
         f"drawtext=fontfile={FONT}:text='EVERYTHING SHOULD BE MADE AS SIMPLE':"
         f"fontsize=30:fontcolor=white:borderw=2:bordercolor=black:"
         f"x=(w-text_w)/2:y=330:enable='gte(t\\,6)',"
         f"drawtext=fontfile={FONT}:text='AS POSSIBLE':"
         f"fontsize=30:fontcolor=white:borderw=2:bordercolor=black:"
         f"x=(w-text_w)/2:y=362:enable='gte(t\\,6)'",
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
