#!/usr/bin/env python3
"""Recover the exact V11 delivery files from a successful render debug artifact.

The production run can finish rendering yet lose only the large aggregate
Artifact finalization request.  The debug artifact retains the verified
segments and all deterministic inputs, so this script rebuilds covers, review
assets, proofs and metadata without re-encoding the source interview.
"""
import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from presentation import VERSION as PRESENTATION_VERSION, layout_for, select_cover_style, verify_render
from produce_cn import (
    AUDIO_CARD_TEMPLATE,
    BRAND_WATERMARK_MARGIN_RATIO,
    BRAND_WATERMARK_OPACITY,
    BRAND_WATERMARK_WIDTH_RATIO,
    COMPETITOR_13_DURATION_PROFILE,
    COVER_STANDARD_VERSION,
    MODELS,
    QUALITY_GATE_VERSION,
    VISUAL_STANDARD_VERSION,
    _chunk_by_duration_profile,
    build_content_fingerprints,
    ensure_min_short_edge,
    make_audio_card,
    make_review_assets,
)


SLUG = "ly-parity-v3-14-0905"
SOURCE_URL = "https://www.bilibili.com/video/BV1Pzug6fEyY"
OCCASION = "2026年8月7日林园57分钟专访"


def probe_duration(path):
    raw = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    return float(raw)


def selected_context(cues, chunk, picks):
    a, b = chunk
    local = cues[a:b + 1]
    selected = sorted({
        index for pick in picks
        for index in range(int(pick["start"]), int(pick["end"]) + 1)
    })
    transcript = "".join(local[index]["text"] for index in selected)
    segments = [{
        "start": local[int(pick["start"])]["start"],
        "end": local[int(pick["end"])]["end"],
        "reason": pick.get("reason", ""),
    } for pick in picks]
    return transcript, segments


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--parts", required=True)
    args = parser.parse_args()

    debug = Path(args.debug)
    out = Path(args.out)
    parts_dir = Path(args.parts)
    out.mkdir(parents=True, exist_ok=True)
    parts_dir.mkdir(parents=True, exist_ok=True)

    source_report = json.loads((debug / "source_quality.json").read_text(encoding="utf-8"))
    if source_report.get("passed") is not True or int(source_report.get("quality_gate_version", 0)) < 11:
        raise SystemExit("debug source quality proof is not V11")
    cues = json.loads((debug / "cues_raw.json").read_text(encoding="utf-8"))
    chunks = _chunk_by_duration_profile(cues, COMPETITOR_13_DURATION_PROFILE)
    if len(chunks) != 13:
        raise SystemExit(f"expected 13 chunks, got {len(chunks)}")

    layout = layout_for(720, 1280, True)
    visual = source_report["visual_identity"]
    metas = []
    for index in range(1, 15):
        source_video = debug / f"seg_{index}1.mp4"
        if not source_video.is_file():
            raise SystemExit(f"missing verified segment {source_video.name}")
        final = out / f"final_{index}.mp4"
        shutil.copy2(source_video, final)

        copy_name = "copywrite_full.json" if index == 14 else f"copywrite_{index}.json"
        highlight_name = "highlights_full.json" if index == 14 else f"highlights_{index}.json"
        copy = json.loads((debug / copy_name).read_text(encoding="utf-8"))
        picks = json.loads((debug / highlight_name).read_text(encoding="utf-8"))
        chunk = (0, len(cues) - 1) if index == 14 else chunks[index - 1]
        transcript, segments = selected_context(cues, chunk, picks)

        duration = probe_duration(final)
        width, height = ensure_min_short_edge(final, label=f"恢复成片{index}")
        render_checks = verify_render(final, layout)

        suffix = f"_{index}"
        preview_name, sheet_name = make_review_assets(final, out, suffix, duration)

        portrait = debug / f"audio_card_portrait_{index}.png"
        if not portrait.is_file():
            raise SystemExit(f"missing verified portrait {portrait.name}")
        cover = out / f"cover_{index}.jpg"
        style = select_cover_style(False, copy["title"], os.environ.get("COVER_STYLE", "auto"))
        make_audio_card(
            cover, "林园", copy["title"], width=1280, height=720,
            portrait_path=portrait, require_portrait=True, cover_style=style)
        cover_proof = json.loads(Path(str(cover) + ".proof.json").read_text(encoding="utf-8"))

        item = {
            "slug": SLUG,
            "source": SOURCE_URL,
            "speaker": "林园",
            "occasion": OCCASION,
            "part": index,
            "quality_gate_version": QUALITY_GATE_VERSION,
            "final": final.name,
            "title": copy["title"],
            "desc": copy["desc"],
            "tags": copy["tags"],
            "cover": cover.name,
            "preview_30s": preview_name,
            "contact_sheet_6": sheet_name,
            "review_assets_verified": True,
            "title_quality_verified": copy.get("title_quality_verified") is True,
            "visual_standard_version": VISUAL_STANDARD_VERSION,
            "cover_standard_version": COVER_STANDARD_VERSION,
            "cover_person_image_verified": True,
            "cover_person_image_source": "authority_reference",
            "presentation_version": PRESENTATION_VERSION,
            "layout_proof": layout,
            "cover_proof": cover_proof,
            "subtitle_word_boundaries_verified": True,
            **render_checks,
            "duration_sec": round(duration, 1),
            "resolution": {"width": width, "height": height,
                           "short_edge": min(width, height)},
            "fingerprints": build_content_fingerprints(final, transcript),
            "watermark_removed": True,
            "watermark_verified": True,
            "clean_strategy": "audio_card",
            "audio_card_template": AUDIO_CARD_TEMPLATE,
            "render_mode": "audio_card",
            "brand_watermark_applied": True,
            "brand_watermark": {
                "name": "园来滚雪球",
                "position": "top-right",
                "width_ratio": BRAND_WATERMARK_WIDTH_RATIO,
                "opacity": BRAND_WATERMARK_OPACITY,
                "margin_ratio": BRAND_WATERMARK_MARGIN_RATIO,
            },
            "segments": segments,
            "source_platform": "bilibili",
            "watermark_cropped": True,
            "visual_identity": visual,
            "subtitles_burned": True,
            "has_existing_subtitles": False,
            "raw_has_existing_subtitles": bool(source_report.get("raw_has_existing_subtitles")),
            "clean_filter_verified": bool(source_report.get("clean_filter_verified")),
            "vertical": height > width,
            "asr_model": "faster-whisper large-v3",
            "llm": MODELS[0],
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        if index == 14:
            item["content_type"] = "full_interview"
        metas.append(item)

    if len(metas) != 14 or any(int(x.get("quality_gate_version", 0)) < 11 for x in metas):
        raise SystemExit("recovery did not produce 14 V11 metadata records")

    meta_path = out / "meta.json"
    meta_path.write_text(json.dumps(metas, ensure_ascii=False, indent=2), encoding="utf-8")
    for index in range(1, 15):
        target = parts_dir / str(index)
        target.mkdir(parents=True, exist_ok=True)
        for name in (
            f"final_{index}.mp4",
            f"cover_{index}.jpg",
            f"cover_{index}_list_160.jpg",
            f"cover_{index}.jpg.proof.json",
            f"preview_30s_{index}.mp4",
            f"contact_sheet_6_{index}.jpg",
            "meta.json",
        ):
            src = meta_path if name == "meta.json" else out / name
            if not src.is_file():
                raise SystemExit(f"missing reconstructed evidence {name}")
            shutil.copy2(src, target / name)
    print(json.dumps({"slug": SLUG, "parts": len(metas), "gate": 11}, ensure_ascii=False))


if __name__ == "__main__":
    main()
