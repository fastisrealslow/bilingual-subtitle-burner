#!/usr/bin/env python3
"""
step7_cover.py — 封面生成（Step 7）

策略：
  1. 从【原始视频】在金句时间段内均匀截取 N 帧（每2秒一帧）
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
import urllib.request
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
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


def extract_frame(video: str, time_sec: float, output: str) -> bool:
    r = subprocess.run(
        [_FFMPEG, "-y", "-ss", str(max(0, time_sec)), "-i", video,
         "-vframes", "1", "-q:v", "2", output],
        capture_output=True
    )
    return r.returncode == 0 and os.path.exists(output) and os.path.getsize(output) > 1000


def image_to_b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def image_sharpness(path: str) -> float:
    try:
        img = Image.open(path).convert("L").resize((160, 90))
        sharp = img.filter(ImageFilter.FIND_EDGES)
        pixels = list(sharp.getdata())
        if not pixels:
            return 0.0
        mean = sum(pixels) / len(pixels)
        return sum((p - mean) ** 2 for p in pixels) / len(pixels)
    except Exception:
        return 0.0


def image_brightness(path: str) -> float:
    try:
        img = Image.open(path).convert("L").resize((160, 90))
        pixels = list(img.getdata())
        return sum(pixels) / len(pixels) if pixels else 128.0
    except Exception:
        return 128.0



def classify_frame_by_color(img_path: str,
                             speaker_color: str = "blue") -> tuple[str, float]:
    """
    纯颜色规则识别帧中主要人物，零 API 费用。
    speaker_color: "blue"（深蓝/藏青西装）| "gray" | "other"
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

    req = urllib.request.Request(
        "https://api.siliconflow.cn/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"].strip()
            # 提取 JSON
            match = re.search(r"\[.*?\]", text, re.DOTALL)
            if match:
                return json.loads(match.group())
    except Exception as e:
        print(f"[cover] vision LLM 调用失败: {e}", file=sys.stderr)
    return []


def pick_best_frame_vision(raw_video: str, clip_start_sec: float, clip_end_sec: float,
                           speaker: str, api_key: str, vision_model: str,
                           tmp_dir: str, sample_interval: float = 3.0,
                           speaker_desc: str = "") -> str | None:
    """
    从原始视频在金句时间段内每 sample_interval 秒截一帧，
    用 vision LLM 识别哪帧是主讲人大特写，返回最佳帧路径。
    """
    duration = clip_end_sec - clip_start_sec
    # 在 20%~80% 范围内采样，避开片头片尾
    sample_start = clip_start_sec + duration * 0.1
    sample_end = clip_end_sec - duration * 0.1

    frame_paths = []
    frame_times = []
    t = sample_start
    idx = 0
    while t <= sample_end:
        path = os.path.join(tmp_dir, f"vframe_{idx:03d}.jpg")
        if extract_frame(raw_video, t, path):
            b = image_brightness(path)
            if 40 <= b <= 230:  # 过滤过暗过亮帧
                frame_paths.append(path)
                frame_times.append(t)
        t += sample_interval
        idx += 1

    if not frame_paths:
        return None

    # ── 第一步：纯颜色规则筛选（零费用）──
    speaker_color = "blue"  # 默认主讲人穿深蓝/藏青西装；可通过 speaker_desc 判断
    if speaker_desc:
        desc_lower = speaker_desc.lower()
        if any(w in desc_lower for w in ["灰", "白", "黑", "红", "绿", "黄"]):
            speaker_color = "other"  # 非蓝色系，退到 vision

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

    print(f"[cover]   共截取 {len(frame_paths)} 帧，发送给 vision 模型识别...", flush=True)

    # 每次最多发 6 帧（避免 token 超限）
    BATCH = 6
    best_score = -1
    best_path = None

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

                if score > best_score:
                    # 额外加权：清晰度
                    sharpness = image_sharpness(batch_paths[frame_idx])
                    adjusted = score + sharpness / 5000  # 清晰度权重较小
                    if adjusted > best_score:
                        best_score = adjusted
                        best_path = batch_paths[frame_idx]

    return best_path


def make_cover(frame_path: str, title: str, speaker: str,
               output_path: str, target_size: tuple = (1280, 720)):
    img = Image.open(frame_path).convert("RGB")
    img = img.resize(target_size, Image.LANCZOS)
    w, h = img.size

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    grad_height = int(h * 0.55)
    for i in range(grad_height):
        alpha = int(200 * (i / grad_height))
        y = h - grad_height + i
        draw_overlay.rectangle([(0, y), (w, y)], fill=(0, 0, 0, alpha))

    img = img.convert("RGBA")
    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # 标题折行
    max_width_chars = 14
    title_lines = []
    line = ""
    for ch in title:
        line += ch
        width_est = sum(2 if ord(c) > 127 else 1 for c in line)
        if width_est >= max_width_chars * 2:
            title_lines.append(line)
            line = ""
    if line:
        title_lines.append(line)

    title_font_size = max(36, w // 22)
    title_font = find_font(title_font_size)
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
    parser.add_argument("--vision-model", default="Qwen/Qwen2.5-VL-72B-Instruct",
                        help="Vision 模型（需支持图片输入）")
    parser.add_argument("--sample-interval", type=float, default=3.0,
                        help="截帧间隔秒数（默认3秒）")
    parser.add_argument("--speaker-desc", default="",
                        help="主讲人外貌描述，如'穿黑色西装的中年男性'，可选，提高识别准确度")
    args = parser.parse_args()

    api_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        print("[cover] 缺少 SILICONFLOW_API_KEY", file=sys.stderr)
        sys.exit(1)

    clips_dir = Path(args.clips_dir)
    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    w, h = map(int, args.size.split("x"))
    tmp_dir = clips_dir / "_tmp_cover"
    tmp_dir.mkdir(exist_ok=True)

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

        best_frame = pick_best_frame_vision(
            raw_video=args.raw_video,
            clip_start_sec=clip_start_sec,
            clip_end_sec=clip_end_sec,
            speaker=args.speaker,
            api_key=api_key,
            vision_model=args.vision_model,
            tmp_dir=str(tmp_dir),
            sample_interval=args.sample_interval,
            speaker_desc=args.speaker_desc,
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
