#!/usr/bin/env python3
"""
clip.py — 按 manifest.json 把长片切成多条短视频，并烧录双语字幕

字幕规则（中文视频）：
  - SRT 里是中文原文，bilingual JSON 里有英文翻译
  - 英文字幕在上（较小字号），中文字幕在下（较大字号）
  - 视频分辨率 640×346，字号按此适配

字幕规则（英文视频，--srt-lang en）：
  - SRT 里是英文原文，bilingual JSON 里有中文翻译
  - 同样英文在上，中文在下
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


SRT_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})\s*\n(.*?)(?=\n\n|\Z)",
    re.DOTALL,
)


def ts2sec(h: str, ms: str) -> float:
    hh, mm, ss = h.split(":")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def sec2srt(s: float) -> str:
    s = max(0.0, s)
    h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def sec2ass(s: float) -> str:
    h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def srt_filter(srt_path: str, start_sec: float, end_sec: float, srt_lang: str = "zh") -> list:
    """
    从全片 SRT 提取时间段内字幕，时间戳相对化。
    srt_lang='zh'：SRT 里是中文，存到 zh 字段；en 字段留空待填
    srt_lang='en'：SRT 里是英文，存到 en 字段；zh 字段留空待填
    """
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()
    result = []
    new_idx = 1
    for m in SRT_RE.finditer(content):
        orig_idx, sh, sms, eh, ems, text = m.groups()
        s = ts2sec(sh, sms); e = ts2sec(eh, ems)
        if e <= start_sec or s >= end_sec:
            continue
        ns = max(0.0, s - start_sec)
        ne = min(end_sec - start_sec, e - start_sec)
        src_text = " ".join(line.strip() for line in text.strip().splitlines())
        entry = {
            "index": new_idx,               # 切片内重新编号（仅用于 ASS 顺序）
            "orig_index": int(orig_idx),    # 全片 SRT 原始编号（用于与 bilingual 对齐）
            "start_sec": ns, "end_sec": ne,
            "start": sec2srt(ns), "end": sec2srt(ne),
            "zh": src_text if srt_lang == "zh" else "",
            "en": src_text if srt_lang == "en" else "",
        }
        result.append(entry)
        new_idx += 1
    return result


def merge_translation(entries: list, bilingual_path: str, full_start_sec: float,
                       srt_lang: str = "zh") -> list:
    """
    把双语 JSON 的翻译填进字幕条目。
    srt_lang='zh'：bilingual 有 en 字段，填到 entries 的 en
    srt_lang='en'：bilingual 有 zh 字段，填到 entries 的 zh
    """
    if not bilingual_path or not os.path.exists(bilingual_path):
        return entries
    with open(bilingual_path, encoding="utf-8") as f:
        bi = json.load(f)

    fill_field = "en" if srt_lang == "zh" else "zh"

    # 主方案：按原始 SRT 编号 (orig_index ↔ bilingual.index) 精确对齐，不依赖时间戳
    idx_map = {}
    for b in bi:
        if "index" in b:
            idx_map[int(b["index"])] = b.get(fill_field, "")

    # 兜底方案：时间戳映射（仅当编号对不上时使用）
    def bi_ts2sec(ts: str) -> float:
        ts = ts.replace(",", ".")
        p = ts.split(":")
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])

    ts_map = {}
    for b in bi:
        abs_start = bi_ts2sec(b["start"])
        rel_start = abs_start - full_start_sec
        ts_map[round(rel_start, 1)] = b.get(fill_field, "")

    for e in entries:
        val = ""
        # 1) 优先用原始编号精确匹配
        oi = e.get("orig_index")
        if oi is not None and oi in idx_map:
            val = idx_map[oi]
        # 2) 编号未命中 → 时间戳精确匹配
        if not val:
            val = ts_map.get(round(e["start_sec"], 1), "")
        # 3) 再不行 → 2 秒内最近的时间戳
        if not val and ts_map:
            closest = min(ts_map.keys(), key=lambda k: abs(k - e["start_sec"]))
            if abs(closest - e["start_sec"]) < 2.0:
                val = ts_map[closest]
        e[fill_field] = val
    return entries


# ── ASS 字幕生成 ──────────────────────────────────────────────────────────────
# 视频实际分辨率 640×346，PlayRes 按实际设置，字号按比例

def wrap_text(t: str, max_chars: int, is_cjk: bool = False) -> str:
    """自动折行"""
    if not t:
        return ""
    if is_cjk:
        # 中文：按字数折行
        if len(t) <= max_chars:
            return t
        return r"\N".join(t[i:i+max_chars] for i in range(0, len(t), max_chars))
    else:
        # 英文：按词折行
        if len(t) <= max_chars:
            return t
        words = t.split()
        lines, cur = [], []
        for w in words:
            if sum(len(x) + 1 for x in cur) + len(w) > max_chars:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))
        return r"\N".join(lines)


def make_ass(entries: list, ass_path: str, video_width: int = 640, video_height: int = 346):
    """
    生成 ASS 字幕：英文在上，中文在下。
    按实际分辨率设置 PlayRes，字号按比例适配。
    """
    # 参考 1920×1080：EN=36, ZH=46 → 640×346 对应 ~12, ~15
    # 实测感觉太小，用 20 / 26
    en_size = max(14, int(video_width * 20 / 640))
    zh_size = max(18, int(video_width * 26 / 640))
    margin_bottom_zh = 12   # 中文距底边
    margin_bottom_en = 12 + zh_size * 2 + 8   # 英文在中文上方

    # 英文折行最大字符数（字体约 en_size/2 px 宽）
    en_wrap = max(20, int(video_width * 38 / 640))
    zh_wrap = max(10, int(video_width * 16 / 640))

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {video_width}",
        f"PlayResY: {video_height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Alignment=2：底部居中；MarginV 控制距底边距离
        f"Style: EN,Arial,{en_size},&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,"
        f"-1,0,0,0,100,100,0,0,1,1,0,2,10,10,{margin_bottom_en},1",
        f"Style: ZH,Microsoft YaHei,{zh_size},&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,"
        f"-1,0,0,0,100,100,0,0,1,1,0,2,10,10,{margin_bottom_zh},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for e in entries:
        s = e["start_sec"]
        end = e["end_sec"]
        en_t = wrap_text(e.get("en", "").strip(), en_wrap, is_cjk=False)
        zh_t = wrap_text(e.get("zh", "").strip(), zh_wrap, is_cjk=True)
        if en_t:
            lines.append(f"Dialogue: 0,{sec2ass(s)},{sec2ass(end)},EN,,0,0,0,,{en_t}")
        if zh_t:
            lines.append(f"Dialogue: 0,{sec2ass(s)},{sec2ass(end)},ZH,,0,0,0,,{zh_t}")

    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(cmd: list):
    print(f"[run] {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run(cmd, check=True)


def get_video_size(video: str):
    """用 ffmpeg 获取视频分辨率"""
    r = subprocess.run(
        ["ffmpeg", "-i", video],
        capture_output=True, text=True
    )
    m = re.search(r"(\d{3,5})x(\d{3,5})", r.stderr + r.stdout)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 640, 346


def main():
    parser = argparse.ArgumentParser(description="按 manifest 切片并烧录双语字幕")
    parser.add_argument("--video", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--srt", required=True, help="完整 SRT（与视频语言一致）")
    parser.add_argument("--bilingual", default=None, help="完整双语 JSON（translate.py 输出）")
    parser.add_argument("--output-dir", default="./clips")
    parser.add_argument("--srt-lang", default="zh", choices=["zh", "en"],
                        help="SRT 里的语言：zh（中文视频）或 en（英文视频）")
    parser.add_argument("--no-subtitle", action="store_true")
    parser.add_argument("--vertical", action="store_true",
                        help="输出竖屏 9:16（1080×1920，适配手机端短视频）；原视频居中，上下模糊背景填充")
    parser.add_argument("--vertical-size", default="1080x1920",
                        help="竖屏画布尺寸，默认 1080x1920")
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "_tmp"
    tmp_dir.mkdir(exist_ok=True)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    # 获取视频分辨率
    vw, vh = get_video_size(args.video)
    print(f"[clip] 视频分辨率: {vw}×{vh}", flush=True)

    # 竖屏画布尺寸（字幕按此尺寸适配）
    if args.vertical:
        cw, ch = map(int, args.vertical_size.split("x"))
        print(f"[clip] 竖屏模式，画布: {cw}×{ch}", flush=True)
    else:
        cw, ch = vw, vh
    print(f"[clip] 共 {len(manifest)} 条待切片", flush=True)

    results = []
    for item in manifest:
        rank = item["rank"]
        title = item.get("title", f"clip_{rank}")
        safe = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40]
        start = item["clip_start_sec"]
        end = item["clip_end_sec"]
        start_hms = item["clip_start"]
        end_hms = item["clip_end"]

        print(f"\n--- [{rank:02d}] {title} ({start_hms}~{end_hms}) ---", flush=True)

        clip_video = str(tmp_dir / f"{rank:02d}_raw.mp4")
        out_mp4 = str(out_dir / f"{rank:02d}_{safe}.mp4")

        # 切割片段（用秒数传给 ffmpeg，避免 SRT 的逗号时间戳格式不兼容）
        run(["ffmpeg", "-y", "-i", args.video,
             "-ss", f"{float(start):.3f}", "-to", f"{float(end):.3f}",
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", clip_video])

        # 竖屏转换滤镜：原视频等比缩放到画布宽，居中；背景用放大模糊的自身填充
        def vertical_vf(sub_filter: str = "") -> str:
            bg = f"scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},boxblur=20:5"
            fg = f"scale={cw}:-2:force_original_aspect_ratio=decrease"
            base = (f"[0:v]{bg}[bg];"
                    f"[0:v]{fg}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
            if sub_filter:
                return base + f";[v]{sub_filter}[vout]"
            return base + ";[v]null[vout]"

        if args.no_subtitle:
            if args.vertical:
                run(["ffmpeg", "-y", "-i", clip_video,
                     "-filter_complex", vertical_vf(),
                     "-map", "[vout]", "-map", "0:a?",
                     "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                     "-c:a", "aac", "-b:a", "128k", out_mp4])
            else:
                import shutil
                shutil.copy(clip_video, out_mp4)
        else:
            entries = srt_filter(args.srt, start, end, args.srt_lang)
            if args.bilingual:
                entries = merge_translation(entries, args.bilingual, start, args.srt_lang)

            ass_path = str(tmp_dir / f"{rank:02d}.ass")
            # 字幕按最终画布尺寸生成（竖屏时用 cw×ch）
            make_ass(entries, ass_path, cw, ch)
            ass_esc = ass_path.replace("\\", "/").replace(":", "\\:")

            if args.vertical:
                run(["ffmpeg", "-y", "-i", clip_video,
                     "-filter_complex", vertical_vf(f"ass={ass_esc}"),
                     "-map", "[vout]", "-map", "0:a?",
                     "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                     "-c:a", "aac", "-b:a", "128k", out_mp4])
            else:
                run(["ffmpeg", "-y", "-i", clip_video,
                     "-vf", f"ass={ass_path}",
                     "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                     "-c:a", "aac", "-b:a", "128k", out_mp4])

        size = Path(out_mp4).stat().st_size / 1024 / 1024
        print(f"✅ [{rank:02d}] → {out_mp4} ({size:.1f}MB)", flush=True)
        results.append({**item, "output_file": str(out_mp4), "size_mb": round(size, 1)})

    print(f"\n=== 全部切片完成 ===", flush=True)
    for r in results:
        print(f"  [{r['rank']:02d}] {r['title']} ({r['size_mb']}MB)", flush=True)


if __name__ == "__main__":
    main()
