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

                if person != "主讲人" or score < MIN_VLM_PASS_SCORE:
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


def make_cover(frame_path: str, title: str, speaker: str,
               output_path: str, target_size: tuple = (1280, 720)):
    img = Image.open(frame_path).convert("RGB")
    img = img.resize(target_size, Image.LANCZOS)
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
    title_font_size = max(36, w // 22)
    title_font = find_font(title_font_size)
    max_line_px = w - 100          # 左右各留 50px 安全边

    def _measure(s: str) -> float:
        return draw.textlength(s, font=title_font)

    title_lines = wrap_title(title, _measure, max_line_px)

    # 行数太多就缩字号重排，宁可小一点也不要堆成四行遮住画面
    while len(title_lines) > 3 and title_font_size > 28:
        title_font_size -= 4
        title_font = find_font(title_font_size)
        title_lines = wrap_title(title, _measure, max_line_px)

    line_height = title_font_size + 10
    total_height = len(title_lines) * line_height
    title_y = h - total_height - 50

    for i, line in enumerate(title_lines):
        ty = title_y + i * line_height
        for dx, dy in [(-2, -2), (2, -2), (-2, 2), (2, 2)]:
            draw.text((40 + dx, ty + dy), line, font=title_font, fill=(0, 0, 0, 200))
        draw.text((40, ty), line, font=title_font, fill=(255, 255, 255))

    tag_font = find_font(max(22, w // 40))
    tag_text = f" {speaker} "
    tag_w = len(tag_text) * (w // 40) + 20
    tag_h = max(22, w // 40) + 16
    draw.rectangle([24, 20, 24 + tag_w, 20 + tag_h], fill=(180, 0, 0))
    draw.text((32, 26), tag_text.strip(), font=tag_font, fill=(255, 255, 255))

    img.save(output_path, "JPEG", quality=90)


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

        make_cover(best_frame, title, args.speaker, cover_path, (w, h))
        print(f"[cover] ✅ [{rank:02d}] → {rank:02d}_cover.jpg", flush=True)

    import shutil
    shutil.rmtree(str(tmp_dir), ignore_errors=True)
    print("[cover] 全部完成", flush=True)


if __name__ == "__main__":
    main()
