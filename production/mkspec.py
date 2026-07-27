#!/usr/bin/env python3
"""Merge authored translations + copy into a cues file to produce a build spec."""
import argparse
import json
from pathlib import Path


def hms(s):
    return f"{int(s//3600):02d}:{int(s%3600//60):02d}:{s%60:06.3f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cues", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cues = json.loads(Path(a.cues).read_text())
    d = json.loads(Path(a.data).read_text())

    src = "zh" if cues["lang"] == "zh" else "en"
    dst = "en" if src == "zh" else "zh"
    items = cues["cues"]
    tr = d["translations"]
    if len(tr) != len(items):
        raise SystemExit(f"translations={len(tr)} but cues={len(items)}")

    fixes = d.get("asr_corrections", [])
    for it, t in zip(items, tr):
        s = it[src]
        for w, r in fixes:
            s = s.replace(w, r)
        it[src] = s
        it[dst] = t

    prov = dict(d["provenance"])
    if fixes:
        prov["asr_corrections"] = [f"{w} → {r}" for w, r in fixes]

    spec = {
        "name": d["name"],
        "speaker": d["speaker"],
        "source_video": d["source_video"],
        "clip_start_sec": cues["clip_start_sec"],
        "clip_end_sec": cues["clip_end_sec"],
        "start_hms": hms(cues["clip_start_sec"]),
        "end_hms": hms(cues["clip_end_sec"]),
        "window_note": d.get("window_note", ""),
        "crop": d.get("crop"),
        "cover_time_sec": d.get("cover_time_sec"),
        "cover_band": d.get("cover_band"),
        "cover_note": d.get("cover_note", ""),
        "title": d["title"],
        "description": d["description"],
        "tags": d["tags"],
        "quotes": d.get("quotes", []),
        "cues": items,
        "provenance": prov,
    }
    Path(a.out).write_text(json.dumps(spec, ensure_ascii=False, indent=2))
    print(f"[spec] {d['name']}: {len(items)} cues -> {a.out}")


if __name__ == "__main__":
    main()
