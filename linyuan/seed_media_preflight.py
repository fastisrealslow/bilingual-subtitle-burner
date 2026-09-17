#!/usr/bin/env python3
"""Plan and summarize an isolated CPU media preflight for expansion seeds.

This module never writes the production catalog and never treats a source-gate
pass as a finished clip.  It exists to decide which metadata-only leads are
worth the much more expensive offline ASR and final acceptance pipeline.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


PREFERRED = re.compile(r"(?:采访|访谈|演讲|交流会|股东大会|投资者|论坛|峰会|课堂)")
LOW_VALUE = re.compile(r"(?:预告|花絮|片段|剪辑|纯音频|录音)")


def _score(row):
    """Prefer substantial, clearly described sources without hard admission."""
    duration = int(row.get("duration_seconds") or 0)
    title = row.get("title") or ""
    in_sweet_spot = 600 <= duration <= 2700
    return (
        bool(PREFERRED.search(title)),
        in_sweet_spot,
        not bool(LOW_VALUE.search(title)),
        -abs(duration - 1500),
        -int(row.get("page") or 0),
    )


def _load_exclusions(paths):
    """Return IDs/URLs already assigned to or screened by an immutable batch."""
    ids, urls = set(), set()
    for path in paths:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in payload.get("samples") or []:
            if row.get("id") is not None:
                ids.add(str(row["id"]))
            if row.get("source_url"):
                urls.add(row["source_url"])
        for row in payload.get("results") or []:
            if row.get("id") is not None:
                ids.add(str(row["id"]))
            if row.get("url"):
                urls.add(row["url"])
            if row.get("source_url"):
                urls.add(row["source_url"])
        for row in payload.get("screened_urls") or []:
            if isinstance(row, str):
                urls.add(row)
                continue
            if row.get("id") is not None:
                ids.add(str(row["id"]))
            if row.get("url"):
                urls.add(row["url"])
    return ids, urls


def latest_acceptance_batch(root):
    """Choose the immutable acceptance manifest with the highest sample ID."""
    choices = []
    for path in Path(root).glob("seed-expansion-acceptance-*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            ids = [int(row["id"]) for row in payload.get("samples") or []]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if ids:
            choices.append((max(ids), path.name, ids))
    if not choices:
        raise ValueError("no valid immutable seed acceptance manifest")
    _, name, ids = max(choices)
    return {"sample_ids": ",".join(str(value) for value in ids),
            "manifest_path": f"simulations/{name}"}


def select_candidates(report, limit=6, per_seed=2, excluded_ids=(), excluded_urls=()):
    """Round-robin across seed families so one collection cannot consume a run."""
    if limit < 1 or per_seed < 1:
        raise ValueError("limit and per_seed must be positive")
    excluded_ids = {str(value) for value in excluded_ids}
    excluded_urls = set(excluded_urls)
    groups = defaultdict(list)
    for row in report.get("candidates") or []:
        if row.get("status") != "new_metadata_candidate":
            continue
        if str(row.get("id")) in excluded_ids or row.get("url") in excluded_urls:
            continue
        if row.get("direct_dispatch") or row.get("media_verified"):
            raise ValueError("metadata candidate unexpectedly claims admission")
        groups[row.get("bvid") or "unknown"].append(dict(row))
    for rows in groups.values():
        rows.sort(key=_score, reverse=True)
    selected, used = [], Counter()
    while len(selected) < limit:
        choices = []
        for family, rows in groups.items():
            if rows and used[family] < per_seed:
                choices.append((used[family], _score(rows[0]), family))
        if not choices:
            break
        _, _, family = max(choices, key=lambda item: (-item[0], item[1], item[2]))
        row = groups[family].pop(0)
        used[family] += 1
        row["slug"] = "seed-preflight-" + hashlib.sha256(
            row["id"].encode("utf-8")).hexdigest()[:12]
        selected.append(row)
    return selected


def summarize(root):
    rows = []
    for path in sorted(Path(root).rglob("preflight-result.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            rows.append({"status": "technical_unknown", "report": str(path),
                         "reason": f"invalid report: {exc}"})
        else:
            rows.append(row)
    counts = Counter(row.get("status", "technical_unknown") for row in rows)
    return {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "tested": len(rows),
        "counts": dict(counts),
        "source_quality_passed": counts["source_quality_passed"],
        "accepted_mp4_count": 0,
        "production_dispatch": False,
        "caveat": ("A source-quality pass is not a finished clip. Offline ASR, "
                   "same-event/content dedup, selection, rendering and all final "
                   "acceptance gates are still required."),
        "results": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    matrix = sub.add_parser("matrix")
    matrix.add_argument("--report", type=Path, required=True)
    matrix.add_argument("--limit", type=int, default=6)
    matrix.add_argument("--per-seed", type=int, default=2)
    matrix.add_argument("--exclude-manifest", type=Path, action="append", default=[])
    batch = sub.add_parser("acceptance-batch")
    batch.add_argument("--root", type=Path, required=True)
    summary = sub.add_parser("summary")
    summary.add_argument("--reports", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "acceptance-batch":
        print(json.dumps(latest_acceptance_batch(args.root),
                         ensure_ascii=False, separators=(",", ":")))
        return 0
    if args.command == "matrix":
        report = json.loads(args.report.read_text(encoding="utf-8"))
        exclusion_paths = list(args.exclude_manifest)
        if not exclusion_paths:
            root = Path("linyuan/simulations")
            exclusion_paths = sorted(root.glob("seed-expansion-acceptance-*.json"))
            exclusion_paths += sorted(root.glob("seed-expansion-screened-*.json"))
        excluded_ids, excluded_urls = _load_exclusions(exclusion_paths)
        print(json.dumps({"include": select_candidates(
            report, args.limit, args.per_seed, excluded_ids, excluded_urls)},
            ensure_ascii=False, separators=(",", ":")))
        return 0
    result = summarize(args.reports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({k: result[k] for k in
                      ("tested", "counts", "accepted_mp4_count")}, ensure_ascii=False))
    return 0 if result["tested"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
