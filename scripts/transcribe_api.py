#!/usr/bin/env python3
"""
transcribe_api.py — 用硅基流动 SenseVoice API 转写，分段上传拼接时间戳。

原理：
  1. 把音频切成 N 秒小段（默认 60s，有重叠避免断句）
  2. 每段单独上传 API，拿到纯文本
  3. 用本地 Whisper tiny 仅做对齐（不识别，只对齐时间戳）
     或者：用字符均匀分配法估算时间戳
  4. 拼成完整 SRT

因为 API 不返回时间戳，这里用「字符均匀分配」估算，
对于不需要精确时间轴的场景（金句识别、翻译）足够用。

用法：
  python scripts/transcribe_api.py \
    --input output/_tmp/full.mp4 \
    --output output/_tmp/full_api.srt \
    --segment-sec 60 \
    --lang zh
"""
import argparse, json, math, os, re, subprocess, sys, time, tempfile
from pathlib import Path
import requests

API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")
API_URL = "https://api.siliconflow.cn/v1/audio/transcriptions"
MODEL   = "FunAudioLLM/SenseVoiceSmall"


def fmt(s: float) -> str:
    s = max(0.0, s)
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def get_duration(path: str) -> float:
    # 用 ffmpeg stderr 解析时长（避免依赖 ffprobe）
    r = subprocess.run(
        ["ffmpeg", "-i", path],
        capture_output=True, text=True
    )
    output = r.stderr + r.stdout
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        return h * 3600 + mn * 60 + s
    return 0.0


def extract_segment(src: str, start: float, duration: float, dst: str):
    subprocess.run([
        "ffmpeg", "-y", "-i", src,
        "-ss", str(start), "-t", str(duration),
        "-vn", "-ar", "16000", "-ac", "1", "-f", "mp3", dst
    ], capture_output=True)


def transcribe_segment(audio_path: str, lang: str = "zh") -> str:
    with open(audio_path, "rb") as f:
        resp = requests.post(
            API_URL,
            headers={"Authorization": f"Bearer {API_KEY}"},
            files={"file": ("audio.mp3", f, "audio/mpeg")},
            data={"model": MODEL, "response_format": "json", "language": lang},
            timeout=120,
        )
    if resp.status_code != 200:
        print(f"  [warn] API 错误 {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return ""
    data = resp.json()
    # 去掉 SenseVoice 的情绪标签 <|HAPPY|> 等
    text = data.get("text", "")
    text = re.sub(r"<\|[^|]+\|>", "", text).strip()
    return text


def split_to_sentences(text: str) -> list[str]:
    """按标点简单分句"""
    parts = re.split(r"([。！？，、；…]+)", text)
    sentences = []
    buf = ""
    for p in parts:
        buf += p
        if re.search(r"[。！？；…]", buf) and len(buf.strip()) > 2:
            sentences.append(buf.strip())
            buf = ""
    if buf.strip():
        sentences.append(buf.strip())
    return [s for s in sentences if s]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--segment-sec", type=float, default=60.0, help="每段时长（秒）")
    parser.add_argument("--overlap-sec", type=float, default=2.0, help="段间重叠（秒）")
    parser.add_argument("--lang", default="zh")
    args = parser.parse_args()

    if not API_KEY:
        print("需要 SILICONFLOW_API_KEY", file=sys.stderr); sys.exit(1)

    total = get_duration(args.input)
    if total <= 0:
        print("[warn] 无法获取视频时长", file=sys.stderr)
    print(f"[api-asr] 视频总时长: {total:.1f}s", flush=True)

    seg_sec = args.segment_sec
    overlap = args.overlap_sec
    n_segs = math.ceil(total / seg_sec)
    print(f"[api-asr] 共 {n_segs} 段，每段 {seg_sec}s", flush=True)

    srt_entries = []
    idx = 1

    with tempfile.TemporaryDirectory() as tmpdir:
        for i in range(n_segs):
            start = i * seg_sec
            duration = min(seg_sec + overlap, total - start)
            if duration <= 0:
                break

            seg_path = os.path.join(tmpdir, f"seg_{i:03d}.mp3")
            extract_segment(args.input, start, duration, seg_path)

            print(f"  [seg {i+1}/{n_segs}] {fmt(start)} ~ {fmt(start+duration)} 上传中...", flush=True)
            text = transcribe_segment(seg_path, args.lang)
            if not text:
                print(f"  [seg {i+1}] 空结果，跳过", flush=True)
                continue

            print(f"  [seg {i+1}] '{text[:60]}...' ({len(text)} chars)", flush=True)

            # 按字符均匀分配时间戳（有效时长 = seg_sec，不含 overlap）
            effective_end = min(start + seg_sec, total)
            effective_dur = effective_end - start

            sentences = split_to_sentences(text)
            if not sentences:
                sentences = [text]

            total_chars = sum(len(s) for s in sentences)
            cur = start
            for s in sentences:
                if not s.strip():
                    continue
                ratio = len(s) / total_chars if total_chars > 0 else 1
                dur = max(0.5, effective_dur * ratio)
                end_t = min(cur + dur, effective_end)
                srt_entries.append((idx, cur, end_t, s.strip()))
                idx += 1
                cur = end_t

            time.sleep(0.3)  # 避免频率限制

    # 写 SRT
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        for no, s, e, t in srt_entries:
            f.write(f"{no}\n{fmt(s)} --> {fmt(e)}\n{t}\n\n")

    print(f"\n[api-asr] 完成，共 {len(srt_entries)} 条 → {args.output}", flush=True)


if __name__ == "__main__":
    main()
