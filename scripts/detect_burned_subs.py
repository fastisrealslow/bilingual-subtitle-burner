#!/usr/bin/env python3
"""探测原片是否已有"烧死"的硬字幕，以及它的位置和语种。

为什么需要：
    搬运素材来源五花八门。有的干净无字幕，有的已经带了英文硬字幕，
    有的中英双语都齐。如果一律再烧一层双语字幕，轻则和原字幕叠在一起
    糊成一片，重则完全遮挡。所以烧字幕之前必须先看清楚原片是什么情况。

思路（两段式，先零成本后小成本）：
    1) 纯本地图像分析定位字幕条带（不花钱）
       对若干采样帧计算"每一行的水平梯度能量"。文字笔画会产生密集的
       水平梯度，所以文字行的能量显著高于背景。
       但仅有能量还不够——台标、水印、背景板花纹同样有高能量。
       关键判据是**时间维度上的变化量**：字幕内容随时间不断更换，
       该行能量的帧间标准差很大；而台标水印基本不动，标准差很小。
       因此取「能量高 且 帧间变化大」的行，聚成条带，即为字幕区。

    2) 只把裁出来的条带图发给视觉模型判语种（花费极小）
       整帧发过去既贵又容易被画面主体干扰，裁成细长条带后
       token 数很少，模型也更专注。

输出 JSON，供流水线决定烧字幕的策略与纵向位置。

用法：
    python3 scripts/detect_burned_subs.py --video in.mp4 --out subs_probe.json
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

import numpy as np
from PIL import Image

API_BASE = (os.environ.get("SILICONFLOW_BASE_URL") or "").strip() or "https://api.siliconflow.cn/v1"
DEFAULT_VISION = os.environ.get("SILICONFLOW_VISION_MODEL") or "Qwen/Qwen3-VL-8B-Instruct"


# ──────────────────────────────────────────────────────────────────────
# 采样与图像分析
# ──────────────────────────────────────────────────────────────────────
def probe_duration(video: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", video],
        capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def extract_frames(video: str, tmp: str, n: int = 16) -> list[str]:
    """在片头片尾各留 8% 余量，均匀采 n 帧。"""
    dur = probe_duration(video)
    if dur <= 0:
        return []
    start, end = dur * 0.08, dur * 0.92
    paths = []
    for i in range(n):
        t = start + (end - start) * i / max(1, n - 1)
        p = os.path.join(tmp, f"probe_{i:03d}.jpg")
        r = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", video,
             "-frames:v", "1", "-q:v", "3", p, "-y"],
            capture_output=True,
        )
        if r.returncode == 0 and os.path.exists(p):
            paths.append(p)
    return paths


def row_energy(path: str) -> np.ndarray:
    """返回每一行的水平梯度能量（长度=图像高度）。

    文字由密集笔画构成，沿水平方向扫过去会频繁明暗跳变，
    因此文字所在行的 |dI/dx| 之和明显高于平坦背景。
    """
    im = Image.open(path).convert("L")
    a = np.asarray(im, dtype=np.float32)
    return np.abs(np.diff(a, axis=1)).sum(axis=1)


def find_subtitle_band(frames: list[str]) -> dict:
    """定位字幕条带。返回 {found, y0, y1, height, width, score}。"""
    profiles = []
    size = None
    for f in frames:
        try:
            p = row_energy(f)
            if size is None:
                with Image.open(f) as im:
                    size = im.size  # (w, h)
            profiles.append(p)
        except Exception:
            pass
    if len(profiles) < 4 or size is None:
        return {"found": False, "reason": "采样帧不足"}

    L = min(len(p) for p in profiles)
    M = np.stack([p[:L] for p in profiles])  # (帧数, 行数)
    w, h = size

    mean_e = M.mean(axis=0)
    std_e = M.std(axis=0)          # 帧间变化量 —— 区分字幕与台标的关键
    # 归一化到 0~1，避免不同分辨率/码率下阈值失效
    def norm(x):
        rng = x.max() - x.min()
        return (x - x.min()) / rng if rng > 1e-6 else np.zeros_like(x)

    mn, sn = norm(mean_e), norm(std_e)
    # 同时要求「有文字」和「在变化」。相乘可天然抑制只满足其一的行。
    score = mn * sn

    # 字幕几乎总在画面底部。实测若放宽到下半部，
    # 说话人的脸部/嘴部会因为频繁动作而同时满足“能量高+变化大”，
    # 造成误报（已在干净素材上复现）。因此只看底部 40%。
    lo = int(L * 0.60)
    region = score[lo:]
    if region.size == 0:
        return {"found": False, "reason": "画面过小"}

    thr = max(0.12, float(region.mean() + region.std()))
    hit = np.where(region >= thr)[0]
    if hit.size < max(4, int(h * 0.012)):
        return {"found": False, "reason": "未发现随时间变化的文字行", "width": w, "height": h}

    # 取最长的连续行段作为字幕条带
    groups, cur = [], [hit[0]]
    for r in hit[1:]:
        if r - cur[-1] <= max(2, int(h * 0.01)):
            cur.append(r)
        else:
            groups.append(cur)
            cur = [r]
    groups.append(cur)
    best = max(groups, key=len)

    y0, y1 = lo + best[0], lo + best[-1]
    pad = max(2, int(h * 0.008))
    y0, y1 = max(0, y0 - pad), min(h - 1, y1 + pad)

    # 再把一道：真字幕应该贴近底部，且不应占据过高的竖向篇幅
    if (y0 / h) < 0.55:
        return {"found": False, "reason": "候选区域位置偏高，不像字幕",
                "width": w, "height": h}
    if (y1 - y0) > h * 0.30:
        return {"found": False, "reason": "候选区域过宽，不像字幕条",
                "width": w, "height": h}

    return {
        "found": True,
        "y0": int(y0), "y1": int(y1),
        "band_height": int(y1 - y0),
        "width": int(w), "height": int(h),
        "top_ratio": round(y0 / h, 4),
        "score": round(float(score[y0:y1 + 1].mean()), 4),
    }


# 视觉接口对图片尺寸有下限：实测 854x22 的细条直接返回 HTTP 400，
# 放大到 2562x66 后能准确读出文字。所以裁完必须拿到足够尺寸再发。
MIN_CROP_H = 96


def crop_band(frame: str, band: dict, out: str) -> bool:
    try:
        with Image.open(frame) as im:
            h = im.height
            # 上下各留一点余量，既避免切掉字符上下缘，也给模型一点上下文
            pad = max(4, int(h * 0.02))
            y0 = max(0, band["y0"] - pad)
            y1 = min(h, band["y1"] + 1 + pad)
            crop = im.crop((0, y0, im.width, y1))
            # 太矮就等比例放大，直到高度达标
            if crop.height < MIN_CROP_H:
                scale = min(4.0, MIN_CROP_H / max(1, crop.height))
                crop = crop.resize(
                    (int(crop.width * scale), int(crop.height * scale)),
                    Image.LANCZOS,
                )
            crop.save(out, quality=92)
        return True
    except Exception as e:
        print(f"[probe] 裁切失败: {e}", file=sys.stderr)
        return False


# ──────────────────────────────────────────────────────────────────────
# 视觉模型判语种
# ──────────────────────────────────────────────────────────────────────
def ask_vision(api_key: str, model: str, crops: list[str]) -> dict:
    content = [{
        "type": "text",
        "text": (
            "这几张细长图片裁自同一个视频的同一位置（疑似字幕区），按时间先后排列。\n"
            "请判断：\n"
            "1. 它们是否是字幕文字（而不是台标、水印、背景板花纹、界面元素）\n"
            "2. 出现了哪些语言\n"
            "3. 文字是否清晰易读\n"
            '只返回 JSON：{"is_subtitle": true/false, "languages": ["中文"或"英文"...], '
            '"legible": true/false, "sample_text": "看到的一句原文"}'
        ),
    }]
    for c in crops:
        with open(c, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + b64}})

    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 400,
        "temperature": 0.1,
    }).encode()

    try:
        import requests
        r = requests.post(f"{API_BASE}/chat/completions",
                          headers={"Authorization": f"Bearer {api_key}",
                                   "Content-Type": "application/json"},
                          data=payload, timeout=120)
        if r.status_code != 200:
            # 400 最常见的原因是图片尺寸不达下限，写清楚便于定位
            print(f"[probe] ⚠️ 视觉接口 HTTP {r.status_code}：{r.text[:200]}",
                  file=sys.stderr)
            return {}
        data = r.json()
    except ImportError:
        req = urllib.request.Request(
            f"{API_BASE}/chat/completions", data=payload,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())

    txt = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    used = (data.get("usage") or {}).get("total_tokens", "?")
    m = re.search(r"\{.*\}", txt, re.DOTALL)
    if not m:
        # 同样不能静默失败：拿不到结果时要能看出是模型问题还是图片问题
        print(f"[probe] ⚠️ 视觉模型 {model} 未返回可解析 JSON（消耗 {used} tokens）",
              file=sys.stderr)
        print(f"[probe]   原始返回：{txt[:200] or '(空)'}", file=sys.stderr)
        return {}
    try:
        out = json.loads(m.group())
        out["_tokens"] = used
        return out
    except json.JSONDecodeError:
        return {}


# ──────────────────────────────────────────────────────────────────────
# 决策
# ──────────────────────────────────────────────────────────────────────
def decide(band: dict, vis: dict) -> dict:
    """把探测结果翻译成流水线能执行的动作。

    目标平台是 B 站/抖音，中文观众为主，所以"中文可读"是硬要求，
    英文只是锦上添花。
    """
    if not band.get("found") or not vis.get("is_subtitle"):
        return {"action": "burn_bilingual",
                "reason": "未检测到原生硬字幕，按常规烧中英双语",
                "avoid_top_ratio": None}

    langs = [str(x) for x in (vis.get("languages") or [])]
    has_zh = any(("中" in x) or ("Chinese" in x) or ("zh" in x.lower()) for x in langs)
    has_en = any(("英" in x) or ("English" in x) or ("en" == x.lower()) for x in langs)
    legible = bool(vis.get("legible", True))
    top = band.get("top_ratio")

    if has_zh and has_en and legible:
        return {"action": "skip_subtitle",
                "reason": "原片已有清晰的中英双语硬字幕，无需再烧",
                "avoid_top_ratio": top}
    if has_zh and legible:
        return {"action": "skip_subtitle",
                "reason": "原片已有清晰中文硬字幕，目标平台以中文为主，直接沿用",
                "avoid_top_ratio": top}
    if has_en:
        return {"action": "burn_zh_only",
                "reason": "原片仅有英文硬字幕，只补中文并上移避让",
                "avoid_top_ratio": top}
    return {"action": "burn_bilingual",
            "reason": "检测到字幕但语种不明，保守起见烧中英双语并避让",
            "avoid_top_ratio": top}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--frames", type=int, default=16)
    ap.add_argument("--vision-model", default=DEFAULT_VISION)
    ap.add_argument("--no-vision", action="store_true",
                    help="只做本地图像分析，不调用视觉模型（完全零成本）")
    args = ap.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        frames = extract_frames(args.video, tmp, args.frames)
        print(f"[probe] 采样 {len(frames)} 帧", flush=True)
        band = find_subtitle_band(frames)

        if band.get("found"):
            print(f"[probe] 疑似字幕条带 y={band['y0']}~{band['y1']} "
                  f"（画面高 {band['height']}，位于 {band['top_ratio']:.1%} 处）", flush=True)
        else:
            print(f"[probe] 未发现硬字幕：{band.get('reason','')}", flush=True)

        vis = {}
        api_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
        if band.get("found") and not args.no_vision and api_key:
            crops = []
            for i, f in enumerate(frames[::max(1, len(frames) // 3)][:3]):
                c = os.path.join(tmp, f"crop_{i}.jpg")
                if crop_band(f, band, c):
                    crops.append(c)
            if crops:
                print(f"[probe] 裁出 {len(crops)} 张条带，送视觉模型判语种...", flush=True)
                vis = ask_vision(api_key, args.vision_model, crops)
                if vis:
                    print(f"[probe] 是字幕={vis.get('is_subtitle')} "
                          f"语种={vis.get('languages')} 清晰={vis.get('legible')} "
                          f"样例=\u300c{str(vis.get('sample_text',''))[:40]}\u300d", flush=True)

        result = {"band": band, "vision": vis, "decision": decide(band, vis)}
        d = result["decision"]
        print(f"[probe] ➜ 决策：{d['action']}  （{d['reason']}）", flush=True)

        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            print(f"[probe] 已写入 {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
