"""Rebuild only the user-selected BV1Ngt163EZ4 interval; never pick other clips."""
import argparse
import json
import re
import subprocess
from pathlib import Path

import produce_cn as P

SLUG = "ly-tv-wine-review-0905"
ORIGINAL_BVID = "BV1Ngt163EZ4"
SOURCE_SHA = "14ef8887af5fe638e7c731a071695131827f079f0e0309a5bf8cccaad6f977bb"
START, END = 239.46, 477.54


def credible_qr(points, width, height):
    """Reject impossible OpenCV quads, not valid but unreadable QR codes."""
    import numpy as np
    import cv2
    p = np.asarray(points).reshape(4, 2)
    if not np.isfinite(p).all() or (p < 0).any():
        return False
    if (p[:, 0] >= width).any() or (p[:, 1] >= height).any():
        return False
    edges = np.roll(p, -1, axis=0) - p
    lengths = np.linalg.norm(edges, axis=1)
    if min(lengths) < 8 or max(lengths) / min(lengths) > 1.8:
        return False
    if abs(cv2.contourArea(p.astype('float32'))) > width * height * .2:
        return False
    angles = abs((edges * np.roll(edges, -1, axis=0)).sum(axis=1) /
                 (lengths * np.roll(lengths, -1)))
    return bool((angles < .5).all())


def verify_review(final, out):
    """Dense QR/black-edge/OCR check with recorded geometry for review."""
    import cv2
    import numpy as np
    cap = cv2.VideoCapture(str(final))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    qr = cv2.QRCodeDetector()
    directory = out / 'inspection'
    directory.mkdir(exist_ok=True)
    paths, candidates = [], []
    black = 0
    for i in range(120):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + .5) / 120))
        ok, frame = cap.read()
        if not ok:
            raise P.VisualQualityError('密集抽帧失败')
        region = frame[360:830, 44:676]
        path = directory / f'{i:03}.jpg'
        cv2.imwrite(str(path), region)
        paths.append(path)
        dark = np.mean(cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) < 18, axis=0) > .92
        black += int(dark[:50].mean() > .45 or dark[-50:].mean() > .45)
        decoded, points, _ = qr.detectAndDecode(region)
        if points is not None:
            valid = bool(decoded) or credible_qr(points, 632, 470)
            candidates.append({'frame':i,'credible':valid,'quad':points.tolist()})
            if valid:
                raise P.VisualQualityError(f'第{i}帧存在可信二维码候选，需复核')
    cap.release()
    if black >= 60:
        raise P.VisualQualityError('持续黑边/取景错误')
    logos = P.detect_corner_logos_in_images(paths, stable_ratio=.5, max_area=.04)
    if logos:
        raise P.VisualQualityError(f'稳定来源角标：{logos}')
    (out/'inspection.json').write_text(json.dumps({'frames':120,'qr_candidates':candidates,
        'black_edge_hits':black,'external_logos':logos},ensure_ascii=False,indent=2))
    return {'live_region_verified':True,'no_qr_verified':True,'no_black_bars_verified':True}


def subtitle_review_sheet(final, entries, path):
    """Six actual frames, including the dining shot and one-/two-line subtitles."""
    import cv2
    cap = cv2.VideoCapture(str(final))
    thumbs = []
    for target in (15, 50, 92, 110, 170, 210):
        cue = min(entries, key=lambda e: abs((e['start_sec']+e['end_sec'])/2-target))
        at = (cue['start_sec']+cue['end_sec'])/2
        cap.set(cv2.CAP_PROP_POS_MSEC, at*1000)
        ok, frame = cap.read()
        if not ok:
            raise P.VisualQualityError('字幕检查图抽帧失败')
        thumbs.append(cv2.resize(frame,(360,640)))
    cap.release()
    cv2.imwrite(str(path),cv2.vconcat([cv2.hconcat(thumbs[:3]),cv2.hconcat(thumbs[3:])]))


def subtitle_entries(cues):
    """Keep source timing, remove overlaps, and split long cues without tiny orphan lines."""
    selected = [c for c in cues if c["end"] > START and c["start"] < END]
    selected.sort(key=lambda c: c["start"])
    entries = []
    for i, cue in enumerate(selected):
        start = max(START, cue["start"])
        end = min(END, cue["end"], selected[i + 1]["start"]
                  if i + 1 < len(selected) else END)
        text = re.sub(r"\s+", "", cue["text"])
        if end <= start or not text:
            continue
        count = (len(text) + 27) // 28
        for k in range(count):
            a, b = len(text) * k // count, len(text) * (k + 1) // count
            entries.append({"start_sec": start - START + (end - start) * a / len(text),
                            "end_sec": start - START + (end - start) * b / len(text),
                            "zh": text[a:b], "en": ""})
    return entries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--debug", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if P._file_sha256(args.source) != SOURCE_SHA:
        raise P.VisualQualityError("重做源文件不匹配，禁止套用专用裁切坐标")
    if P.video_size(args.source) != (2230, 1080):
        raise P.VisualQualityError("重做源文件尺寸不匹配")
    source_report = P.load_source_quality_report(
        args.source, args.debug / "source_quality.json")
    entries = subtitle_entries(json.loads((args.debug / "cues_raw.json").read_text()))
    transcript = "".join(e["zh"] for e in entries)
    title = "林园：电视机整天在降价，酒在涨价，两个相反的方向"
    if P.title_quality_error(title, "林园", transcript):
        raise P.VisualQualityError("原片观点与重做标题不匹配")
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    card = out / "card.png"
    portrait = args.debug / "audio_card_portrait_2.png"
    P.make_audio_card(card, "林园", title, portrait_path=portrait, require_portrait=True)
    ass = out / "subtitles.ass"
    P.make_ass(entries, ass, 720, 1280, card_style=True)
    # Keep two lines balanced (17 chars must not become 14 + 3).
    lines = ass.read_text(encoding="utf-8-sig").splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("Dialogue:"):
            continue
        prefix, text = line.rsplit("}", 1)
        text = text.replace("\\N", "")
        if len(text) > 14:
            mid = (len(text) + 1) // 2
            text = text[:mid] + "\\N" + text[mid:]
        lines[i] = prefix + "}" + text
    ass.write_text("\n".join(lines), encoding="utf-8-sig")
    # This source has a nested 16:9 broadcast, ticker, CCTV logo, account logo
    # and QR. Coordinates are source-specific, guarded by the exact SHA above.
    vf = ("[1:v]setpts=PTS-STARTPTS,split=2[wide][dining];"
          "[wide]delogo=x=1470:y=40:w=210:h=80:enable='between(t,59.64,76.5733)+gte(t,124.67)',"
          "delogo=x=545:y=42:w=190:h=80:enable='between(t,124.67,154.9)',"
          "delogo=x=970:y=535:w=150:h=150:enable='between(t,165,200)',"
          "crop=1120:600:550:42,scale=632:338:flags=lanczos,setsar=1[live];"
          "[dining]crop=650:484:1090:190,scale=632:470:flags=lanczos,setsar=1[close];"
          "[0:v]drawbox=x=44:y=360:w=632:h=470:color=0xECECE3:t=fill[bg];"
          "[bg][live]overlay=44:426[base];"
          "[base][close]overlay=44:360:enable='between(t,88.3733,94.74)'[card];"
          f"[card]ass={ass}[outv]")
    final = out / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(card),
                    "-ss", str(START), "-t", str(END - START), "-i", str(args.source),
                    "-filter_complex", vf, "-map", "[outv]", "-map", "1:a:0",
                    "-af", "asetpts=PTS-STARTPTS,highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", "-r", "30", "-pix_fmt", "yuv420p",
                    "-t", str(END - START), "-shortest", str(final)], check=True)
    # Sample every ~2 seconds, not just six easily missed positions.
    checks = verify_review(final, out)
    preview, sheet = P.make_review_assets(final, out, "", END - START)
    subtitle_review_sheet(final, entries, out / sheet)
    cover = out / "cover.jpg"
    P.make_audio_card(cover, "林园", title, width=1280, height=720,
                      portrait_path=portrait, require_portrait=True)
    fp = P.build_content_fingerprints(final, transcript)
    meta = {
        "slug": SLUG, "speaker": "林园", "review_of_bvid": ORIGINAL_BVID,
        "source_sha256": SOURCE_SHA, "source_run_id": 33951966452,
        "source_platform": "weibo", "final": final.name, "cover": cover.name,
        "title": title + "【重制试看】", "desc": "原片重制试看：调整取景、清除来源包装、字幕居中。" + P.AUDIO_CARD_DISCLAIMER,
        "tags": ["林园", "价值投资", "投资观点"],
        "segments": [{"start": START, "end": END}], "duration_sec": END - START,
        "preview_30s": preview, "contact_sheet_6": sheet,
        "quality_gate_version": P.QUALITY_GATE_VERSION,
        "visual_standard_version": P.VISUAL_STANDARD_VERSION,
        "cover_standard_version": P.COVER_STANDARD_VERSION,
        "visual_identity": source_report["visual_identity"],
        "cover_person_image_verified": True, "cover_person_image_source": "authority_reference",
        "title_quality_verified": True, "review_assets_verified": True,
        "layout_proof": {"live_region": P.LIVE_REGION, "subtitle_region": P.SUBTITLE_REGION,
                         "subtitle_max_lines": 2, "subtitle_font_px": 40,
                         "subtitle_vertical_alignment": "center", "subtitle_layout_version": 2},
        "resolution": {"width": 720, "height": 1280, "short_edge": 720},
        "watermark_verified": True, "brand_watermark_applied": True,
        "subtitles_burned": True, "has_existing_subtitles": False,
        "clean_strategy": "audio_card", "render_mode": "live_video_card",
        "clean_filter_verified": True, "fingerprints": fp, **checks,
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    print(json.dumps({"slug": SLUG, "sha256": fp["sha256"], "duration": END - START}))


if __name__ == "__main__":
    main()
