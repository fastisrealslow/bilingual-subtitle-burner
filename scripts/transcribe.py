#!/usr/bin/env python3
"""
transcribe.py — 使用 faster-whisper 从视频中提取英文字幕（带时间轴）。
输出标准 SRT 文件。
"""
import argparse
import os
import sys

from faster_whisper import WhisperModel


def format_timestamp(seconds: float) -> str:
    """把秒转换成 SRT 时间格式 HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000.0))
    hours = ms // 3_600_000
    ms -= hours * 3_600_000
    minutes = ms // 60_000
    ms -= minutes * 60_000
    secs = ms // 1000
    ms -= secs * 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def main():
    parser = argparse.ArgumentParser(description="用 faster-whisper 转写视频/音频为英文 SRT")
    parser.add_argument("--input", required=True, help="输入视频或音频文件路径")
    parser.add_argument("--output", required=True, help="输出 SRT 路径")
    parser.add_argument("--model", default="base", help="模型大小: tiny/base/small/medium/large-v3")
    parser.add_argument("--language", default="en", help="源语言，默认 en")
    parser.add_argument("--compute-type", default="int8", help="int8 / int8_float16 / float16 / float32")
    parser.add_argument("--device", default="cpu", help="cpu 或 cuda")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[transcribe] 找不到输入文件: {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"[transcribe] 加载模型 {args.model} (device={args.device}, compute={args.compute_type}) ...")
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)

    print(f"[transcribe] 开始转写: {args.input}")
    segments, info = model.transcribe(
        args.input,
        language=args.language,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
    )
    print(f"[transcribe] 检测语言={info.language} (置信度={info.language_probability:.2f})")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    count = 0
    with open(args.output, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, start=1):
            text = seg.text.strip()
            if not text:
                continue
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg.start)} --> {format_timestamp(seg.end)}\n")
            f.write(f"{text}\n\n")
            count += 1
            print(f"  [{format_timestamp(seg.start)}] {text}")

    print(f"[transcribe] 完成，共 {count} 条字幕 -> {args.output}")


if __name__ == "__main__":
    main()
