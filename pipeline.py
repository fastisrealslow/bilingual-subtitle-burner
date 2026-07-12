#!/usr/bin/env python3
"""
pipeline.py — 金句短片全流程主控

流程：
  1. ASR 转写（如果 full.srt 不存在）
  2. 翻译全片字幕（如果 bilingual.json 不存在）
  3. highlight.py：金句识别+打分
  4. copywrite.py：标题/文案/标签生成
  5. clip.py：切片+字幕烧录
  6. （可选）segment.py：整片语义分段+双语长片输出

用法：
  python pipeline.py \
    --video output/_tmp/full.mp4 \
    --output-dir ./output \
    --speaker 帕伯莱 \
    --top-n 5

环境变量：
  SILICONFLOW_API_KEY  （必填）
  SILICONFLOW_MODEL    （可选，默认 Qwen/Qwen3-8B）
  SPEAKER_NAME         （可选，会被 --speaker 覆盖）
  CHANNEL_NAME         （可选）
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent / "scripts"
WHISPER_MODEL_DIR = "/home/node/.cache/whisper/base2"


def run(cmd: list, **kw):
    print(f"\n>>> {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kw)


def main():
    print("[⚠️ DEPRECATED] pipeline.py 已废弃，请改用统一入口 run.py"
          "（支持断点续跑 / 封面 / 上传素材包）。", file=sys.stderr, flush=True)
    parser = argparse.ArgumentParser(description="[DEPRECATED] 金句短片全流程，请改用 run.py")
    parser.add_argument("--video", required=True, help="完整视频文件路径")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--speaker", default="演讲者", help="说话人名，如帕伯莱")
    parser.add_argument("--channel", default="价值投资讲堂")
    parser.add_argument("--top-n", type=int, default=5, help="输出前N个金句")
    parser.add_argument("--whisper-model", default=WHISPER_MODEL_DIR)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--skip-asr", action="store_true", help="跳过ASR（full.srt已存在）")
    parser.add_argument("--skip-translate", action="store_true", help="跳过翻译")
    parser.add_argument("--also-segment", action="store_true", help="同时输出语义分段长片")
    args = parser.parse_args()

    api_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
    if not api_key:
        print("缺少 SILICONFLOW_API_KEY", file=sys.stderr); sys.exit(1)

    env = {**os.environ,
           "SILICONFLOW_API_KEY": api_key,
           "SILICONFLOW_MODEL": (os.environ.get("SILICONFLOW_MODEL") or "Qwen/Qwen3-8B"),
           "SPEAKER_NAME": args.speaker,
           "CHANNEL_NAME": args.channel}

    out = Path(args.output_dir)
    tmp = out / "_tmp"
    clips_dir = out / "clips"
    for d in [out, tmp, clips_dir]:
        d.mkdir(parents=True, exist_ok=True)

    full_srt = tmp / "full.srt"
    bilingual_json = tmp / "full_bilingual.json"

    # ── Step 1: ASR ────────────────────────────────────────────────────────────
    if args.skip_asr and full_srt.exists():
        print(f"\n[Step 1] 跳过 ASR，使用已有字幕: {full_srt}", flush=True)
    else:
        print("\n=== Step 1: ASR 转写 ===", flush=True)
        # 直接用 Python 调用，指定本地模型路径
        asr_script = f"""
import sys
sys.stdout.reconfigure(line_buffering=True)
from faster_whisper import WhisperModel

print('[ASR] 加载模型 {args.whisper_model} ...', flush=True)
model = WhisperModel('{args.whisper_model}', device='{args.device}', compute_type='int8')

print('[ASR] 开始转写 {args.video} ...', flush=True)
segments, info = model.transcribe(
    '{args.video}',
    language='en',
    vad_filter=True,
    vad_parameters=dict(min_silence_duration_ms=500),
)
print(f'[ASR] 语言={{info.language}} 置信度={{info.language_probability:.2f}}', flush=True)

def fmt(s):
    if s<0: s=0
    ms=int(round(s*1000)); h=ms//3600000; ms-=h*3600000
    m=ms//60000; ms-=m*60000; sec=ms//1000; ms-=sec*1000
    return f'{{h:02d}}:{{m:02d}}:{{sec:02d}},{{ms:03d}}'

count=0
with open('{full_srt}','w',encoding='utf-8') as f:
    for i,seg in enumerate(segments,1):
        t=seg.text.strip()
        if not t: continue
        f.write(f'{{i}}\\n{{fmt(seg.start)}} --> {{fmt(seg.end)}}\\n{{t}}\\n\\n')
        count+=1
        if count%100==0: print(f'  已转写 {{count}} 条...', flush=True)
print(f'[ASR] 完成，共 {{count}} 条 -> {full_srt}', flush=True)
"""
        run([sys.executable, "-c", asr_script], env=env)

    # ── Step 2: 翻译全片 ───────────────────────────────────────────────────────
    if args.skip_translate and bilingual_json.exists():
        print(f"\n[Step 2] 跳过翻译，使用已有: {bilingual_json}", flush=True)
    else:
        print("\n=== Step 2: 翻译全片字幕 ===", flush=True)
        run([sys.executable, str(SCRIPTS_DIR / "translate.py"),
             "--input", str(full_srt),
             "--output", str(bilingual_json),
             "--batch-size", str(args.batch_size)], env=env)

    # ── Step 3: 金句识别 ───────────────────────────────────────────────────────
    print("\n=== Step 3: 金句识别 ===", flush=True)
    highlights_json = tmp / "highlights.json"
    
    # 获取视频总时长
    import subprocess as sp
    dur_out = sp.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", args.video],
        stderr=sp.DEVNULL
    ).decode().strip()
    total_duration = float(dur_out) if dur_out else 0.0

    run([sys.executable, str(SCRIPTS_DIR / "highlight.py"),
         "--srt", str(full_srt),
         "--bilingual", str(bilingual_json),
         "--output", str(highlights_json),
         "--speaker", args.speaker,
         "--top-n", str(args.top_n),
         "--total-duration", str(total_duration)], env=env)

    # ── Step 4: 文案生成 ───────────────────────────────────────────────────────
    print("\n=== Step 4: 文案生成 ===", flush=True)
    manifest_json = tmp / "manifest.json"
    run([sys.executable, str(SCRIPTS_DIR / "copywrite.py"),
         "--highlights", str(highlights_json),
         "--output", str(manifest_json),
         "--speaker", args.speaker,
         "--channel", args.channel], env=env)

    # ── Step 5: 切片+烧录 ─────────────────────────────────────────────────────
    print("\n=== Step 5: 切片+双语字幕烧录 ===", flush=True)
    run([sys.executable, str(SCRIPTS_DIR / "clip.py"),
         "--video", args.video,
         "--manifest", str(manifest_json),
         "--srt", str(full_srt),
         "--bilingual", str(bilingual_json),
         "--output-dir", str(clips_dir)], env=env)

    # ── Step 6: 语义分段长片（可选）────────────────────────────────────────────
    if args.also_segment:
        print("\n=== Step 6: 语义分段长片 ===", flush=True)
        run([sys.executable, "run_from_file.py",
             "--input", args.video,
             "--output-dir", args.output_dir,
             "--target-duration", "600",
             "--skip-asr",      # full.srt 已存在
             "--skip-translate"], env=env)

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    print("\n\n========== 全部完成！==========", flush=True)
    with open(manifest_json, encoding="utf-8") as f:
        manifest = json.load(f)

    print(f"\n📋 金句清单（共 {len(manifest)} 条）：", flush=True)
    for item in manifest:
        p = clips_dir / item.get("video_filename", f"{item['rank']:02d}.mp4")
        size = p.stat().st_size / 1024 / 1024 if p.exists() else 0
        print(f"\n  [{item['rank']:02d}] ⭐{item['score']:.1f}分 | {item['clip_start']}~{item['clip_end']} ({item['clip_duration_sec']:.0f}s)", flush=True)
        print(f"       标题：{item['title']}", flush=True)
        print(f"       标签：{item.get('tags', [])}", flush=True)
        print(f"       文件：{p.name} ({size:.1f}MB)", flush=True)

    print(f"\n输出目录: {clips_dir.absolute()}", flush=True)
    print("清单文件:", manifest_json, flush=True)


if __name__ == "__main__":
    main()
