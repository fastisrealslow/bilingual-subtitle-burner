#!/usr/bin/env python3
"""
run_pipeline.py — 金句短片全流程（下完 large-v3 后一键跑）

用法：
  SILICONFLOW_API_KEY=xxx python3 run_pipeline.py \
    --video output/_tmp/full.mp4 \
    --speaker 李录 \
    --top-n 5

流程：
  1. ASR（large-v3，中文）
  2. 翻译（中→英，双语 JSON）
  3. 金句识别
  4. 文案生成
  5. 切片+字幕烧录
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).parent / "scripts"
LARGE_V3 = "/home/node/.cache/whisper/large-v3"
BASE_MODEL = "/home/node/.cache/whisper/base"


def get_video_duration(video: str) -> float:
    """用 ffmpeg stderr 解析时长，不依赖 ffprobe"""
    r = subprocess.run(
        ["ffmpeg", "-i", video],
        capture_output=True, text=True
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr + r.stdout)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return 0.0


def run(cmd, **kw):
    print(f"\n>>> {' '.join(str(c) for c in cmd)}", flush=True)
    result = subprocess.run(cmd, **kw)
    if result.returncode != 0:
        print(f"[ERROR] 命令失败，退出码 {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--speaker", default="李录")
    parser.add_argument("--channel", default="价值投资讲堂")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--language", default="zh", help="视频语言，zh/en/auto")
    parser.add_argument("--model", default=None, help="Whisper 模型路径，默认自动选 large-v3 或 base")
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--skip-translate", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        print("需要 SILICONFLOW_API_KEY", file=sys.stderr); sys.exit(1)

    env = {
        **os.environ,
        "SILICONFLOW_API_KEY": api_key,
        "SILICONFLOW_MODEL": os.environ.get("SILICONFLOW_MODEL", "Qwen/Qwen3-32B"),
    }

    out = Path(args.output_dir)
    tmp = out / "_tmp"
    clips = out / "clips"
    for d in [tmp, clips]:
        d.mkdir(parents=True, exist_ok=True)

    # 选模型
    if args.model:
        model = args.model
    elif Path(LARGE_V3 + "/model.bin").exists() and \
         Path(LARGE_V3 + "/model.bin").stat().st_size > 1_400_000_000:
        model = LARGE_V3
        print(f"[info] 使用 large-v3: {model}", flush=True)
    else:
        model = BASE_MODEL
        print(f"[info] large-v3 未就绪，降级使用 base: {model}", flush=True)

    srt = tmp / "full.srt"
    bilingual = tmp / "full_bilingual.json"

    # ── Step 1: ASR ──────────────────────────────────────────────────
    if args.skip_asr and srt.exists() and srt.stat().st_size > 100:
        print(f"\n[Step 1] 跳过 ASR，使用 {srt}", flush=True)
    else:
        print("\n=== Step 1: ASR 转写 ===", flush=True)
        lang_arg = [] if args.language == "auto" else ["--language", args.language]
        run([
            sys.executable, str(SCRIPTS / "transcribe.py"),
            "--input", args.video,
            "--output", str(srt),
            "--model", model,
            "--device", "cpu",
            "--compute-type", "int8",
            *lang_arg,
        ], env=env)

    # ── Step 2: 翻译 ─────────────────────────────────────────────────
    if args.skip_translate and bilingual.exists() and bilingual.stat().st_size > 100:
        print(f"\n[Step 2] 跳过翻译，使用 {bilingual}", flush=True)
    else:
        print("\n=== Step 2: 翻译字幕（中文→英文，英文在上） ===", flush=True)
        run([
            sys.executable, str(SCRIPTS / "translate.py"),
            "--input", str(srt),
            "--output", str(bilingual),
            "--direction", "zh2en",
            "--batch-size", "20",
        ], env=env)

    # ── Step 3: 金句识别 ──────────────────────────────────────────────
    total_dur = get_video_duration(args.video)
    print(f"\n=== Step 3: 金句识别（视频时长={total_dur:.1f}s）===", flush=True)
    highlights = tmp / "highlights.json"
    run([
        sys.executable, str(SCRIPTS / "highlight.py"),
        "--srt", str(srt),
        "--bilingual", str(bilingual),
        "--output", str(highlights),
        "--speaker", args.speaker,
        "--top-n", str(args.top_n),
        "--total-duration", str(total_dur),
    ], env=env)

    # ── Step 4: 文案生成 ──────────────────────────────────────────────
    print("\n=== Step 4: 文案生成 ===", flush=True)
    manifest = tmp / "manifest.json"
    run([
        sys.executable, str(SCRIPTS / "copywrite.py"),
        "--highlights", str(highlights),
        "--output", str(manifest),
        "--speaker", args.speaker,
        "--channel", args.channel,
    ], env=env)

    # ── Step 5: 切片+烧录 ─────────────────────────────────────────────
    print("\n=== Step 5: 切片+双语字幕烧录 ===", flush=True)
    run([
        sys.executable, str(SCRIPTS / "clip.py"),
        "--video", args.video,
        "--manifest", str(manifest),
        "--srt", str(srt),
        "--bilingual", str(bilingual),
        "--output-dir", str(clips),
        "--srt-lang", "zh",
    ], env=env)

    # ── 汇总 ──────────────────────────────────────────────────────────
    print("\n\n========== 完成！==========", flush=True)
    with open(manifest, encoding="utf-8") as f:
        items = json.load(f)

    for item in items:
        fname = item.get("video_filename", f"{item['rank']:02d}.mp4")
        p = clips / fname
        size = p.stat().st_size / 1024 / 1024 if p.exists() else 0
        print(f"\n  [{item['rank']:02d}] ⭐{item['score']:.1f} | {item['clip_start']}~{item['clip_end']}", flush=True)
        print(f"       标题：{item['title']}", flush=True)
        print(f"       文件：{fname} ({size:.1f}MB)", flush=True)

    print(f"\n输出目录：{clips.absolute()}", flush=True)


if __name__ == "__main__":
    main()
