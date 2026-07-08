#!/usr/bin/env python3
"""
run_from_file.py — 从已下载的视频文件开始，跳过 yt-dlp 下载步骤。
用法:
  python run_from_file.py --input /path/to/video.mp4 --output-dir ./output \
                          [--target-duration 600] [--model base]
"""
import argparse, json, os, re, subprocess, sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent / "scripts"


def run(cmd, **kw):
    print(f"\n[run] {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kw)


def srt_filter(srt_path, start_sec, end_sec, out_path):
    SRT_RE = re.compile(
        r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})\s*\n(.*?)(?=\n\n|\Z)",
        re.DOTALL,
    )
    def ts2s(h, ms):
        hh, mm, ss = h.split(":")
        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000
    def s2srt(s):
        s = max(0.0, s)
        h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60)
        ms = int(round((s - int(s)) * 1000))
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    with open(srt_path) as f:
        content = f.read()
    out = []; idx = 1
    for m in SRT_RE.finditer(content):
        _, sh, sms, eh, ems, text = m.groups()
        s = ts2s(sh, sms); e = ts2s(eh, ems)
        if e <= start_sec or s >= end_sec:
            continue
        ns = max(0.0, s - start_sec)
        ne = min(end_sec - start_sec, e - start_sec)
        out.append(f"{idx}\n{s2srt(ns)} --> {s2srt(ne)}\n{text.strip()}\n")
        idx += 1
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write("\n".join(out))
    print(f"[filter] {idx-1} 条字幕 → {out_path}", flush=True)


def burn_ass(ch_video, entries, out_mp4):
    def ts2s(ts):
        ts = ts.replace(",", ".")
        p = ts.split(":")
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
    def s2ass(s):
        h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
        return f"{h}:{m:02d}:{sec:05.2f}"
    def wrap_en(t, n=42):
        if len(t) <= n: return t
        words = t.split(); lines, cur = [], []
        for w in words:
            if sum(len(x) + 1 for x in cur) + len(w) > n:
                lines.append(" ".join(cur)); cur = [w]
            else:
                cur.append(w)
        if cur: lines.append(" ".join(cur))
        return r"\N".join(lines)
    def wrap_zh(t, n=20):
        if len(t) <= n: return t
        return r"\N".join(t[i:i+n] for i in range(0, len(t), n))

    ass = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1920", "PlayResY: 1080", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: EN,Arial,36,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2,1,2,20,20,100,1",
        "Style: ZH,Microsoft YaHei,46,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,2,1,2,20,20,20,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for e in entries:
        s = ts2s(e["start"]); e_end = ts2s(e["end"])
        en_t = wrap_en(e.get("en", "").strip())
        zh_t = wrap_zh(e.get("zh", "").strip())
        if en_t: ass.append(f"Dialogue: 0,{s2ass(s)},{s2ass(e_end)},EN,,0,0,0,,{en_t}")
        if zh_t: ass.append(f"Dialogue: 0,{s2ass(s)},{s2ass(e_end)},ZH,,0,0,0,,{zh_t}")

    ass_path = str(Path(ch_video).with_suffix(".ass"))
    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(ass))
    run(["ffmpeg", "-y", "-i", ch_video, "-vf", f"ass={ass_path}",
         "-c:v", "libx264", "-crf", "18", "-preset", "fast",
         "-c:a", "aac", "-b:a", "128k", out_mp4])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="已下载的视频文件路径")
    parser.add_argument("--output-dir", default="./output")
    parser.add_argument("--target-duration", type=int, default=600)
    parser.add_argument("--model", default="base")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()

    if not os.environ.get("SILICONFLOW_API_KEY"):
        print("缺少 SILICONFLOW_API_KEY", file=sys.stderr); sys.exit(1)

    out_dir = Path(args.output_dir)
    tmp_dir = out_dir / "_tmp"
    segments_dir = out_dir / "segments"
    for d in [tmp_dir, segments_dir]:
        d.mkdir(parents=True, exist_ok=True)

    full_srt = tmp_dir / "full.srt"
    segments_json = tmp_dir / "segments.json"

    # Step 1: ASR
    print("\n=== Step 1: ASR 转写 ===", flush=True)
    run([sys.executable, str(SCRIPTS_DIR / "transcribe.py"),
         "--input", args.input,
         "--output", str(full_srt),
         "--model", args.model,
         "--device", args.device,
         "--compute-type", "int8"])

    # Step 2: 分段
    print("\n=== Step 2: 语义分段 ===", flush=True)
    run([sys.executable, str(SCRIPTS_DIR / "segment.py"),
         "--input", str(full_srt),
         "--output", str(segments_json),
         "--target-duration", str(args.target_duration)])

    with open(segments_json) as f:
        chapters = json.load(f)
    print(f"\n共 {len(chapters)} 章节:", flush=True)
    for ch in chapters:
        print(f"  [{ch['index']:02d}] {ch['start']}~{ch['end']} ({ch['duration_sec']/60:.1f}min) {ch['title_zh']}", flush=True)

    # Step 3: 逐章节
    print("\n=== Step 3: 逐章节处理 ===", flush=True)
    for ch in chapters:
        idx = ch["index"]; title = ch["title_zh"]
        safe = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40]
        print(f"\n--- [{idx:02d}] {title} ({ch['start']}~{ch['end']}) ---", flush=True)

        ch_dir = tmp_dir / f"ch{idx:02d}"
        ch_dir.mkdir(exist_ok=True)
        ch_video = str(ch_dir / "clip.mp4")

        run(["ffmpeg", "-y", "-i", args.input,
             "-ss", ch["start"], "-to", ch["end"],
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", ch_video])

        ch_srt = str(ch_dir / "en.srt")
        srt_filter(str(full_srt), ch["start_sec"], ch["end_sec"], ch_srt)

        ch_bilingual = str(ch_dir / "bilingual.json")
        run([sys.executable, str(SCRIPTS_DIR / "translate.py"),
             "--input", ch_srt, "--output", ch_bilingual,
             "--batch-size", str(args.batch_size)])

        with open(ch_bilingual) as f:
            entries = json.load(f)

        out_mp4 = str(segments_dir / f"{idx:02d}_{safe}.mp4")
        burn_ass(ch_video, entries, out_mp4)

        size = Path(out_mp4).stat().st_size / 1024 / 1024
        print(f"\n✅ [{idx:02d}] {title} → {Path(out_mp4).name} ({size:.1f}MB)", flush=True)

    print("\n\n=== 全部完成！===", flush=True)
    print(f"输出目录: {segments_dir.absolute()}", flush=True)
    for ch in chapters:
        safe = re.sub(r'[\\/:*?"<>|]', '_', ch['title_zh']).strip()[:40]
        p = segments_dir / f"{ch['index']:02d}_{safe}.mp4"
        size = p.stat().st_size / 1024 / 1024 if p.exists() else 0
        print(f"  [{ch['index']:02d}] {ch['title_zh']} → {p.name} ({size:.1f}MB)", flush=True)


if __name__ == "__main__":
    main()
