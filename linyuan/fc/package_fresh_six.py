"""Repackage only reviewed files; retain original production metadata and hashes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import index as fc


def main():
    approved = fc.FRESH_SIX_APPROVED
    if not 1 <= len(approved) <= 6:
        raise SystemExit("Expected 1..6 individually reviewed MP4s")
    root = Path("fresh-six-package")
    root.mkdir(exist_ok=True)
    by_slug = {}
    for sha, item in approved.items():
        by_slug.setdefault(item["slug"], []).append((sha, item))
    artifact_names = []
    for slug, rows in sorted(by_slug.items()):
        rows.sort(key=lambda row: row[1]["part_index"])
        if [r[1]["part_index"] for r in rows] != list(range(len(rows))):
            raise SystemExit("Reviewed part indices must be contiguous")
        runs = {r[1]["run_id"] for r in rows}
        if len(runs) != 1:
            raise SystemExit("A slug must use one immutable reviewed production run")
        source = root / ("source-"+slug)
        subprocess.run(["gh", "run", "download", str(next(iter(runs))),
            "--repo", fc.REPO, "--name", "deliver-"+slug, "--dir", str(source)], check=True)
        original = json.loads((source/"meta.json").read_text())
        original = original if isinstance(original, list) else [original]
        by_final = {m["final"]: m for m in original}
        selected = []
        for sha, item in rows:
            meta = by_final[item["final"]]
            video = source/item["final"]
            if (meta.get("title") != item["title"] or meta.get('render_mode') != item['render_mode']
                    or (meta.get("fingerprints") or {}).get("sha256") != sha):
                raise SystemExit("Reviewed metadata changed")
            error = fc.artifact_quality_error(meta) or fc.fresh_six_review_error(meta, video, slug, item["source_url"])
            if error:
                raise SystemExit(error)
            selected.append(meta)
        for sha, item in rows:
            folder = root/item["artifact_name"]
            folder.mkdir(exist_ok=True)
            (folder/"meta.json").write_text(json.dumps(selected, ensure_ascii=False, indent=2))
            for name in (item["final"], item["cover"]):
                if Path(name).name != name:
                    raise SystemExit("Unsafe artifact filename")
                shutil.copy2(source/name, folder/name)
            artifact_names.append(item["artifact_name"])
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        for i, name in enumerate(artifact_names, 1):
            output.write(f"part{i}={name}\n")
    print(json.dumps({"reviewed_artifacts":artifact_names}, ensure_ascii=False))


if __name__ == "__main__":
    main()
