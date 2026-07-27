#!/usr/bin/env python3
"""按时间区间探测源片烧死的硬字幕带落在哪一行，供逐条 cue 动态避让。

和 ``scripts/detect_burned_subs.py`` 的分工：
    那个是全片一次性的「有没有硬字幕、什么语种、要不要烧」，采样跨越整片，
    输出的是全片平均位置。这个只回答一个很窄的问题 ——
    「**这一条 cue 的这几秒里**，硬字幕带的上沿在第几行」。

为什么必须逐条看：
    实测 854x480 的芒格源片，常规对白的硬字幕落在 y≈408~456，
    但引言板那几段换了明显更大的字号、排两行、整体位置更高（y≈330~400）。
    全片一个固定 MarginV 必然二选一：贴着对白摆就会被引言板压住，
    按引言板摆则全片对白都悬在半空。

判据（纯本地、零成本）：
    硬字幕是描黑边的近白色文字，所以逐行统计「亮像素占该行宽度的比例」：
      - 比例太低 → 是干净背景
      - 比例太高 → 是整行白（白色 PPT、过曝、闪白转场），不是文字
    光看亮度不够 —— B-roll 的亮画面同样能落进这个区间，所以再逐行看「文字
    感」（见 ``MIN_TEXTNESS``），把连续亮块剔掉。剩下的行聚成条带，超过厚度
    上限的条带不认（见 ``MAX_BAND_HEIGHT_RATIO``），取最靠下的那一条 ——
    字幕总在画面最下方那一片文字里，上方的图表标注、台标都不该参与摆位决策。

拿不出可信条带时一律返回「没探到」，让调用方回落到固定默认值并记日志。
把误判的位置当真值用出去，比认怂回落危险得多：CI run 30269220766 就是把
y=240 的 B-roll 亮画面当成字幕带，把中文顶到了画面中线以上。
"""

import os
import subprocess
import tempfile
from collections import namedtuple

import numpy as np
from PIL import Image

# 近白色。硬字幕正文是白字黑边，正文像素普遍在 230 以上，
# 取 200 给 h264 量化和低码率源片留量。
BRIGHT_LUMA = 200
# 一行里亮像素的占比区间。下限 1% 在 854 宽上约 8px，足以滤掉孤立噪点；
# 上限 90% 用来挡掉整行白（白底 PPT、闪白转场）—— 那是背景不是文字。
MIN_BRIGHT_RATIO = 0.01
MAX_BRIGHT_RATIO = 0.90
# 文字感：把该行按 BRIGHT_LUMA 二值化，数相邻列 bool 不同的次数，再除以该行
# 亮像素个数。文字是细笔画，单位亮像素摊到的跳变多；实景亮块连续成片，跳变
# 极少。两类差一个数量级，0.35 两侧都有很大余量。
#
# 实测源片（854x480，219.7 / 221.0 / 222.5 / 224.0 四帧）：
#   真字幕带 y=421~435      亮占比 8%~20%   文字感 0.35~1.29（正文行普遍 >1.0）
#   B-roll 亮画面 y=240~360  亮占比 8%~40%   文字感 0.02~0.29
# 222.5s 那帧画面里几乎没字，亮占比只剩 0.5%，文字感仍有 1.00 —— 字少时该
# 判别量照样成立，这正是它比「亮占比」可靠的地方。
MIN_TEXTNESS = 0.35
# 只在画面下半部分找。上半部分是讲者和图表，嘴部动作和轴标签都会误判。
SEARCH_TOP_RATIO = 0.5
# 条带厚度上限。字幕带天生是薄的：实测常规对白带约 15px（480 的 3%），
# PR #6 处理的大字号双行引言板约 70px（15%，含两行之间的行距）。上限取
# 20%（480 上是 96px）—— 容得下双行引言板，又挡得住 CI run 30269220766 里
# 那片 120px 厚的 B-roll 亮画面。
MAX_BAND_HEIGHT_RATIO = 0.20
# 行间空隙的容忍度。实测两行 44px 字号的引言板，上下两行的字形之间空了
# 30 行暗像素（h=480 的 6.2%），不容忍这点空隙就会把一块板切成上下两条，
# 摆位只躲开下面那一行。取 7%（480 上是 33 行）：30 行的引言板照常合并，
# 而误判那帧里真字幕与上方亮画面之间 40 行的空隙不会被跨过去。
BAND_GAP_RATIO = 0.07
# 每条 cue 抽几帧。全片几十条 cue，每条 3 帧已经能覆盖「字幕换了一次」，
# 再多纯粹是给 ffmpeg 加班。
DEFAULT_FRAMES_PER_CUE = 3


def _band_groups(rows: np.ndarray, gap: int) -> list:
    """把命中的行号切成连续段，行间空隙不超过 gap 的算同一段。

    多行字幕的行间距会留出几行暗像素，容忍这点空隙才能把一块两行的
    引言板当成一条带，而不是上下两条各自算摆位。
    """
    groups, cur = [], [rows[0]]
    for r in rows[1:]:
        if r - cur[-1] <= gap:
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    return groups


def row_textness(bw: np.ndarray) -> np.ndarray:
    """逐行「横向亮暗跳变次数 ÷ 该行亮像素数」。全暗的行记 0。"""
    trans = (bw[:, 1:] != bw[:, :-1]).sum(axis=1)
    bright = bw.sum(axis=1)
    return np.divide(trans, bright, out=np.zeros(len(bw), dtype=float),
                     where=bright > 0)


# top_y 为 None 时 reject 说明是哪道闸没过，detail 带上实测数值，供调用方
# 把回落理由原样写进日志 —— 回落可以，闷声回落不行。
BandScan = namedtuple("BandScan", "top_y reject detail")


def scan_band(frame_path: str,
              bright_luma: int = BRIGHT_LUMA,
              min_ratio: float = MIN_BRIGHT_RATIO,
              max_ratio: float = MAX_BRIGHT_RATIO,
              search_top_ratio: float = SEARCH_TOP_RATIO,
              min_textness: float = MIN_TEXTNESS,
              max_height_ratio: float = MAX_BAND_HEIGHT_RATIO) -> BandScan:
    """单帧：画面下半部分最靠下那条**可信**字幕带的顶边 y，附判定理由。"""
    with Image.open(frame_path) as im:
        a = np.asarray(im.convert("L"), dtype=np.uint8)
    h, w = a.shape
    lo = int(h * search_top_ratio)
    if lo >= h or w == 0:
        return BandScan(None, "frame_too_small", f"画面 {w}x{h} 没有下半部分")

    bw = a[lo:] >= bright_luma
    ratio = bw.sum(axis=1) / w
    bright_rows = (ratio >= min_ratio) & (ratio <= max_ratio)
    if not bright_rows.any():
        return BandScan(None, "no_bright_rows",
                        f"下半部分没有亮占比落在 "
                        f"{min_ratio:.0%}~{max_ratio:.0%} 的行")

    # 文字感闸门必须在聚成条带**之前**生效：亮画面的行先剔掉，才不会被
    # 拿去和下方真字幕连成一条、把顶边一路拽到画面中线。
    textness = row_textness(bw)
    hit = np.where(bright_rows & (textness >= min_textness))[0]
    if hit.size == 0:
        return BandScan(None, "no_text_rows",
                        f"{int(bright_rows.sum())} 个亮行没有一行像文字"
                        f"（文字感最高 {textness[bright_rows].max():.2f}，"
                        f"阈值 {min_textness}）")

    gap = max(6, int(h * BAND_GAP_RATIO))
    min_height = max(3, int(h * 0.01))
    max_height = int(h * max_height_ratio)
    groups = [g for g in _band_groups(hit, gap) if len(g) >= min_height]
    thin = [g for g in groups if g[-1] - g[0] + 1 <= max_height]
    if not thin:
        if not groups:
            return BandScan(None, "no_band", "像文字的行凑不成一条够高的带")
        thickest = max(groups, key=lambda g: g[-1] - g[0])
        return BandScan(None, "band_too_thick",
                        f"最厚的候选带 y={lo + thickest[0]}~{lo + thickest[-1]}"
                        f" 有 {thickest[-1] - thickest[0] + 1}px，"
                        f"超过上限 {max_height}px")

    lowest = max(thin, key=lambda g: g[-1])
    top = int(lo + lowest[0])
    # 画面中线以上不可能是字幕带。文字感和厚度都放过的东西还落在这儿，
    # 说明判据在这一帧上失灵了，宁可报「没探到」也不要拿它去摆位。
    if top <= h // 2:
        return BandScan(None, "band_above_midline",
                        f"候选带上沿 y={top} 落在画面中线 {h // 2} 之上")
    return BandScan(top, None, "")


def band_top_y(frame_path: str, **kw) -> int | None:
    """单帧：画面下半部分最靠下那条亮文字带的顶边 y。找不到返回 None。"""
    return scan_band(frame_path, **kw).top_y


def extract_cue_frames(video: str, start_sec: float, end_sec: float,
                       tmp_dir: str, frames: int = DEFAULT_FRAMES_PER_CUE,
                       prefix: str = "cue") -> list:
    """在 [start, end) 内均匀抽帧，两头各让开 10% 避开淡入淡出的中间态。"""
    span = max(0.0, end_sec - start_sec)
    if span <= 0 or frames <= 0:
        return []
    a, b = start_sec + span * 0.1, end_sec - span * 0.1
    times = [a] if frames == 1 else [a + (b - a) * i / (frames - 1)
                                     for i in range(frames)]

    paths = []
    for i, t in enumerate(times):
        p = os.path.join(tmp_dir, f"{prefix}_{i:02d}.png")
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", video,
             "-frames:v", "1", p, "-y"],
            capture_output=True)
        if r.returncode == 0 and os.path.isfile(p):
            paths.append(p)
    return paths


def probe_cue_band(video: str, start_sec: float, end_sec: float,
                   tmp_dir: str | None = None,
                   frames: int = DEFAULT_FRAMES_PER_CUE,
                   prefix: str = "cue", **kw) -> tuple:
    """这条 cue 期间硬字幕带顶边的**最小** y，以及探不到时的理由。

    取最小值而不是平均：字幕在这几秒里换过行数或字号时，只有按最靠上的
    那一次摆位才能保证整条 cue 全程都不叠字。

    返回 ``(top_y, note)``：探到时 note 为空串；探不到时 note 汇总各帧没过
    哪道闸、实测值多少，调用方要把它原样写进日志。
    """
    if tmp_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return probe_cue_band(video, start_sec, end_sec, tmp,
                                  frames=frames, prefix=prefix, **kw)

    scans = [scan_band(f, **kw) for f in
             extract_cue_frames(video, start_sec, end_sec,
                                tmp_dir, frames, prefix)]
    tops = [s.top_y for s in scans if s.top_y is not None]
    if tops:
        return min(tops), ""
    if not scans:
        return None, "这条 cue 一帧都没抽出来"
    seen = list(dict.fromkeys(f"{s.reject}：{s.detail}" for s in scans))
    return None, "；".join(seen)


def probe_cue_band_top(video: str, start_sec: float, end_sec: float,
                       tmp_dir: str | None = None,
                       frames: int = DEFAULT_FRAMES_PER_CUE,
                       prefix: str = "cue", **kw) -> int | None:
    """``probe_cue_band`` 只要顶边那一半。"""
    return probe_cue_band(video, start_sec, end_sec, tmp_dir,
                          frames=frames, prefix=prefix, **kw)[0]
