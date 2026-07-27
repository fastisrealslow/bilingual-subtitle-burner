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
    落在区间内的行聚成条带，取最靠下的那一条 —— 字幕总在画面最下方那一片
    文字里，上方的图表标注、台标都不该参与摆位决策。
"""

import os
import subprocess
import tempfile

import numpy as np
from PIL import Image

# 近白色。硬字幕正文是白字黑边，正文像素普遍在 230 以上，
# 取 200 给 h264 量化和低码率源片留量。
BRIGHT_LUMA = 200
# 一行里亮像素的占比区间。下限 1% 在 854 宽上约 8px，足以滤掉孤立噪点；
# 上限 90% 用来挡掉整行白（白底 PPT、闪白转场）—— 那是背景不是文字。
MIN_BRIGHT_RATIO = 0.01
MAX_BRIGHT_RATIO = 0.90
# 只在画面下半部分找。上半部分是讲者和图表，嘴部动作和轴标签都会误判。
SEARCH_TOP_RATIO = 0.5
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


def band_top_y(frame_path: str,
               bright_luma: int = BRIGHT_LUMA,
               min_ratio: float = MIN_BRIGHT_RATIO,
               max_ratio: float = MAX_BRIGHT_RATIO,
               search_top_ratio: float = SEARCH_TOP_RATIO) -> int | None:
    """单帧：画面下半部分最靠下那条亮文字带的顶边 y。找不到返回 None。"""
    with Image.open(frame_path) as im:
        a = np.asarray(im.convert("L"), dtype=np.uint8)
    h, w = a.shape
    lo = int(h * search_top_ratio)
    if lo >= h or w == 0:
        return None

    ratio = (a[lo:] >= bright_luma).sum(axis=1) / w
    hit = np.where((ratio >= min_ratio) & (ratio <= max_ratio))[0]
    if hit.size == 0:
        return None

    # 行间空隙的容忍度。实测两行 44px 字号的引言板，上下两行的字形之间空了
    # 30 行暗像素（h=480 的 6.2%）；容忍度给到 3% 时这块板会被切成上下两条，
    # 摆位只躲开了下面那一行，中文照样压在上面那行上。按 8% 走。
    # 宁可多合并：合并的结果是中文摆得更高（更安全），而抬得过头有
    # 「不越过画面中线」那道闸拦着。
    gap = max(6, int(h * 0.08))
    min_height = max(3, int(h * 0.01))
    groups = [g for g in _band_groups(hit, gap) if len(g) >= min_height]
    if not groups:
        return None

    lowest = max(groups, key=lambda g: g[-1])
    return int(lo + lowest[0])


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


def probe_cue_band_top(video: str, start_sec: float, end_sec: float,
                       tmp_dir: str | None = None,
                       frames: int = DEFAULT_FRAMES_PER_CUE,
                       prefix: str = "cue", **kw) -> int | None:
    """这条 cue 期间硬字幕带顶边的**最小** y（最靠上的那一次）。

    取最小值而不是平均：字幕在这几秒里换过行数或字号时，只有按最靠上的
    那一次摆位才能保证整条 cue 全程都不叠字。
    """
    if tmp_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return probe_cue_band_top(video, start_sec, end_sec, tmp,
                                      frames=frames, prefix=prefix, **kw)

    tops = [y for y in (band_top_y(f, **kw) for f in
                        extract_cue_frames(video, start_sec, end_sec,
                                           tmp_dir, frames, prefix))
            if y is not None]
    return min(tops) if tops else None
