"""分屏访谈侦测：在 input 阶段给出默认的 ``cover_crop``。

## 为什么要有这一步

Zoom / Riverside 录的远程访谈是左右分屏：一半是采访者，一半是我们要的主讲人。
封面选帧拿到的是整张分屏图，出来的封面上两个人各占一半，主讲人的脸小到看不清。
帕伯莱那条 ``NVD-m9seDe4`` 就是这么废掉 7 集封面的 —— 用户没传 ``--cover-crop``，
``cover-render`` 老老实实把整张分屏烧成了封面。

这一步只做一件事：**产出一个默认的 ``W:H:X:Y`` 字符串**，交给已有的
``--cover-crop`` 通路去消费。选帧、动态避让、安全区那几道闸门一行都不动。

## 判定不了就退 3，绝不默认取右半

「分屏但认不出哪半是主讲人」和「不是分屏」是两件完全不同的事：

* 不是分屏 → 返回 ``crop=None``，下游保持原有的整帧行为。
* 是分屏但认不出 → 调用方退 3。**不能**猜一个「反正被访者一般在右边」，
  猜错就是把采访者的脸配上主讲人的角标发出去，和硬出没有区别。

## 纯本地，不打外部 API

只用 opencv 随包分发的 haar 级联（``requirements.txt`` 里已经锁了
``opencv-python-headless<5``，封面选帧的人脸预筛用的是同一份）。不下模型文件、
不请求 SiliconFlow。
"""

from __future__ import annotations

import os
import subprocess

# ── 侦测参数（全部只在这里定义，不外露成 CLI / 环境变量）────────────────────
# 命名统一带 SPLIT_DETECT_ 前缀，和封面那边的阈值区分开：那些是出片闸门，
# 这些只决定「默认裁哪半」，两套互不影响。

SPLIT_DETECT_SAMPLE_FRAMES = 5           # 沿片长均匀抽几帧
SPLIT_DETECT_MIN_PAIRED_FRAMES = 4       # 其中至少几帧要是「左右各一张脸」
SPLIT_DETECT_MIN_FACES_PER_FRAME = 2     # 单帧算「双人」的最少人脸数
SPLIT_DETECT_MIN_CENTER_GAP_RATIO = 0.40  # 两张最大人脸的中心 x 距离 / 画宽
SPLIT_DETECT_MAX_CLUSTER_JITTER_RATIO = 0.05  # 人脸簇中心的帧间漂移 / 画宽
SPLIT_DETECT_CLUSTER_RADIUS_RATIO = 0.20  # 归簇半径 / 画宽，超出的脸不算入任一簇
SPLIT_DETECT_MIN_DOMINANCE_RATIO = 1.3   # 主讲人簇得分 / 另一簇，低于此算势均力敌
SPLIT_DETECT_HALF_MIN_RATIO = 0.40       # 主讲人簇中心落在 [0.40, 0.60] 画宽内
SPLIT_DETECT_HALF_MAX_RATIO = 0.60       # 说明分割线判错了，宁可退 3
SPLIT_DETECT_LETTERBOX_LUMA = 24         # 行灰度 98 分位低于此算黑边行
SPLIT_DETECT_LETTERBOX_PERCENTILE = 98   # 用分位而不是最大值：黑边上有零星噪点
SPLIT_DETECT_MAX_MARGIN_RATIO = 0.25     # 上下各裁掉的高度不超过画高的 1/4
SPLIT_DETECT_HAAR_SCALE_FACTOR = 1.1
SPLIT_DETECT_HAAR_MIN_NEIGHBORS = 5

_FFMPEG = os.environ.get("FFMPEG_BIN") or "ffmpeg"

_cascade = None
_cascade_loaded = False


class DetectorUnavailable(RuntimeError):
    """haar 级联加载不了 —— 侦测能力缺失，调用方必须退 3 而不是当成「非分屏」。"""


def _get_cascade():
    global _cascade, _cascade_loaded
    if _cascade_loaded:
        return _cascade
    _cascade_loaded = True
    try:
        import cv2
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(path)
        if not cascade.empty():
            _cascade = cascade
    except (ImportError, AttributeError):
        pass
    return _cascade


# ── 单帧观测 ────────────────────────────────────────────────────────────────

def detect_faces(img_path: str) -> tuple[list, tuple] | None:
    """返回 ``([(x, y, w, h), ...], (W, H))``；图读不出来返回 ``None``。

    级联加载不了时抛 :class:`DetectorUnavailable` —— 这跟「这帧没脸」必须分开，
    前者是能力缺失，后者是观测结果。
    """
    cascade = _get_cascade()
    if cascade is None:
        raise DetectorUnavailable(
            "haar 级联不可用（缺 opencv-python-headless，或装了移除 "
            "CascadeClassifier 的 5.x）")
    import cv2
    img = cv2.imread(img_path)
    if img is None:
        return None
    h, w = img.shape[:2]
    if not h or not w:
        return None
    gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    faces = cascade.detectMultiScale(
        gray, scaleFactor=SPLIT_DETECT_HAAR_SCALE_FACTOR,
        minNeighbors=SPLIT_DETECT_HAAR_MIN_NEIGHBORS)
    boxes = [(int(x), int(y), int(fw), int(fh)) for x, y, fw, fh in faces]
    return boxes, (int(w), int(h))


def letterbox_bars(img_path: str) -> tuple[int, int] | None:
    """量出上下黑边各多少行；图读不出来返回 ``None``。

    margin 取实测黑边高度，不写死数值：Zoom 的分屏画面上下留多少黑边取决于
    录制端的宫格布局，同一个平台不同场次都不一样。
    """
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise DetectorUnavailable(f"黑边测量需要 opencv/numpy（{e}）") from e
    img = cv2.imread(img_path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if not gray.size:
        return None
    dark = np.percentile(gray, SPLIT_DETECT_LETTERBOX_PERCENTILE,
                         axis=1) <= SPLIT_DETECT_LETTERBOX_LUMA
    top = bottom = 0
    for is_dark in dark:
        if not is_dark:
            break
        top += 1
    for is_dark in dark[::-1]:
        if not is_dark:
            break
        bottom += 1
    # 全黑帧：上下会各自数满整帧，那不是黑边，是坏帧
    if top >= len(dark):
        return 0, 0
    return top, bottom


def observe(img_path: str) -> dict | None:
    """一帧的全部观测量，喂给 :func:`split_screen_verdict`。"""
    found = detect_faces(img_path)
    if found is None:
        return None
    faces, size = found
    return {"faces": faces, "size": size, "bars": letterbox_bars(img_path)}


# ── 判定（纯函数，不碰磁盘）─────────────────────────────────────────────────

def _center_x(face) -> float:
    x, _, w, _ = face
    return x + w / 2.0


def _area(face) -> int:
    _, _, w, h = face
    return w * h


def _widest_pair(faces: list) -> tuple | None:
    """按面积取最大两张脸，再按中心 x 排序成 ``(左, 右)``。不够两张返回 ``None``。"""
    if len(faces) < SPLIT_DETECT_MIN_FACES_PER_FRAME:
        return None
    top2 = sorted(faces, key=_area, reverse=True)[:SPLIT_DETECT_MIN_FACES_PER_FRAME]
    return tuple(sorted(top2, key=_center_x))


def _median(values: list) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _not_split(detail: str, **checks) -> dict:
    return {"split_screen": False, "crop": None, "speaker_half": None,
            "detail": detail, "checks": checks}


def _indeterminate(detail: str, **checks) -> dict:
    return {"split_screen": True, "crop": None, "speaker_half": None,
            "detail": detail, "checks": checks}


def crop_string(size: tuple, half: str, margin: int) -> str:
    """``W:H:X:Y``，只留 ``half`` 那一半、上下各切掉 ``margin`` 行。"""
    width, height = size
    half_w = width // 2
    keep_h = height - 2 * margin
    x = half_w if half == "right" else 0
    return f"{half_w}:{keep_h}:{x}:{margin}"


def safe_margin(size: tuple, bars: list, face_tops: list,
                face_bottoms: list) -> int:
    """上下对称裁掉多少行：实测黑边高度，但保证一张检出的脸都不被切到。

    两侧黑边不一样厚时取薄的那一侧 —— 裁切是对称的，按厚的那侧算会从另一侧
    切进画面内容里。
    """
    _, height = size
    measured = [min(top, bottom) for top, bottom in bars] or [0]
    margin = min(measured)
    if face_tops and face_bottoms:
        margin = min(margin, min(face_tops), height - max(face_bottoms))
    margin = min(margin, int(height * SPLIT_DETECT_MAX_MARGIN_RATIO))
    margin = max(0, margin)
    return margin - margin % 2          # 保持裁出的高度是偶数，编码器友好


def split_screen_verdict(frames: list) -> dict:
    """从若干帧的观测量判定分屏与主讲人半区。

    ``frames`` 里每项形如 ``{"faces": [(x, y, w, h), ...],
    "size": (W, H), "bars": (top, bottom)}``。

    返回 ``{"split_screen", "crop", "speaker_half", "detail", "checks"}``：
    ``split_screen`` 为真而 ``crop`` 为 ``None`` 就是「是分屏但认不出主讲人」，
    调用方必须据此退 3。
    """
    frames = [f for f in frames if f and f.get("size")]
    if not frames:
        return _not_split("没有可用的采样帧", sampled_frames=0)

    sizes = {tuple(f["size"]) for f in frames}
    if len(sizes) > 1:
        return _not_split(f"采样帧尺寸不一致（{sorted(sizes)}），判不了分屏",
                          sampled_frames=len(frames))
    size = frames[0]["size"]
    width, height = size

    pairs = []
    for frame in frames:
        pair = _widest_pair(frame["faces"])
        if pair is None:
            continue
        gap = _center_x(pair[1]) - _center_x(pair[0])
        if gap < SPLIT_DETECT_MIN_CENTER_GAP_RATIO * width:
            continue
        pairs.append(pair)

    checks = {"sampled_frames": len(frames), "paired_frames": len(pairs),
              "frame_size": [width, height]}
    if len(pairs) < SPLIT_DETECT_MIN_PAIRED_FRAMES:
        return _not_split(
            f"只有 {len(pairs)}/{len(frames)} 帧是左右分居的双人画面，"
            f"不足 {SPLIT_DETECT_MIN_PAIRED_FRAMES} 帧，按单机位处理", **checks)

    left_cx = [_center_x(p[0]) for p in pairs]
    right_cx = [_center_x(p[1]) for p in pairs]
    jitter = max(max(left_cx) - min(left_cx), max(right_cx) - min(right_cx))
    checks["cluster_jitter_px"] = round(jitter, 1)
    checks["max_jitter_px"] = round(SPLIT_DETECT_MAX_CLUSTER_JITTER_RATIO * width, 1)
    if jitter >= SPLIT_DETECT_MAX_CLUSTER_JITTER_RATIO * width:
        return _not_split(
            f"两个人脸簇的中心在帧间漂移 {jitter:.0f}px，超过画宽的 "
            f"{SPLIT_DETECT_MAX_CLUSTER_JITTER_RATIO:.0%}，判为切镜头而非固定分屏",
            **checks)

    centers = {"left": _median(left_cx), "right": _median(right_cx)}
    radius = SPLIT_DETECT_CLUSTER_RADIUS_RATIO * width
    stats = {side: {"area": 0, "frames": 0, "tops": [], "bottoms": []}
             for side in centers}
    for frame in frames:
        seen = set()
        for face in frame["faces"]:
            cx = _center_x(face)
            side = min(centers, key=lambda s: abs(cx - centers[s]))
            if abs(cx - centers[side]) > radius:
                continue
            bucket = stats[side]
            bucket["area"] += _area(face)
            bucket["tops"].append(face[1])
            bucket["bottoms"].append(face[1] + face[3])
            seen.add(side)
        for side in seen:
            stats[side]["frames"] += 1

    scores = {side: s["area"] * s["frames"] for side, s in stats.items()}
    checks["cluster_center_x"] = {s: round(c, 1) for s, c in centers.items()}
    checks["cluster_area_px2"] = {s: stats[s]["area"] for s in stats}
    checks["cluster_frames"] = {s: stats[s]["frames"] for s in stats}
    checks["cluster_score"] = scores

    strong, weak = sorted(scores, key=lambda s: scores[s], reverse=True)
    if scores[weak] <= 0:
        return _indeterminate(
            f"{weak} 半区的脸面积×出现帧数为 0，两半的对比无从谈起", **checks)
    dominance = scores[strong] / scores[weak]
    checks["dominance_ratio"] = round(dominance, 3)
    checks["min_dominance_ratio"] = SPLIT_DETECT_MIN_DOMINANCE_RATIO
    if dominance < SPLIT_DETECT_MIN_DOMINANCE_RATIO:
        return _indeterminate(
            f"左右两半势均力敌（脸面积×出现帧数之比 {dominance:.2f} < "
            f"{SPLIT_DETECT_MIN_DOMINANCE_RATIO}），认不出哪半是主讲人。"
            f"请显式传 cover_crop", **checks)

    center_ratio = centers[strong] / width
    checks["speaker_center_ratio"] = round(center_ratio, 3)
    checks["half_band"] = [SPLIT_DETECT_HALF_MIN_RATIO, SPLIT_DETECT_HALF_MAX_RATIO]
    if SPLIT_DETECT_HALF_MIN_RATIO <= center_ratio <= SPLIT_DETECT_HALF_MAX_RATIO:
        return _indeterminate(
            f"主讲人簇中心落在画面正中带（{center_ratio:.0%}，"
            f"在 {SPLIT_DETECT_HALF_MIN_RATIO:.0%}–"
            f"{SPLIT_DETECT_HALF_MAX_RATIO:.0%} 之间），左右分割线判不准。"
            f"请显式传 cover_crop", **checks)

    half = "left" if center_ratio < SPLIT_DETECT_HALF_MIN_RATIO else "right"
    bars = [tuple(f["bars"]) for f in frames if f.get("bars")]
    tops = [y for s in stats.values() for y in s["tops"]]
    bottoms = [y for s in stats.values() for y in s["bottoms"]]
    margin = safe_margin(size, bars, tops, bottoms)
    checks["letterbox_bars"] = [list(b) for b in bars]
    checks["margin_px"] = margin
    return {"split_screen": True, "crop": crop_string(size, half, margin),
            "speaker_half": half,
            "detail": f"分屏访谈，主讲人在{'右' if half == 'right' else '左'}半区"
                      f"（簇中心 {center_ratio:.0%} 画宽，得分比 {dominance:.2f}），"
                      f"上下各避开 {margin}px 黑边",
            "checks": checks}


# ── 从视频抽帧 ──────────────────────────────────────────────────────────────

def sample_times(duration_sec: float,
                 count: int = SPLIT_DETECT_SAMPLE_FRAMES) -> list:
    """片长 /(count+1) 处各取一帧，两端都不贴边。"""
    if duration_sec <= 0:
        return []
    step = duration_sec / (count + 1)
    return [round(step * (i + 1), 3) for i in range(count)]


def _extract(video: str, time_sec: float, out_path: str) -> bool:
    cmd = [_FFMPEG, "-y", "-ss", str(max(0.0, time_sec)), "-i", video,
           "-vframes", "1", "-q:v", "2", out_path]
    r = subprocess.run(cmd, capture_output=True)
    return (r.returncode == 0 and os.path.exists(out_path)
            and os.path.getsize(out_path) > 1000)


def detect_from_video(video: str, tmp_dir: str, duration_sec: float) -> dict:
    """抽帧 + 判定。抽不出帧时返回 ``split_screen=False`` 并说明原因。

    抽不出帧不按「侦测失败」处理：那说明片子本身读不了，选帧那一步同样截不出
    候选帧、会自己退出，不存在「悄悄出一张错封面」的风险。级联加载不了就不同了
    —— 片子是好的，只是我们瞎了，那种情况由 :class:`DetectorUnavailable` 上抛。
    """
    os.makedirs(tmp_dir, exist_ok=True)
    frames = []
    times = sample_times(duration_sec)
    for i, t in enumerate(times):
        path = os.path.join(tmp_dir, f"split_probe_{i:02d}.jpg")
        if not _extract(video, t, path):
            # 第一帧就截不出来说明整条片子读不了，别再白跑剩下几次 ffmpeg
            break
        obs = observe(path)
        if obs is not None:
            frames.append(obs)
    if len(frames) < SPLIT_DETECT_MIN_PAIRED_FRAMES:
        print(f"[input] 只抽到 {len(frames)}/{len(times)} 帧可用样本，跳过分屏侦测",
              flush=True)
        return _not_split(
            f"可用采样帧只有 {len(frames)}/{len(times)} 帧，判不了分屏",
            sampled_frames=len(frames), requested_frames=len(times))
    return split_screen_verdict(frames)
