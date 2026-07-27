#!/usr/bin/env python3
"""Produce one finished bilingual short video from a source clip + real transcript.

Two phases:
  cues  — slice the transcript to the chosen window (cut points via highlight.align_clips)
          and merge it into sentence-level cues, ready for translation.
  build — burn bilingual subtitles, pick + render a cover, emit meta.json and frames.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

REPO = Path("/home/user/workspace/bilingual-subtitle-burner-3984ea42-8abeedc3")
sys.path.insert(0, str(REPO / "scripts"))
from highlight import parse_srt, align_clips          # noqa: E402
from platform_rules import normalize_cjk_punctuation  # noqa: E402

ZH_FONT_FILE = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
ZH_BOLD_FILE = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
EN_FONT_FILE = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
ZH_FONT_NAME = "Noto Sans CJK SC"
EN_FONT_NAME = "Arial"

SIDE_MARGIN = 40      # subtitle safe area, px per side
BOTTOM_MARGIN = 22
MAX_ZH_LINES = 3
MAX_EN_LINES = 3

CJK = r"一-鿿㐀-䶿぀-ヿ"
ZH_SENT_END = "。！？；"
EN_SENT_END = ".!?"
# A line must never begin with these.
NO_LINE_START = "，。！？；：、）」』%,.!?;:)]}"


def sh(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def probe(path, *entries):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-show_entries",
         "format=duration", "-of", "json", str(path)],
        check=True, capture_output=True, text=True).stdout
    j = json.loads(out)
    st = j["streams"][0]
    return int(st["width"]), int(st["height"]), float(j["format"]["duration"])


# ── cues ──────────────────────────────────────────────────────────────────────

def merge_cues(entries, lang, max_sec=6.5, max_chars=None):
    """Merge SRT entries into sentence-level cues."""
    if max_chars is None:
        max_chars = 34 if lang == "zh" else 110
    enders = ZH_SENT_END if lang == "zh" else EN_SENT_END
    cues, buf = [], []

    def flush():
        if not buf:
            return
        cues.append({
            "start": buf[0]["start_sec"],
            "end": buf[-1]["end_sec"],
            "text": ("" if lang == "zh" else " ").join(
                e["text"].strip() for e in buf).strip(),
        })
        buf.clear()

    for e in entries:
        buf.append(e)
        txt = "".join(x["text"] for x in buf)
        dur = buf[-1]["end_sec"] - buf[0]["start_sec"]
        if e["text"].rstrip().endswith(tuple(enders)) or dur >= max_sec \
                or len(txt) >= max_chars:
            flush()
    flush()
    return cues


def cmd_cues(a):
    entries = parse_srt(a.srt)
    total = entries[-1]["end_sec"] if entries else 0.0
    item = {"start_sec": a.start, "end_sec": a.end}
    aligned = align_clips([item], entries, total)[0]
    s, e = aligned["clip_start_sec"], aligned["clip_end_sec"]
    print(f"[align_clips] {a.start:.1f}~{a.end:.1f} -> {s:.2f}~{e:.2f} "
          f"({e - s:.1f}s)")

    # Keep only entries fully inside the window; a partially-covered entry at
    # either edge is exactly the "half sentence" the cut is supposed to avoid.
    win = [x for x in entries
           if x["start_sec"] >= s - 0.05 and x["end_sec"] <= e + 0.05]
    cues = merge_cues(win, a.lang)
    for c in cues:                       # times relative to clip start
        c["start"] = max(0.0, c["start"] - s)
        c["end"] = min(e - s, c["end"] - s)
    key = "zh" if a.lang == "zh" else "en"
    out = {"clip_start_sec": s, "clip_end_sec": e, "duration_sec": e - s,
           "lang": a.lang,
           "cues": [{"start": round(c["start"], 2), "end": round(c["end"], 2),
                     key: c["text"], ("en" if key == "zh" else "zh"): ""}
                    for c in cues]}
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"[cues] {len(cues)} cues -> {a.out}")
    for c in out["cues"]:
        print(f'  [{c["start"]:6.2f}-{c["end"]:6.2f}] {c[key]}')


# ── text wrapping ─────────────────────────────────────────────────────────────

def wrap_by_width(text, font, max_w, latin_word_safe):
    """Greedy wrap measured against the real font. Never splits a Latin word."""
    text = " ".join(text.split())
    if not text:
        return []
    if latin_word_safe:
        tokens = text.split(" ")
        joiner = " "
    else:
        tokens = list(text)
        joiner = ""
    lines, cur = [], ""
    for tok in tokens:
        cand = tok if not cur else cur + joiner + tok
        if font.getlength(cand) <= max_w or not cur:
            cur = cand
        else:
            # don't let a line start with closing punctuation
            if not latin_word_safe and tok and tok[0] in NO_LINE_START:
                cur = cand
            else:
                lines.append(cur)
                cur = tok
    if cur:
        lines.append(cur)
    return lines


# ── ASS ───────────────────────────────────────────────────────────────────────

def build_ass(cues, w, h, path):
    zh_size = 28 if h >= 420 else 24
    en_size = int(round(zh_size * 0.72))
    zh_lh = int(round(zh_size * 1.28))
    en_lh = int(round(en_size * 1.30))
    usable = w - 2 * SIDE_MARGIN
    zh_font = ImageFont.truetype(ZH_FONT_FILE, zh_size)
    en_font = ImageFont.truetype(EN_FONT_FILE, en_size)

    head = [
        "[Script Info]", "ScriptType: v4.00+", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {w}", f"PlayResY: {h}", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: ZH,{ZH_FONT_NAME},{zh_size},&H00FFFFFF,&H000000FF,&H00202020,"
        f"&H90000000,-1,0,0,0,100,100,0,0,1,2.6,1.2,2,"
        f"{SIDE_MARGIN},{SIDE_MARGIN},{BOTTOM_MARGIN},1",
        f"Style: EN,{EN_FONT_NAME},{en_size},&H00E2E2E2,&H000000FF,&H00202020,"
        f"&H90000000,0,0,0,0,100,100,0,0,1,2.2,1.0,2,"
        f"{SIDE_MARGIN},{SIDE_MARGIN},{BOTTOM_MARGIN},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text",
    ]

    def t(s):
        s = max(0.0, s)
        return f"{int(s//3600)}:{int(s%3600//60):02d}:{s%60:05.2f}"

    stats = {"max_zh_lines": 0, "max_en_lines": 0, "max_block_px": 0}
    body = []
    for c in cues:
        zh = normalize_cjk_punctuation((c.get("zh") or "").strip())
        en = " ".join((c.get("en") or "").split())
        zl = wrap_by_width(zh, zh_font, usable, False)[:MAX_ZH_LINES]
        el = wrap_by_width(en, en_font, usable, True)[:MAX_EN_LINES]
        st, et = t(c["start"]), t(c["end"])
        if zl:
            body.append(f"Dialogue: 0,{st},{et},ZH,,0,0,{BOTTOM_MARGIN},,"
                        + r"\N".join(zl))
        if el:
            mv = BOTTOM_MARGIN + len(zl) * zh_lh
            body.append(f"Dialogue: 0,{st},{et},EN,,0,0,{mv},,"
                        + r"\N".join(el))
        stats["max_zh_lines"] = max(stats["max_zh_lines"], len(zl))
        stats["max_en_lines"] = max(stats["max_en_lines"], len(el))
        stats["max_block_px"] = max(
            stats["max_block_px"],
            BOTTOM_MARGIN + len(zl) * zh_lh + len(el) * en_lh)
    Path(path).write_text("\n".join(head + body), encoding="utf-8-sig")
    stats["subtitle_top_px"] = h - stats["max_block_px"]
    return stats


# ── cover ─────────────────────────────────────────────────────────────────────

def pick_cover_frame(clip, work, n=26, force_time=None):
    w, h, dur = probe(clip)
    cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    if force_time is not None:
        # Stock-footage sources have no usable portrait of the speaker, so the
        # frame is chosen by hand; still detect a face to place the title band.
        p = work / "cand_forced.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{force_time:.2f}",
                        "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(p)],
                       check=True, capture_output=True)
        img = cv2.imread(str(p))
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.1, 6, minSize=(50, 50))
        face = None
        ratio = 0.0
        if len(faces):
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            face = [int(fx), int(fy), int(fw), int(fh)]
            ratio = round((fw * fh) / float(w * h), 4)
        return {"path": p, "time": force_time, "score": 0, "face": face,
                "face_ratio": ratio, "sharpness": 0.0, "frames_sampled": 1,
                "frames_with_face": int(face is not None), "manual": True}

    best = None
    considered = 0
    for i in range(n):
        ts = dur * (i + 0.5) / n
        p = work / f"cand_{i:02d}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{ts:.2f}",
                        "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(p)],
                       check=True, capture_output=True)
        if not p.exists():
            continue
        img = cv2.imread(str(p))
        if img is None:
            continue
        considered += 1
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.1, 6, minSize=(50, 50))
        if len(faces) == 0:
            continue
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        area = (fw * fh) / float(w * h)
        sharp = cv2.Laplacian(gray, cv2.CV_64F).var()
        # favour a decent-sized, sharp face
        score = min(area, 0.22) * 1000 + min(sharp, 900) * 0.05
        if best is None or score > best["score"]:
            best = {"path": p, "time": ts, "score": score,
                    "face": [int(fx), int(fy), int(fw), int(fh)],
                    "face_ratio": round(area, 4), "sharpness": round(sharp, 1)}
    if best is None:                      # fallback: middle frame, no face found
        p = work / "cand_mid.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{dur/2:.2f}",
                        "-i", str(clip), "-frames:v", "1", "-q:v", "2", str(p)],
                       check=True, capture_output=True)
        best = {"path": p, "time": dur / 2, "score": 0, "face": None,
                "face_ratio": 0.0, "sharpness": 0.0}
    best["frames_sampled"] = n
    best["frames_with_face"] = considered
    return best


NO_LINE_END = "「『（(《【"


def layout_title(title, usable_w, H):
    """Fit the cover title on one line if it can, else split it into two
    balanced lines. Never orphans a couple of characters onto their own line
    and never breaks just after an opening bracket."""
    hi, lo = int(H * 0.13), int(H * 0.062)
    for size in range(hi, lo - 1, -1):
        f = ImageFont.truetype(ZH_BOLD_FILE, size)
        if f.getlength(title) <= usable_w:
            return f, [title], size

    n = len(title)
    for size in range(hi, lo - 1, -1):
        f = ImageFont.truetype(ZH_BOLD_FILE, size)
        best = None
        for i in range(3, n - 2):            # keep >=3 chars on either line
            if title[i] in NO_LINE_START or title[i - 1] in NO_LINE_END:
                continue
            a, b = title[:i], title[i:]
            wa, wb = f.getlength(a), f.getlength(b)
            if wa <= usable_w and wb <= usable_w:
                diff = abs(wa - wb)
                if best is None or diff < best[0]:
                    best = (diff, [a, b])
        if best:
            return f, best[1], size

    f = ImageFont.truetype(ZH_BOLD_FILE, lo)
    return f, [title[:n // 2], title[n // 2:]], lo


def render_cover(frame_path, title, out_path, face, force_band=None):
    img = Image.open(frame_path).convert("RGB")
    W, H = img.size
    pad = int(W * 0.055)
    usable = W - 2 * pad

    # Put the title in whichever horizontal band the face is NOT in.
    top_band = True
    if force_band:
        top_band = force_band == "top"
    elif face:
        fy, fh = face[1], face[3]
        top_band = (fy + fh / 2) > H * 0.5

    font, lines, size = layout_title(title, usable, H)
    lh = int(size * 1.24)
    block_h = lh * len(lines)

    band_h = block_h + int(pad * 1.5)
    y0 = 0 if top_band else H - band_h
    # contrast band behind the text
    band = Image.new("RGBA", (W, band_h), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    for i in range(band_h):
        a = int(205 * (1 - i / band_h) if top_band else 205 * (i / band_h))
        bd.line([(0, i), (W, i)], fill=(0, 0, 0, a))
    img = img.convert("RGBA")
    img.alpha_composite(band, (0, y0))
    d = ImageDraw.Draw(img)

    ty = y0 + (band_h - block_h) // 2
    for ln in lines:
        tw = font.getlength(ln)
        x = (W - tw) / 2
        for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2)):
            d.text((x + dx, ty + dy), ln, font=font, fill=(0, 0, 0))
        d.text((x, ty), ln, font=font, fill=(255, 255, 255))
        ty += lh
    img.convert("RGB").save(out_path, quality=92)
    return {"font_px": size, "lines": lines, "band": "top" if top_band else "bottom",
            "max_line_px": int(max(font.getlength(l) for l in lines)),
            "usable_px": usable}


# ── build ─────────────────────────────────────────────────────────────────────

def cmd_build(a):
    spec = json.loads(Path(a.spec).read_text())
    name = spec["name"]
    out = Path(os.environ.get("OUT_ROOT", "/home/user/workspace/deliver")) / name
    work = out / "_work"
    frames = out / "frames"
    for d in (work, frames):
        d.mkdir(parents=True, exist_ok=True)

    src = spec["source_video"]
    s, e = spec["clip_start_sec"], spec["clip_end_sec"]
    clip = work / "clip.mp4"
    crop = spec.get("crop")
    print(f"[cut] {src} {s:.2f}~{e:.2f} crop={crop}")
    vf = ["-vf", f"crop={crop}"] if crop else []
    sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{s:.3f}", "-i", src,
        "-t", f"{e - s:.3f}", *vf, "-c:v", "libx264", "-crf", "18",
        "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", str(clip)])

    w, h, dur = probe(clip)
    ass = work / "subs.ass"
    stats = build_ass(spec["cues"], w, h, ass)
    print(f"[ass] {stats}")

    final = out / "final.mp4"
    print("[burn] rendering subtitles")
    sh(["ffmpeg", "-v", "error", "-y", "-i", str(clip),
        "-vf", f"ass={ass}", "-c:v", "libx264", "-crf", "18",
        "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", str(final)])

    title = normalize_cjk_punctuation(spec["title"])
    cov = pick_cover_frame(clip, work, force_time=spec.get('cover_time_sec'))
    cinfo = render_cover(cov["path"], title, out / "cover.jpg", cov["face"],
                         spec.get("cover_band"))
    print(f"[cover] t={cov['time']:.1f}s {cinfo}")

    fw, fh, fdur = probe(final)
    for label, ts in (("head", 1.2), ("mid", fdur / 2), ("tail", fdur - 1.2)):
        sh(["ffmpeg", "-v", "error", "-y", "-ss", f"{ts:.2f}", "-i", str(final),
            "-frames:v", "1", "-q:v", "2", str(frames / f"{label}.jpg")])
    shutil.copy(out / "cover.jpg", frames / "cover.jpg")

    meta = {
        "name": name,
        "title": title,
        "description": normalize_cjk_punctuation(spec["description"]),
        "tags": spec["tags"],
        "speaker": spec["speaker"],
        "source_video": src,
        "source_window": {
            "start_sec": round(s, 2), "end_sec": round(e, 2),
            "start_hms": spec.get("start_hms"), "end_hms": spec.get("end_hms"),
            "note": spec.get("window_note", ""),
        },
        "duration_sec": round(fdur, 2),
        "resolution": f"{fw}x{fh}",
        "subtitle": {
            "mode": "bilingual", "cues": len(spec["cues"]),
            "zh_font": ZH_FONT_NAME, "en_font": EN_FONT_NAME,
            "side_margin_px": SIDE_MARGIN, "bottom_margin_px": BOTTOM_MARGIN,
            **stats,
        },
        "quotes": spec.get("quotes", []),
        "cover": {
            "frame_time_sec": round(cov["time"], 2),
            "frames_sampled": cov["frames_sampled"],
            "frames_with_face": cov["frames_with_face"],
            "face_ratio": cov["face_ratio"],
            "manual_pick": cov.get("manual", False),
            "manual_reason": spec.get("cover_note", ""),
            "title_lines": cinfo["lines"],
            "title_font_px": cinfo["font_px"],
            "title_max_line_px": cinfo["max_line_px"],
            "title_usable_px": cinfo["usable_px"],
        },
        "provenance": spec["provenance"],
    }
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"[done] {final} ({fdur:.1f}s)")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cues")
    c.add_argument("--srt", required=True)
    c.add_argument("--start", type=float, required=True)
    c.add_argument("--end", type=float, required=True)
    c.add_argument("--lang", choices=["zh", "en"], required=True)
    c.add_argument("--out", required=True)
    c.set_defaults(func=cmd_cues)
    b = sub.add_parser("build")
    b.add_argument("--spec", required=True)
    b.set_defaults(func=cmd_build)
    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
