#!/usr/bin/env python3
"""
run_smart_segment.py — 智能语义分段全流程主控脚本

流程：
  1. yt-dlp 下载视频（支持 YouTube）
  2. faster-whisper 对整段视频 ASR 生成完整英文 SRT
  3. segment.py：LLM 语义分段，生成章节 JSON
  4. 对每个章节：
     a. ffmpeg 按时间戳切割视频片段
     b. translate.py：翻译该章节字幕
     c. 烧录双语字幕（英上中下）
     d. 输出 <index>_<title>.mp4

用法：
  python run_smart_segment.py \\
    --url "https://www.youtube.com/watch?v=xxxx" \\
    --output-dir ./output \\
    [--target-duration 600] \\
    [--model base] \\
    [--cookies cookies.txt]

环境变量：
  SILICONFLOW_API_KEY   （必填）
  SILICONFLOW_MODEL     （可选）
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).parent / "scripts"


def run(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    print(f"[run] {' '.join(str(c) for c in cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def sanitize_filename(name: str) -> str:
    """将章节标题处理成合法文件名"""
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = name.strip()[:40]
    return name


def srt_filter_by_time(srt_path: str, start_sec: float, end_sec: float, out_path: str):
    """从完整 SRT 中提取指定时间范围的字幕，时间戳重新从 0 开始"""
    SRT_BLOCK_RE = re.compile(
        r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})\s*\n(.*?)(?=\n\n|\Z)",
        re.DOTALL,
    )

    def ts_to_sec(h, ms):
        hh, mm, ss = h.split(":")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000

    def sec_to_srt(s):
        s = max(0.0, s)
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = int(s % 60)
        ms = int(round((s - int(s)) * 1000))
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    with open(srt_path, "r", encoding="utf-8") as f:
        content = f.read()

    out_entries = []
    new_idx = 1
    for m in SRT_BLOCK_RE.finditer(content):
        idx, sh, sms, eh, ems, text = m.groups()
        s = ts_to_sec(sh, sms)
        e = ts_to_sec(eh, ems)
        if e <= start_sec or s >= end_sec:
            continue
        # 重新计算相对时间
        ns = max(0.0, s - start_sec)
        ne = min(end_sec - start_sec, e - start_sec)
        text_clean = text.strip()
        out_entries.append(f"{new_idx}\n{sec_to_srt(ns)} --> {sec_to_srt(ne)}\n{text_clean}\n")
        new_idx += 1

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_entries))
    print(f"[filter] 提取 {new_idx - 1} 条字幕 → {out_path}")


def burn_bilingual(video_path: str, bilingual_json: str, output_path: str):
    """
    从双语 JSON 生成 ASS 字幕并烧录到视频。
    英文在上（小字号），中文在下（大字号），自动折行。
    """
    with open(bilingual_json, "r", encoding="utf-8") as f:
        entries = json.load(f)

    # 生成 ASS 内容
    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # EN: 小字，底部偏上
        "Style: EN,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2,1,2,20,20,100,1",
        # ZH: 大字，底部
        "Style: ZH,Microsoft YaHei,46,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2,1,2,20,20,20,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def sec_to_ass(s):
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h}:{m:02d}:{sec:05.2f}"

    def srt_ts_to_sec(ts: str) -> float:
        ts = ts.replace(",", ".")
        parts = ts.split(":")
        h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
        return h * 3600 + m * 60 + s

    def wrap_text(text: str, max_len: int = 40) -> str:
        """简单折行"""
        if len(text) <= max_len:
            return text
        words = text.split()
        lines, cur = [], []
        for w in words:
            if sum(len(x) + 1 for x in cur) + len(w) > max_len:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))
        return r"\N".join(lines)

    def wrap_zh(text: str, max_len: int = 18) -> str:
        """中文折行（按字符数）"""
        if len(text) <= max_len:
            return text
        parts = [text[i:i+max_len] for i in range(0, len(text), max_len)]
        return r"\N".join(parts)

    for e in entries:
        start_sec = srt_ts_to_sec(e["start"])
        end_sec = srt_ts_to_sec(e["end"])
        en_text = wrap_text(e.get("en", "").strip())
        zh_text = wrap_zh(e.get("zh", "").strip())

        t_start = sec_to_ass(start_sec)
        t_end = sec_to_ass(end_sec)

        if en_text:
            ass_lines.append(f"Dialogue: 0,{t_start},{t_end},EN,,0,0,0,,{en_text}")
        if zh_text:
            ass_lines.append(f"Dialogue: 0,{t_start},{t_end},ZH,,0,0,0,,{zh_text}")

    ass_content = "\n".join(ass_lines)
    ass_path = bilingual_json.replace(".json", ".ass")
    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write(ass_content)

    # ffmpeg 烧录
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    run([
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"ass={ass_path}",
        "-c:v", "libx264", "-crf", "18", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ])
    print(f"[burn] 完成 → {output_path}")


def main():
    parser = argparse.ArgumentParser(description="智能语义分段 + 双语字幕烧录")
    parser.add_argument("--url", required=True, help="YouTube 视频 URL")
    parser.add_argument("--output-dir", default="./output", help="输出目录")
    parser.add_argument("--target-duration", type=int, default=600,
                        help="目标章节时长（秒），默认 600 = 10分钟")
    parser.add_argument("--model", default="base",
                        help="Whisper 模型大小 (tiny/base/small/medium/large-v3)")
    parser.add_argument("--device", default="cpu", help="cpu 或 cuda")
    parser.add_argument("--cookies", default=None, help="yt-dlp cookies 文件路径")
    parser.add_argument("--batch-size", type=int, default=20, help="翻译批次大小")
    args = parser.parse_args()

    api_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        print("[main] 缺少环境变量 SILICONFLOW_API_KEY", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    tmp_dir = out_dir / "_tmp"
    segments_dir = out_dir / "segments"
    for d in [out_dir, tmp_dir, segments_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # ── Step 1: 下载视频 ────────────────────────────────────────────────────
    print("\n=== Step 1: 下载视频 ===")
    video_path = str(tmp_dir / "full.mp4")
    dl_cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", video_path,
        "--no-playlist",
    ]
    if args.cookies:
        dl_cmd += ["--cookies", args.cookies]
    dl_cmd.append(args.url)
    run(dl_cmd)

    # ── Step 2: ASR 全片转写 ────────────────────────────────────────────────
    print("\n=== Step 2: ASR 转写 ===")
    full_srt = str(tmp_dir / "full.srt")
    run([
        sys.executable, str(SCRIPTS_DIR / "transcribe.py"),
        "--input", video_path,
        "--output", full_srt,
        "--model", args.model,
        "--device", args.device,
        "--compute-type", "int8",
    ])

    # ── Step 3: 语义分段 ────────────────────────────────────────────────────
    print("\n=== Step 3: 语义分段 ===")
    segments_json = str(tmp_dir / "segments.json")
    run(
        [sys.executable, str(SCRIPTS_DIR / "segment.py"),
         "--input", full_srt,
         "--output", segments_json,
         "--target-duration", str(args.target_duration)],
        env={**os.environ},
    )

    with open(segments_json, "r", encoding="utf-8") as f:
        chapters = json.load(f)
    print(f"\n共 {len(chapters)} 个章节")

    # ── Step 4: 逐章节处理 ─────────────────────────────────────────────────
    print("\n=== Step 4: 逐章节处理 ===")
    for ch in chapters:
        idx = ch["index"]
        title = ch["title_zh"]
        start = ch["start"]
        end = ch["end"]
        start_sec = ch["start_sec"]
        end_sec = ch["end_sec"]
        safe_title = sanitize_filename(title)

        print(f"\n--- 章节 {idx:02d}: {title} ({start} ~ {end}) ---")

        ch_dir = tmp_dir / f"ch{idx:02d}"
        ch_dir.mkdir(exist_ok=True)

        # 4a. ffmpeg 切割视频
        ch_video = str(ch_dir / "clip.mp4")
        run([
            "ffmpeg", "-y",
            "-i", video_path,
            "-ss", start,
            "-to", end,
            "-c:v", "libx264", "-crf", "18", "-preset", "fast",
            "-c:a", "aac",
            ch_video,
        ])

        # 4b. 提取该章节字幕（时间相对化）
        ch_srt = str(ch_dir / "en.srt")
        srt_filter_by_time(full_srt, start_sec, end_sec, ch_srt)

        # 4c. 翻译
        ch_bilingual = str(ch_dir / "bilingual.json")
        run(
            [sys.executable, str(SCRIPTS_DIR / "translate.py"),
             "--input", ch_srt,
             "--output", ch_bilingual,
             "--batch-size", str(args.batch_size)],
            env={**os.environ},
        )

        # 4d. 烧录双语字幕
        ch_output = str(segments_dir / f"{idx:02d}_{safe_title}.mp4")
        burn_bilingual(ch_video, ch_bilingual, ch_output)

    # ── 汇总 ─────────────────────────────────────────────────────────────────
    print("\n=== 全部完成！===")
    print(f"输出目录: {segments_dir.absolute()}")
    for ch in chapters:
        safe_title = sanitize_filename(ch["title_zh"])
        out_file = segments_dir / f"{ch['index']:02d}_{safe_title}.mp4"
        size_mb = out_file.stat().st_size / 1024 / 1024 if out_file.exists() else 0
        print(f"  [{ch['index']:02d}] {ch['title_zh']} → {out_file.name} ({size_mb:.1f}MB)")


if __name__ == "__main__":
    main()
