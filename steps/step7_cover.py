#!/usr/bin/env python3
"""
step7_cover.py — 封面生成（Step 7）

策略：
  1. 从【原始视频】在金句时间段内均匀截取 N 帧（默认 24，避开首尾各 3 秒）
  2. 调用 LLM vision 逐帧识别哪帧是主讲人（根据外貌特征描述）的大特写
  3. 选 LLM 认定是主讲人且清晰度最高的帧
  4. 叠加渐变遮罩 + 标题文字 + 说话人标签
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sf_client  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageStat
except ImportError:
    print("[cover] 缺少 Pillow", file=sys.stderr)
    sys.exit(1)


FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def find_font(size: int):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _find_ffmpeg() -> str:
    """查找 ffmpeg 可执行路径"""
    import shutil
    candidates = ["ffmpeg",
                  "/home/node/.local/bin/ffmpeg",
                  "/usr/local/bin/ffmpeg",
                  "/usr/bin/ffmpeg"]
    for c in candidates:
        if shutil.which(c) or os.path.isfile(c):
            return c
    return "ffmpeg"

_FFMPEG = _find_ffmpeg()


def extract_frame(video: str, time_sec: float, output: str,
                  crop: str | None = None) -> bool:
    """截一帧。``crop`` 是 ffmpeg crop 滤镜的 ``W:H:X:Y``，用于切掉源片
    底部烧死的英文硬字幕等干扰区域。"""
    cmd = [_FFMPEG, "-y", "-ss", str(max(0, time_sec)), "-i", video]
    if crop:
        cmd += ["-vf", f"crop={crop}"]
    cmd += ["-vframes", "1", "-q:v", "2", output]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0 and os.path.exists(output) and os.path.getsize(output) > 1000


def image_to_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def image_sharpness(path: str) -> float:
    try:
        img = Image.open(path).convert("L").resize((160, 90))
        sharp = img.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(sharp)
        # 方差 = stddev^2，越大越清晰
        return stat.stddev[0] ** 2
    except Exception:
        return 0.0


def image_brightness(path: str) -> float:
    try:
        img = Image.open(path).convert("L").resize((160, 90))
        return ImageStat.Stat(img).mean[0]
    except Exception:
        return 128.0



# 颜色关键词 → speaker_color 映射（从 --speaker-desc 推断）
COLOR_KEYWORDS = {
    "blue": ["蓝", "藏青", "青", "navy", "blue"],
    # 以下颜色目前无专用阈值，会直接走 vision（speaker_color=other）
    "other": ["灰", "白", "黑", "红", "绿", "黄", "gray", "grey", "white", "black", "red"],
}


def infer_speaker_color(speaker_desc: str) -> str:
    """从主讲人外貌描述推断西装颜色系；无法判断时返回 'auto'。"""
    if not speaker_desc:
        return "auto"
    d = speaker_desc.lower()
    # 蓝色系优先（有专用阈值）
    if any(k in d for k in COLOR_KEYWORDS["blue"]):
        return "blue"
    if any(k in d for k in COLOR_KEYWORDS["other"]):
        return "other"
    return "auto"


def classify_frame_by_color(img_path: str,
                             speaker_color: str = "blue") -> tuple[str, float]:
    """
    纯颜色规则识别帧中主要人物，零 API 费用。
    仅当 speaker_color=='blue'（深蓝/藏青西装）时启用专用阈值判断；
    其他颜色系统一返回"不确定"，交给 vision LLM 裁定。
    返回 (classification, confidence)
    classification: "主讲人" | "主持人" | "双人" | "不确定"
    """
    try:
        img = Image.open(img_path).convert("RGB")
        w, h = img.size
        # 去掉字幕区域和片头片尾干扰，只看中间主体
        crop = img.crop((0, int(h * 0.08), w, int(h * 0.78)))
        cw, ch = crop.size
        pixels = crop.load()

        blue_cnt = gray_cnt = total = 0
        for y in range(ch):
            for x in range(cw):
                r, g, b = pixels[x, y]
                total += 1
                # 深蓝/藏青：蓝通道明显高于红绿，且整体偏暗
                if b > 80 and b > r + 20 and b > g + 20 and r < 130 and g < 130:
                    blue_cnt += 1
                # 中性灰：三通道接近，亮度中等
                elif abs(r - g) < 30 and abs(g - b) < 30 and abs(r - b) < 30 and 70 < r < 210:
                    gray_cnt += 1

        if total == 0:
            return "不确定", 0.0

        blue_ratio = blue_cnt / total
        gray_ratio = gray_cnt / total

        # 决策规则（基于实测数据调校）
        # 李录帧：blue_ratio 通常 0.07~0.17
        # 主持人帧：blue_ratio < 0.03，gray_ratio > 0.35
        # 双人帧：blue_ratio < 0.04，gray_ratio > 0.35
        if speaker_color == "blue":
            if blue_ratio >= 0.06:
                if gray_ratio >= 0.35:
                    return "双人", blue_ratio
                return "主讲人", blue_ratio
            elif gray_ratio >= 0.35:
                return "主持人", gray_ratio
            else:
                return "不确定", max(blue_ratio, gray_ratio)
        else:
            # 通用：无法用颜色区分时返回不确定
            return "不确定", 0.0
    except Exception:
        return "不确定", 0.0

# ── 人脸预筛 ──────────────────────────────────────────────────────────────────
# vision 调用是整条流水线最贵、最慢的一步（单条约 5 分钟）。候选帧里有相当
# 一部分是空镜、PPT、背影，既做不了封面又照样按图片计费。先用 OpenCV 的
# haar 级联在本地过一遍，只把「有正脸且脸够大」的帧送上去。

MIN_FACE_AREA_RATIO = 0.05   # 最大人脸框面积 / 帧面积，低于此视为脸太小做不了封面
FALLBACK_KEEP = 8            # 无帧达标时改按脸大小取前 N 帧，而不是把全部帧都送上去

# 封面出片门槛。挑不出合格帧时宁可不出片，也不要拿一张路人合影当封面。
EXIT_QUALITY = 2
MIN_VLM_PASS_SCORE = 6       # vision 自评封面分低于此视为不合格
MAX_VLM_REJECTIONS = 5       # 不合格候选超过这个数就判定这条片子挑不出封面
FACE_TOP_RATIO = 0.6         # 人脸中心必须落在画面上 60%，否则字幕条会压到脸
WATERMARK_MARGIN = 0.12      # 四角水印区边长占比，人脸压在这里会被水印遮住

# 候选帧池。旧版按固定 3s 间隔采样，一条 66s 的金句段扣掉首尾只剩十几个点，
# 亮度过滤再砍一刀，实测只剩 5 帧进人脸预筛 —— MAX_VLM_REJECTIONS=5 这条
# 「不合格超过 5 个才拒」的门槛根本凑不满，等于没设。改成按张数均匀取样。
DEFAULT_COVER_CANDIDATES = 24
EDGE_MARGIN_SEC = 3.0        # 片头片尾各避开 3s，那里通常是转场和黑帧

NO_COVER_HINT = (
    "该源片可能没有合格的人物封面帧（常见于解说式剪辑/空镜素材片）。"
    "可用 --cover-time-sec <秒> 手动指定，或加 --no-vlm 走纯几何兜底。"
)

_face_cascade = None
_face_cascade_loaded = False


def _get_face_cascade():
    """惰性加载 haar 级联；OpenCV 缺失或模型文件读不到时返回 None。"""
    global _face_cascade, _face_cascade_loaded
    if _face_cascade_loaded:
        return _face_cascade
    _face_cascade_loaded = True
    try:
        import cv2
        path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(path)
        if cascade.empty():
            print(f"[cover]   ⚠️ haar 级联加载失败（{path}），跳过人脸预筛", file=sys.stderr)
            return None
        _face_cascade = cascade
    except ImportError:
        print("[cover]   ⚠️ 未安装 opencv-python-headless，跳过人脸预筛", file=sys.stderr)
    return _face_cascade


def largest_face_ratio(img_path: str) -> float:
    """返回该帧最大正脸框面积占整帧的比例；无脸或检测不可用时返回 0。"""
    cascade = _get_face_cascade()
    if cascade is None:
        return 0.0
    try:
        import cv2
        img = cv2.imread(img_path)
        if img is None:
            return 0.0
        h, w = img.shape[:2]
        if not h or not w:
            return 0.0
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        if len(faces) == 0:
            return 0.0
        return max(fw * fh for _, _, fw, fh in faces) / float(w * h)
    except Exception as e:
        print(f"[cover]   ⚠️ 人脸检测异常({e})，该帧按无脸处理", file=sys.stderr)
        return 0.0


def sample_frame_times(clip_start_sec: float, clip_end_sec: float,
                       count: int = DEFAULT_COVER_CANDIDATES,
                       margin: float = EDGE_MARGIN_SEC) -> list[float]:
    """在 ``[start+margin, end-margin]`` 上均匀取 ``count`` 个时间点。

    取的是每格的中点而不是端点，这样首尾两张也离边界有半格，不会踩到
    刚好卡在 margin 上的转场。片段短到装不下两侧 margin 时按比例收窄，
    实在收不动就退化成整段中点一张。
    """
    count = max(1, int(count))
    span = clip_end_sec - clip_start_sec
    if span <= 0:
        return [clip_start_sec]

    m = margin if span > margin * 2 else span * 0.1
    lo, hi = clip_start_sec + m, clip_end_sec - m
    if hi <= lo:
        return [clip_start_sec + span / 2]

    step = (hi - lo) / count
    return [lo + step * (i + 0.5) for i in range(count)]


def frame_passes_vlm(person: str, cover_score) -> bool:
    """VLM 判定一帧能否做封面：必须是主讲人本人，且封面分不低于阈值。

    自动选帧和手动钉帧（``--cover-time-sec``）共用这一条判定，
    免得两条路径各写一套、门槛悄悄跑偏。
    """
    try:
        score = float(cover_score)
    except (TypeError, ValueError):
        return False
    return person == "主讲人" and score >= MIN_VLM_PASS_SCORE


def verify_frame_person(api_key: str, model: str, frame_path: str, speaker: str,
                        speaker_desc: str = "") -> dict | None:
    """把单独一帧送 VLM 做人物识别 + 封面分判定。

    给手动钉帧用：钉帧只该覆盖「选哪一帧」，不该顺带把人物校验也跳过 ——
    钉错时间点会静默产出「别人的脸 + 本人角标」的封面（CI run 30281699063
    就是这样把爱因斯坦的资料照当成芒格发了出去）。

    返回 ``{"person", "cover_score", "reason", "passed"}``；
    VLM 没有返回可用结果时返回 ``None``，由调用方按外部依赖失败处理
    —— 校验不了绝不等于校验通过。
    """
    results = call_vision_llm(api_key, model, [frame_path], speaker, speaker_desc)
    if not results:
        return None
    r = results[0]
    person = r.get("person", "")
    score = r.get("cover_score", 0)
    return {
        "person": person,
        "cover_score": score,
        "reason": r.get("reason", ""),
        "passed": frame_passes_vlm(person, score),
    }


def reject_cover(reason: str, **fields) -> None:
    """打印结构化拒绝原因到 stderr 并以 EXIT_QUALITY 退出。"""
    payload = {"stage": "cover", "reason": reason, **fields}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
    sys.exit(EXIT_QUALITY)


def largest_face_box(img_path: str):
    """返回 ``(占比, (x, y, w, h), (W, H))``；无脸或检测不可用时返回 ``None``。"""
    cascade = _get_face_cascade()
    if cascade is None:
        return None
    try:
        import cv2
        img = cv2.imread(img_path)
        if img is None:
            return None
        h, w = img.shape[:2]
        if not h or not w:
            return None
        gray = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        if len(faces) == 0:
            return None
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        return (fw * fh) / float(w * h), (int(fx), int(fy), int(fw), int(fh)), (w, h)
    except Exception as e:
        print(f"[cover]   ⚠️ 人脸检测异常({e})，该帧按无脸处理", file=sys.stderr)
        return None


def frame_geometry_verdict(img_path: str,
                           min_ratio: float = MIN_FACE_AREA_RATIO
                           ) -> tuple[bool, str, float]:
    """纯几何规则判定一帧能不能做封面。

    三条：脸够大、脸中心落在画面上 ``FACE_TOP_RATIO``（下方要留给标题条）、
    脸不压在四角水印区。返回 ``(是否合格, 原因, 脸占比)``。
    """
    box = largest_face_box(img_path)
    if box is None:
        return False, "no_face", 0.0
    ratio, (fx, fy, fw, fh), (w, h) = box
    if ratio < min_ratio:
        return False, f"face_too_small({ratio:.1%}<{min_ratio:.0%})", ratio

    cy = fy + fh / 2
    if cy > h * FACE_TOP_RATIO:
        return False, f"face_too_low({cy / h:.0%}>{FACE_TOP_RATIO:.0%})", ratio

    mx, my = w * WATERMARK_MARGIN, h * WATERMARK_MARGIN
    in_x = fx < mx or (fx + fw) > (w - mx)
    in_y = fy < my or (fy + fh) > (h - my)
    if in_x and in_y:
        return False, "face_in_watermark_corner", ratio
    return True, "ok", ratio


def pick_best_frame_geometric(raw_video: str, clip_start_sec: float,
                              clip_end_sec: float, tmp_dir: str,
                              candidates: int = DEFAULT_COVER_CANDIDATES,
                              report: dict | None = None,
                              crop: str | None = None) -> str:
    """不调 VLM，只按几何规则挑封面帧；一帧都不合格就退 EXIT_QUALITY。

    ``--no-vlm`` 路径。合格帧里取清晰度最高的那张。候选帧和成品封面必须用
    同一个 ``crop``，否则会出现「预筛看的是带硬字幕的画面、成品却是裁过的」。
    """
    passed: list[tuple[float, str, float]] = []   # (清晰度, 路径, 时间)
    rejections: list[dict] = []
    for idx, t in enumerate(sample_frame_times(clip_start_sec, clip_end_sec,
                                               candidates)):
        path = os.path.join(tmp_dir, f"gframe_{idx:03d}.jpg")
        if extract_frame(raw_video, t, path, crop):
            b = image_brightness(path)
            if not (40 <= b <= 230):
                rejections.append({"time_sec": round(t, 1),
                                   "reason": f"brightness({b:.0f})"})
            else:
                ok, why, ratio = frame_geometry_verdict(path)
                if ok:
                    passed.append((image_sharpness(path), path, t))
                else:
                    rejections.append({"time_sec": round(t, 1), "reason": why,
                                       "face_ratio": round(ratio, 4)})

    if report is not None:
        report["cover_vlm_passed"] = False
        report["cover_geometric_rejections"] = rejections

    if not passed:
        reject_cover("no_frame_meets_geometry",
                     detail="没有满足条件的封面帧（几何规则）",
                     candidates_evaluated=len(rejections),
                     rejections=rejections[:20], hint=NO_COVER_HINT)

    best = max(passed, key=lambda s: s[0])
    if report is not None:
        report["cover_frame_time_sec"] = round(best[2], 1)
    print(f"[cover]   几何规则命中 {len(passed)} 帧，选清晰度最高帧 "
          f"t={best[2]:.1f}s", flush=True)
    return best[1]


def filter_frames_by_face(frame_paths: list[str], frame_times: list[float],
                          min_ratio: float = MIN_FACE_AREA_RATIO
                          ) -> tuple[list[str], list[float]]:
    """筛掉没有正脸或脸太小的帧。

    一帧都不合格时返回原样 —— 宁可多花钱也不能让封面这一步空手而归，
    级联对侧脸、戴眼镜、低分辨率素材本来就会漏检。
    """
    if not frame_paths or _get_face_cascade() is None:
        return frame_paths, frame_times

    t0 = time.time()
    scored = [(largest_face_ratio(p), p, t) for p, t in zip(frame_paths, frame_times)]
    elapsed = time.time() - t0

    kept = [s for s in scored if s[0] >= min_ratio]
    if kept:
        # 达标帧太少就按脸大小补齐到 FALLBACK_KEEP。以 B-roll 为主的片源里
        # 常常 47 帧只有 1 帧过线，vision 拿到唯一候选只能矮子里拔将军（实测
        # 选出一张自评 1 分、"完全不适合"的路人合影）。预筛是为了省调用，
        # 不是为了把候选池砍到没得挑。
        if len(kept) < FALLBACK_KEEP:
            chosen = {s[1] for s in kept}
            extra = sorted((s for s in scored if s[0] > 0 and s[1] not in chosen),
                           key=lambda s: -s[0])
            kept += extra[:FALLBACK_KEEP - len(kept)]
        kept.sort(key=lambda s: s[2])   # 送给 vision 的帧必须仍按时间排序
        print(f"[cover]   人脸预筛：{len(frame_paths)} 帧 → {len(kept)} 帧"
              f"（脸占比 ≥{min_ratio:.0%}，不足 {FALLBACK_KEEP} 帧按脸大小补齐，"
              f"耗时 {elapsed:.1f}s）", flush=True)
        return [s[1] for s in kept], [s[2] for s in kept]

    # 中景机位的说话人在 854 宽的片源里脸只占 4% 左右（实测一条 60 帧的
    # 片源 44 帧检出正脸、最大占比 0.047），整条片子全卡在阈值下方一点。
    # 此时"全部送 vision"等于白付一遍预筛的 CPU 又一分钱没省，改成按脸
    # 大小取前几帧——排序信息本来就已经算出来了。
    ranked = sorted((s for s in scored if s[0] > 0), key=lambda s: -s[0])[:FALLBACK_KEEP]
    if ranked:
        ranked.sort(key=lambda s: s[2])   # 送给 vision 的帧必须仍按时间排序
        print(f"[cover]   人脸预筛：{len(frame_paths)} 帧无一满足"
              f"「脸占比 ≥{min_ratio:.0%}」（最大 {max(r for r, _, _ in scored):.1%}），"
              f"改取脸最大的 {len(ranked)} 帧送 vision（耗时 {elapsed:.1f}s）", flush=True)
        return [p for _, p, _ in ranked], [t for _, _, t in ranked]

    print(f"[cover]   人脸预筛：{len(frame_paths)} 帧未检出任何正脸，"
          f"回退为全部送 vision（耗时 {elapsed:.1f}s）", flush=True)
    return frame_paths, frame_times


def call_vision_llm(api_key: str, model: str, frame_paths: list[str],
                    speaker: str, speaker_desc: str = "") -> list[dict]:
    """
    把多帧图片发给 vision LLM，让它识别哪帧是主讲人的正面大特写。
    返回每帧的分析结果列表。
    """
    # 构建 content（多图）
    content = []
    for i, path in enumerate(frame_paths):
        b64 = image_to_b64(path)
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })
        content.append({
            "type": "text",
            "text": f"图片{i+1}（帧{i+1}）"
        })

    content.append({
        "type": "text",
        "text": (
            f"以上是访谈视频的 {len(frame_paths)} 帧截图（按时间顺序）。"
            f"访谈中有两个人：\n"
            f"- 主讲嘉宾【{speaker}】：被采访的主角，是短视频封面要展示的人"
            + (f"，外貌特征：{speaker_desc}" if speaker_desc else "") + "\n"
            + f"- 主持人/采访者：提问者\n\n"
            f"请逐帧分析，判断每帧画面中主要是哪类人，并给出该帧作为封面的适合度（1-10分）：\n"
            f"- 10分：{speaker}正面大特写，表情清晰，背景干净\n"
            f"- 7-9分：{speaker}正面或四分之三侧面，画面清晰\n"
            f"- 4-6分：双人画面，或{speaker}侧面\n"
            f"- 1-3分：主持人为主，或{speaker}不在画面中\n\n"
            f"返回 JSON 数组，格式：\n"
            f'[{{"frame": 1, "person": "主讲人|主持人|双人|其他", "cover_score": 8, "reason": "..."}},...]\n'
            f"只返回 JSON，不要其他内容。"
        )
    })

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 512,
        "temperature": 0.1,
    }).encode("utf-8")

    try:
        resp = sf_client.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=payload, timeout=60, tag="cover",
        )
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        used = (data.get("usage") or {}).get("prompt_tokens", "?")
        # 提取 JSON
        match = re.search(r"\[.*?\]", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        # 调用成功但拿不到结果 —— 必须告警，不能静默失败。
        # 这种情况图片 token 已经扣费，但没有任何产出。
        # 典型原因：模型名已下架被平台转到纯文本模型，根本看不了图。
        print(
            f"[cover] \u26a0\ufe0f vision 模型 {model} 返回无法解析的内容"
            f"\uff08已消耗 {used} input tokens\uff09。"
            f"请确认该模型支持图片输入且仍在售。",
            file=sys.stderr,
        )
        if text:
            print(f"[cover]   原始返回：{text[:200]}", file=sys.stderr)
        else:
            print("[cover]   原始返回为空（纯文本模型收到图片时的典型表现）", file=sys.stderr)
    except (sf_client.FatalHTTPError, sf_client.RetriesExhausted):
        # 鉴权/余额/连续 5xx 是外部故障，不是“这条片子挑不出封面”。
        # 咽下去会让它伪装成质量拒绝（退 2），把真实原因藏起来。
        raise
    except Exception as e:
        print(f"[cover] vision LLM 调用失败: {e}", file=sys.stderr)
    return []


def pick_best_frame_vision(raw_video: str, clip_start_sec: float, clip_end_sec: float,
                           speaker: str, api_key: str, vision_model: str,
                           tmp_dir: str,
                           candidates: int = DEFAULT_COVER_CANDIDATES,
                           speaker_desc: str = "", speaker_color: str = "auto",
                           report: dict | None = None,
                           crop: str | None = None) -> str | None:
    """
    从原始视频在金句时间段内均匀截 ``candidates`` 帧，
    用 vision LLM 识别哪帧是主讲人大特写，返回最佳帧路径。

    ``report`` 给进来时会被填上 ``cover_vlm_passed`` 和
    ``cover_vlm_rejections``，供 produce.py 写进 meta.json。
    VLM 判定不合格的候选超过 ``MAX_VLM_REJECTIONS`` 且没有任何合格帧时，
    直接退 EXIT_QUALITY —— 这条片子就是挑不出封面，不要硬凑。

    ``crop`` 会应用到所有候选帧，保证人脸预筛和 VLM 看到的就是成品封面。
    """
    frame_paths = []
    frame_times = []
    dropped = []  # 记录被亮度过滤掉的帧，便于全部被过滤时诊断
    for idx, t in enumerate(sample_frame_times(clip_start_sec, clip_end_sec,
                                               candidates)):
        path = os.path.join(tmp_dir, f"vframe_{idx:03d}.jpg")
        if extract_frame(raw_video, t, path, crop):
            b = image_brightness(path)
            if 40 <= b <= 230:  # 过滤过暗过亮帧
                frame_paths.append(path)
                frame_times.append(t)
            else:
                dropped.append(b)

    if not frame_paths:
        # 不能静默返回：否则 vision 根本没被调用，日志里却只看到
        # “vision 未返回有效帧”，会让人误以为是模型或 API 的问题。
        if dropped:
            print(
                f"[cover]   ⚠️ 共 {len(dropped)} 帧全被亮度过滤（阈值 40~230，"
                f"实测 {min(dropped):.0f}~{max(dropped):.0f}），vision 未被调用。"
                f"若素材本身偏暗，请放宽阈值。",
                flush=True,
            )
        else:
            print("[cover]   ⚠️ 未能从原视频截出任何帧，vision 未被调用。", flush=True)
        return None

    # ── 第一步：颜色规则筛选（零费用，仅蓝色系启用）──
    # speaker_color 由外部传入（auto 时已在 main 里从 speaker_desc 推断）
    # 非 blue 系（other/auto-未知）时直接跳过规则，全部交给 vision
    if speaker_color != "blue":
        print(f"[cover]   speaker_color={speaker_color}，跳过颜色规则，直接用 vision 识别", flush=True)
        speaker_frames = []
        uncertain_frames = [("不确定", 0.0, fp) for fp in frame_paths]
    else:
        rule_results = []
        for fp in frame_paths:
            cls, conf = classify_frame_by_color(fp, speaker_color)
            rule_results.append((cls, conf, fp))
        speaker_frames = [(cls, conf, fp) for cls, conf, fp in rule_results if cls == "主讲人"]
        uncertain_frames = [(cls, conf, fp) for cls, conf, fp in rule_results if cls == "不确定"]

    # 有颜色规则确认的主讲人帧，直接选清晰度最高的
    if speaker_frames:
        best = max(speaker_frames, key=lambda x: image_sharpness(x[2]))
        print(f"[cover]   颜色规则命中 {len(speaker_frames)} 帧主讲人，选清晰度最高帧 t={frame_times[frame_paths.index(best[2])]:.1f}s")
        return best[2]

    # 没有主讲人帧，有不确定帧 → 调用 vision LLM 裁决
    if not uncertain_frames:
        return None  # 全是主持人/双人，兜底由外层处理

    # 无 key 时无法调 vision，直接返回 None 让外层退到中间帧兜底
    if not api_key:
        print("[cover]   无 API key，跳过 vision 识别，交由外层兜底", flush=True)
        return None

    # 送 vision 之前先本地筛掉没有正脸的帧
    frame_paths, frame_times = filter_frames_by_face(frame_paths, frame_times)

    print(f"[cover]   共 {len(frame_paths)} 帧，发送给 vision 模型识别...", flush=True)

    # 每次最多发 6 帧（避免 token 超限）
    BATCH = 6
    best_score = -1
    best_path = None
    best_time = None
    rejections: list[dict] = []

    for b_start in range(0, len(frame_paths), BATCH):
        batch_paths = frame_paths[b_start:b_start + BATCH]
        batch_times = frame_times[b_start:b_start + BATCH]

        results = call_vision_llm(api_key, vision_model, batch_paths, speaker, speaker_desc)

        for r in results:
            frame_idx = r.get("frame", 1) - 1
            score = r.get("cover_score", 0)
            person = r.get("person", "")
            reason = r.get("reason", "")

            if frame_idx < len(batch_paths):
                t = batch_times[frame_idx]
                print(f"[cover]   帧 t={t:.1f}s: {person}, 封面分={score}, {reason}", flush=True)

                if not frame_passes_vlm(person, score):
                    rejections.append({"time_sec": round(t, 1), "person": person,
                                       "cover_score": score, "reason": reason})
                    continue

                # 额外加权：清晰度
                sharpness = image_sharpness(batch_paths[frame_idx])
                adjusted = score + sharpness / 5000  # 清晰度权重较小
                if adjusted > best_score:
                    best_score = adjusted
                    best_path = batch_paths[frame_idx]
                    best_time = t

    if report is not None:
        report["cover_vlm_passed"] = best_path is not None
        report["cover_vlm_rejections"] = rejections
        if best_time is not None:
            report["cover_frame_time_sec"] = round(best_time, 1)

    if best_path is None and len(rejections) > MAX_VLM_REJECTIONS:
        reject_cover("no_frame_passed_vlm",
                     detail="没有满足条件的封面帧（VLM 校验全部不合格）",
                     candidates_evaluated=len(frame_paths),
                     best_score=max((r["cover_score"] for r in rejections),
                                    default=0),
                     rejected=len(rejections),
                     threshold=MAX_VLM_REJECTIONS,
                     min_cover_score=MIN_VLM_PASS_SCORE,
                     rejections=rejections[:20],
                     hint=NO_COVER_HINT)

    return best_path


# 行首禁则：这些字符不能出现在行首，否则排版看着很脏
_NO_LINE_START = "，。！？、；：」』）】》,.!?;:)]}’”"


def _segment(text: str) -> list:
    """把标题切成不应该被拆开的最小单元。

    jieba 在就按词切，不在就退化成逐字（英文按空格），
    不让分词库成为硬依赖。
    """
    try:
        import jieba
        units = [u for u in jieba.lcut(text) if u]
    except ImportError:
        units, buf = [], ""
        for ch in text:
            if ord(ch) > 127:          # CJK 逐字可断
                if buf:
                    units.append(buf); buf = ""
                units.append(ch)
            elif ch == " ":
                if buf:
                    units.append(buf); buf = ""
            else:
                buf += ch              # 英文单词不拆
        if buf:
            units.append(buf)

    # 把紧跟在后的禁则标点粘回前一个单元，防止它被抛到下行行首
    merged = []
    for u in units:
        if merged and u and all(c in _NO_LINE_START for c in u):
            merged[-1] += u
        else:
            merged.append(u)
    return merged


def wrap_title(title: str, measure, max_px: float) -> list:
    """按真实宽度折行，不劈词，并尽量把各行长度拉均。

    ``measure`` 是一个 ``str -> 像素宽度`` 的回调（通常包 PIL 的
    ``draw.textlength``），这样本函数不绑定具体字体实现，好测。

    均衡的意义：贪心换行会得到“很长的一行 + 小尾巴”，既难看也更
    容易造成奇怪的断处。先用贪心算出最少行数 n，再在 n 行的前提下
    把每行目标宽度压到最小，重新排一遍。

    “最小”只能搜，不能算：词边界是离散的，总宽/n 这个理论值通常正好
    落在某个词的中间，一排就多出一行，于是均衡整个被放弃。实测标题
    “股乾爹：「耐心」不是美德，而是这门生意的入场券？”就这样烧成了
    20 字 + 4 字，而 13 + 11 明明排得下。二分几轮的代价可以忽略。
    """
    units = _segment(title)
    if not units:
        return [title]

    def greedy(limit: float) -> list:
        lines, cur = [], ""
        for u in units:
            trial = cur + u
            if cur and measure(trial) > limit:
                lines.append(cur)
                cur = u
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines

    lines = greedy(max_px)
    n = len(lines)
    if n <= 1:
        return lines

    # 在 [总宽/n, max_px] 里二分出仍能排成 n 行的最小行宽
    lo, hi = measure(title) / n, max_px
    best = lines
    for _ in range(24):
        mid = (lo + hi) / 2
        cand = greedy(mid)
        if len(cand) <= n:
            best, hi = cand, mid
        else:
            lo = mid
    return best


# ── 出图几何 ─────────────────────────────────────────────────────────────────
# 旧版直接 `img.resize(target_size)` 是非等比硬拉伸。源帧被 --cover-crop 切过
# 之后宽高比常常远离目标：实测切出来是 854x340（2.51），而目标 1280x720 是
# 1.778、1080x1920 是 0.5625。竖版封面因此把人脸拽成极细长，人物明显变形。
#
# 改成两条分支，都严格保持几何比例：
#   cover —— 等比放大后居中裁切，画面填满、无黑边，但会切掉边缘；
#   fit   —— 等比缩放到完整装进目标，居中贴在「同帧放大 + 高斯模糊」的背景上。
#
# 阈值 COVER_MIN_RETAIN_RATIO：按比例填满目标后，源图在两个方向上还能保留多少。
# 定在 85%（即任一方向要丢掉超过 15% 画面就不裁）是因为：这份素材上人脸检测
# 并不可靠——讲话人多是侧脸，haar 级联检不出来；画面里的铜像等静物又会误检，
# 所以 _face_focus_x 经常拿不到可信的主体位置、只能退回居中。裁切幅度一大，
# 居中窗口就会把偏离画面中心的讲话人切掉：实测 854x340 → 1280x720 要丢掉 29%
# 宽度，居中窗口 (125,0,729,340) 正好把偏右的人脸切掉一半。
# 因此只在「基本只是修边」时才 cover；定不准主体又要大幅裁切时，宁可留虚化边
# 框（等比装入 + 背景虚化），也绝不冒险切掉主体。
# 实测：854x340 → 1280x720 只保留 71% 宽，走 fit（上下各约 105px 虚化条）；
#       1920x1080 → 1280x720 两个方向都保留 100%，走 cover、不出现虚化边。
COVER_MIN_RETAIN_RATIO = 0.85

# 背景虚化半径相对目标短边的比例。太小盖不住背景细节、看着像重影，
# 太大则整块糊成色块、失去与前景的呼应。
FIT_BACKGROUND_BLUR_RATIO = 0.04

# 竖版 fit 分支里人脸带横向仍要保留的源宽比例。
#
# 854x340 的源帧按 fit 装进 1080x1920，人脸带只有 430px 高、占画面 22.4%，
# 在抖音信息流里就是「一大片虚化 + 中间一条细人脸」。要让主体变大只有等比
# 放大一条路，而等比放大必然横向溢出、要裁掉一些源宽 —— 这个数就是裁多少。
#
# 2/3 表示放大 1.5 倍、两侧各裁掉 16.7%：实测 partner.mp4 第 1200s 那帧
# （crop=854:340:0:70，芒格的头在源图 x≈470~690）居中窗口是 [142, 712]，
# 头完整保留；再往上加到 1.75 倍窗口收到 [183, 671]，右半张脸就被切掉了。
# 人脸带因此从 22.4% 提到 33.6%。
#
# 窗口一律居中，不走 _face_focus_x：haar 在这份素材上不可信，同一帧检出的
# 最大框是 (198,9,164,164)、占比 9.3%（高于 MIN_FACE_AREA_RATIO 的 5%，
# 门槛拦不住），实际是背景写字楼窗格的误检。照它对齐会把窗口拽成 [0, 570]，
# 芒格被切掉一半 —— 放大幅度越大，认错主体的代价越大，所以这里只居中。
PORTRAIT_ZOOM_KEEP_WIDTH = 2 / 3


# ── 平台安全区 ───────────────────────────────────────────────────────────────
# 出图尺寸不等于平台真正显示的尺寸，两边都会再裁一刀：
#
#   抖音信息流对 1080x1920 的竖图只显示中间 1080x1464，上下各切掉 228px。
#   B站官方推荐 16:10，1280x720 要变成 1146x717 得左右各切掉 64px。
#
# 旧排版把角标钉在 y=20、标题起笔在 x=40，两个都落在被切掉的那一圈里：竖版
# 在抖音上只剩虚化背景加中间一条人脸，横版第一个字会被啃掉。所以文字与角标
# 的位置一律从下面这个安全区推出来，不再从画面边缘推。
#
# 比例而不是绝对像素，是为了让 --size 传别的分辨率时同样成立。
PORTRAIT_SAFE_INSET_RATIO = 240 / 1920    # 上下各收 240px（比抖音实切的 228 再紧 12px）
LANDSCAPE_SAFE_INSET_RATIO = 80 / 1280    # 左右各收 80px（比 16:10 实切的 64 再紧 16px）

# B站信息流会在封面下沿压自己的东西：左下角一枚分区标签，右下角时长。标题正好
# 排在左下角，等于每张横版封面的第一行都被标签盖掉一块。把下沿这条带子整个让
# 出来，标题上提到它之上 —— 顺带也躲开了右下角的时长。
LANDSCAPE_CHROME_BAND_RATIO = 0.17        # 720 上是 122px

# 排版内边距，都相对安全区（不是画面边缘）算。
TITLE_SIDE_MARGIN = 40
TITLE_BOTTOM_MARGIN = 50
TAG_MARGIN_X = 24
TAG_MARGIN_Y = 20
TITLE_SHADOW = 2                          # 标题描边偏移，量包围盒时要算进去


def platform_safe_area(target_size: tuple) -> tuple:
    """该出图尺寸下「平台一定看得见」的矩形 ``(l, t, r, b)``。"""
    tw, th = target_size
    if th > tw:
        inset = round(th * PORTRAIT_SAFE_INSET_RATIO)
        return (0, inset, tw, th - inset)
    inset = round(tw * LANDSCAPE_SAFE_INSET_RATIO)
    return (inset, 0, tw - inset, th)


def platform_chrome_zone(target_size: tuple) -> tuple | None:
    """平台自己要占用、我们不能往里放东西的矩形；竖版没有这种带子时返回 None。"""
    tw, th = target_size
    if th > tw:
        return None
    return (0, th - round(th * LANDSCAPE_CHROME_BAND_RATIO), tw, th)


class CoverSafeAreaError(RuntimeError):
    """出图后测出有元素越出安全区。带 ``violations`` 明细，供调用方退 2。"""

    def __init__(self, target_size: tuple, violations: list):
        self.target_size = tuple(target_size)
        self.violations = violations
        super().__init__("；".join(
            f"{v['element']} 包围盒 {v['box']} 越出{v['limit_name']} {v['limit']}"
            for v in violations))


def safe_area_violations(boxes: dict, target_size: tuple) -> list:
    """逐个元素比对安全区与平台占位带，返回越界明细（全在界内时是空表）。

    ``boxes`` 是 ``{元素名: (l, t, r, b)}``，坐标就是实际画上去的像素位置。
    """
    safe = platform_safe_area(target_size)
    chrome = platform_chrome_zone(target_size)
    out = []
    for name, box in boxes.items():
        l, t, r, b = (round(v) for v in box)
        if l < safe[0] or t < safe[1] or r > safe[2] or b > safe[3]:
            out.append({"element": name, "box": (l, t, r, b),
                        "limit_name": "安全区", "limit": safe})
        elif chrome and r > chrome[0] and b > chrome[1] \
                and l < chrome[2] and t < chrome[3]:
            out.append({"element": name, "box": (l, t, r, b),
                        "limit_name": "平台占位带", "limit": chrome})
    return out


def assert_cover_in_safe_area(boxes: dict, target_size: tuple) -> None:
    """出图自检闸门：任何文字或角标越界就抛 ``CoverSafeAreaError``。

    这一步宁可失败也不能放行 —— 一张标题被平台切掉的封面发出去是不可撤回的，
    而重跑一次的成本只是几秒钟出图（这步不花 API 钱）。
    """
    violations = safe_area_violations(boxes, target_size)
    if violations:
        raise CoverSafeAreaError(target_size, violations)


def cover_crop_box(src_size: tuple, target_size: tuple,
                   focus_x: float | None = None) -> tuple:
    """源图坐标系里「与目标同比例的最大矩形」裁切窗口 ``(l, t, r, b)``。

    ``focus_x`` 是希望水平对齐的点（一般是人脸中心）；给 None 时取图像正中。
    窗口一定夹在图像边界内。
    """
    sw, sh = src_size
    tw, th = target_size
    if sw * th > sh * tw:          # 源图比目标更宽 → 高度用满，横向裁
        cw, ch = max(1, round(sh * tw / th)), sh
    else:                          # 源图比目标更高 → 宽度用满，纵向裁
        cw, ch = sw, max(1, round(sw * th / tw))
    cw, ch = min(cw, sw), min(ch, sh)

    cx = sw / 2 if focus_x is None else float(focus_x)
    left = int(round(cx - cw / 2))
    left = max(0, min(left, sw - cw))
    top = max(0, (sh - ch) // 2)
    return (left, top, left + cw, top + ch)


def choose_cover_strategy(src_size: tuple, target_size: tuple) -> str:
    """返回该源图应走的出图策略：``"cover"`` 或 ``"fit"``。

    抽成纯函数是为了能单独断言分支判定，不必真去生成图片。
    """
    sw, sh = src_size
    if sw <= 0 or sh <= 0:
        return "fit"
    left, top, right, bottom = cover_crop_box(src_size, target_size)
    if (right - left) / sw >= COVER_MIN_RETAIN_RATIO and \
       (bottom - top) / sh >= COVER_MIN_RETAIN_RATIO:
        return "cover"
    return "fit"


def _face_focus_x(frame_path: str, src_size: tuple) -> float | None:
    """人脸中心的横坐标（源图坐标系）；没有可采信的人脸时返回 None。

    复用候选帧预筛那套 haar 级联检测，不额外引入依赖。

    只有脸大到过 ``MIN_FACE_AREA_RATIO``（即预筛用的「脸占比 ≥5%」）才采信：
    haar 级联在背景上误检小方块是常事。实测 partner.mp4 第 1200s 那帧
    （crop=854:340:0:70，芒格在偏右、左边一尊青铜半身像）检出的最大框是
    43x43、占画面仅 0.64%，那是铜像上的斑块；照它对齐会把裁切窗口拽到最左，
    芒格被推到成品 86% 的位置、脑袋侧边切掉，而居中窗口能让他落在 65%。
    讲话人的脸本来就该是大的（该帧真脸约 220px 宽、占比 14%），
    这个门槛既有现成的语义又不用再发明一个数。
    """
    box = largest_face_box(frame_path)
    if box is None:
        return None
    ratio, (fx, fy, fw, fh), (dw, dh) = box
    if ratio < MIN_FACE_AREA_RATIO:
        return None
    if dw <= 0 or dh <= 0:
        return None
    # 检测走的是磁盘上的原图，若调用方已对图像做过变换则按比例换算回来
    return (fx + fw / 2) * (src_size[0] / float(dw))


def fit_zoom(target_size: tuple) -> float:
    """fit 分支要额外放大多少倍。竖版放大人脸带，横版保持原样。

    横版不放大是因为它本来就没有「主体太小」的问题：854x340 装进 1280x720
    的人脸带已经有 510px 高、占了 71%。
    """
    tw, th = target_size
    return 1 / PORTRAIT_ZOOM_KEEP_WIDTH if th > tw else 1.0


def fit_scale(src_size: tuple, target_size: tuple) -> float:
    """fit 分支实际用的缩放倍数（两个方向同一个数，所以不可能形变）。

    上限钉在「铺满目标」：再大就只是把已经填满的画面继续往外裁，没有意义。
    """
    sw, sh = src_size
    tw, th = target_size
    base = min(tw / sw, th / sh)
    return min(base * fit_zoom(target_size), max(tw / sw, th / sh))


def fit_to_target(img, target_size: tuple):
    """等比缩放后居中贴在同帧放大虚化的背景上。

    横版按「完整装进目标」缩放，一个像素都不裁；竖版额外放大 ``fit_zoom``
    倍再居中裁掉横向溢出，让人脸带占满更多画面。两个方向共用同一个缩放倍数，
    所以无论走哪条路几何比例都不变。
    """
    tw, th = target_size
    bg = img.crop(cover_crop_box(img.size, target_size)).resize(
        target_size, Image.LANCZOS)
    radius = max(1, int(min(tw, th) * FIT_BACKGROUND_BLUR_RATIO))
    bg = bg.filter(ImageFilter.GaussianBlur(radius))

    sw, sh = img.size
    scale = fit_scale(img.size, target_size)
    fg = img.resize((max(1, round(sw * scale)), max(1, round(sh * scale))),
                    Image.LANCZOS)
    fw, fh = fg.size
    if fw > tw or fh > th:
        left, top = max(0, (fw - tw) // 2), max(0, (fh - th) // 2)
        fg = fg.crop((left, top, left + min(fw, tw), top + min(fh, th)))
        fw, fh = fg.size
    bg.paste(fg, ((tw - fw) // 2, (th - fh) // 2))
    return bg


def render_geometry(frame_path: str, target_size: tuple):
    """把源帧变成恰好 ``target_size`` 的底图，全程不改变几何比例。"""
    img = Image.open(frame_path).convert("RGB")
    if choose_cover_strategy(img.size, target_size) == "cover":
        box = cover_crop_box(img.size, target_size,
                             _face_focus_x(frame_path, img.size))
        return img.crop(box).resize(target_size, Image.LANCZOS)
    return fit_to_target(img, target_size)


def make_cover(frame_path: str, title: str, speaker: str,
               output_path: str, target_size: tuple = (1280, 720)) -> dict:
    """烧标题出图，返回 ``{元素名: 包围盒}``；有元素越界则抛 ``CoverSafeAreaError``。"""
    img = render_geometry(frame_path, target_size)
    w, h = img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    grad_height = int(h * 0.55)  # noqa: E501  (以下渐变遮罩逻辑不变)
    for i in range(grad_height):
        alpha = int(200 * (i / grad_height))
        y = h - grad_height + i
        draw_overlay.rectangle([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # 标题折行。
    #
    # 旧版是“逐字累加 + 固定 14 字估宽”，两个毛病：
    #   1. 不看真实字体度量，宽度估不准；
    #   2. 贪心填满第一行，会把词从中间劈开。实测出现过
    #      “大选后全面买入的反常 / 识逻辑！”——把“反常识”劈成两截。
    #
    # 现在：真实字体宽度 + jieba 词边界 + 行数均衡 + 行首禁则。
    #
    # 左右边界和落底位置都从 platform_safe_area 推，不再从画面边缘推 ——
    # 边缘那一圈会被平台裁掉。
    safe_l, safe_t, safe_r, safe_b = platform_safe_area(target_size)
    chrome = platform_chrome_zone(target_size)
    title_bottom = (chrome[1] if chrome else safe_b) - TITLE_BOTTOM_MARGIN

    title_font_size = max(36, w // 22)
    title_font = find_font(title_font_size)
    max_line_px = (safe_r - safe_l) - 2 * (TITLE_SIDE_MARGIN + TITLE_SHADOW)

    def _measure(s: str) -> float:
        return draw.textlength(s, font=title_font)

    title_lines = wrap_title(title, _measure, max_line_px)

    # 行数太多就缩字号重排，宁可小一点也不要堆成四行遮住画面
    while len(title_lines) > 3 and title_font_size > 28:
        title_font_size -= 4
        title_font = find_font(title_font_size)
        title_lines = wrap_title(title, _measure, max_line_px)

    # 先按 (0, 0) 起笔量一遍真实墨水框，再整体平移到「左边贴安全区内边距、
    # 底边贴 title_bottom」。用字号估高度会差出十几像素，正好卡在闸门上。
    line_height = title_font_size + 10
    ink = [draw.textbbox((0, i * line_height), line, font=title_font)
           for i, line in enumerate(title_lines)]
    dx = safe_l + TITLE_SIDE_MARGIN - min(b[0] for b in ink)
    dy = title_bottom - max(b[3] for b in ink)

    boxes: dict = {}
    for i, (line, box) in enumerate(zip(title_lines, ink)):
        tx, ty = dx, dy + i * line_height
        for ox, oy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
            draw.text((tx + ox * TITLE_SHADOW, ty + oy * TITLE_SHADOW), line,
                      font=title_font, fill=(0, 0, 0, 200))
        draw.text((tx, ty), line, font=title_font, fill=(255, 255, 255))
        boxes[f"标题第{i + 1}行"] = (box[0] + dx - TITLE_SHADOW,
                                  box[1] + dy - TITLE_SHADOW,
                                  box[2] + dx + TITLE_SHADOW,
                                  box[3] + dy + TITLE_SHADOW)

    tag_font_size = max(22, w // 40)
    tag_font = find_font(tag_font_size)
    tag_text = f" {speaker} "
    tag_l, tag_t = safe_l + TAG_MARGIN_X, safe_t + TAG_MARGIN_Y
    tag_r = tag_l + draw.textlength(tag_text, font=tag_font) + 16
    tag_b = tag_t + tag_font_size + 16
    # 画到 r-1/b-1：draw.rectangle 两端都含，减一才和 textbbox 的左闭右开一致，
    # 报出去的包围盒才是同一套约定
    draw.rectangle([tag_l, tag_t, tag_r - 1, tag_b - 1], fill=(180, 0, 0))
    draw.text((tag_l + 8, tag_t + 6), tag_text.strip(), font=tag_font,
              fill=(255, 255, 255))
    boxes["角标"] = (tag_l, tag_t, tag_r, tag_b)

    # 闸门在落盘之前：越界就一个字节都不写，不给「先出图再说」留后门。
    assert_cover_in_safe_area(boxes, target_size)
    img.save(output_path, "JPEG", quality=90)
    return boxes


def main():
    parser = argparse.ArgumentParser(description="Step 7: 封面生成（vision 识别主讲人）")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--clips-dir", required=True)
    parser.add_argument("--raw-video", required=True, help="原始完整视频路径")
    parser.add_argument("--speaker", default="演讲者")
    parser.add_argument("--size", default="1280x720")
    # 注意：旧默认值 Qwen/Qwen2.5-VL-72B-Instruct 已从硅基流动下架。
    # 平台会静默把请求转到 Qwen/Qwen3.5-9B（纯文本模型），
    # 结果是：图片照样计入 input token 扣费，但模型根本看不到图，
    # 返回空内容→ 退回兜底截帧。实测这一项占了总花费的 97%，产出为零。
    parser.add_argument("--vision-model", default="Qwen/Qwen3-VL-8B-Instruct",
                        help="Vision 模型（必须支持图片输入，建议 Qwen3-VL 系列）")
    parser.add_argument("--cover-candidates", type=int,
                        default=DEFAULT_COVER_CANDIDATES,
                        help=f"候选帧数量，在金句时长上均匀取样"
                             f"（默认 {DEFAULT_COVER_CANDIDATES}）")
    parser.add_argument("--speaker-desc", default="",
                        help="主讲人外貌描述，如'穿黑色西装的中年男性'，可选，提高识别准确度")
    parser.add_argument("--speaker-color", default="auto",
                        choices=["auto", "blue", "other"],
                        help="主讲人西装颜色系：blue=启用零费用颜色规则；other=直接走vision；auto=从--speaker-desc推断")
    parser.add_argument("--no-vlm", action="store_true",
                        help="跳过 VLM 校验，只按几何规则选帧（脸≥5%%、位于画面上60%%、避开水印区）")
    args = parser.parse_args()

    api_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()

    clips_dir = Path(args.clips_dir)
    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    w, h = map(int, args.size.split("x"))
    tmp_dir = clips_dir / "_tmp_cover"
    tmp_dir.mkdir(exist_ok=True)

    # 确定主讲人西装颜色系：显式 --speaker-color 优先，否则从 --speaker-desc 推断
    speaker_color = args.speaker_color
    if speaker_color == "auto":
        speaker_color = infer_speaker_color(args.speaker_desc)
    print(f"[cover] 主讲人={args.speaker} 颜色系={speaker_color}"
          + ("（启用零费用颜色规则）" if speaker_color == "blue" else "（直接用 vision 识别）"), flush=True)

    # 非 blue 系需要 vision LLM，此时才强制要求 key；blue 规则无需 key（无法命中时退到中间帧兜底）
    if args.no_vlm:
        print("[cover] --no-vlm：跳过 VLM 校验，只按几何规则选帧", flush=True)
    elif speaker_color != "blue" and not api_key:
        print("[cover] 缺少 SILICONFLOW_API_KEY（非 blue 颜色系需要 vision 识别）", file=sys.stderr)
        sys.exit(1)

    for item in manifest:
        rank = item["rank"]
        title = item.get("title", f"clip_{rank}")
        clip_start_sec = item.get("clip_start_sec", item.get("start_sec", 0))
        clip_end_sec = item.get("clip_end_sec", item.get("end_sec", clip_start_sec + 60))

        mp4_candidates = list(clips_dir.glob(f"{rank:02d}_*.mp4"))
        if not mp4_candidates:
            print(f"[cover] [{rank:02d}] 找不到 mp4，跳过", flush=True)
            continue
        cover_path = str(clips_dir / f"{rank:02d}_cover.jpg")

        print(f"[cover] [{rank:02d}] 在原始视频 {clip_start_sec:.0f}s~{clip_end_sec:.0f}s 段截帧识别...", flush=True)

        if args.no_vlm:
            best_frame = pick_best_frame_geometric(
                raw_video=args.raw_video,
                clip_start_sec=clip_start_sec,
                clip_end_sec=clip_end_sec,
                tmp_dir=str(tmp_dir),
                candidates=args.cover_candidates,
            )
        else:
            best_frame = pick_best_frame_vision(
                raw_video=args.raw_video,
                clip_start_sec=clip_start_sec,
                clip_end_sec=clip_end_sec,
                speaker=args.speaker,
                api_key=api_key,
                vision_model=args.vision_model,
                tmp_dir=str(tmp_dir),
                candidates=args.cover_candidates,
                speaker_desc=args.speaker_desc,
                speaker_color=speaker_color,
            )

        if not best_frame:
            # 兜底：取片段中间帧
            print(f"[cover] [{rank:02d}] vision 未返回有效帧，用兜底截帧", flush=True)
            mid = (clip_start_sec + clip_end_sec) / 2
            fallback_path = str(tmp_dir / f"fallback_{rank:02d}.jpg")
            extract_frame(args.raw_video, mid, fallback_path)
            best_frame = fallback_path

        try:
            make_cover(best_frame, title, args.speaker, cover_path, (w, h))
        except CoverSafeAreaError as e:
            reject_cover("cover_text_outside_safe_area",
                         detail="封面文字/角标越出平台安全区，拒绝出图",
                         target_size=[w, h], violations=e.violations)
        print(f"[cover] ✅ [{rank:02d}] → {rank:02d}_cover.jpg", flush=True)

    import shutil
    shutil.rmtree(str(tmp_dir), ignore_errors=True)
    print("[cover] 全部完成", flush=True)


if __name__ == "__main__":
    main()
