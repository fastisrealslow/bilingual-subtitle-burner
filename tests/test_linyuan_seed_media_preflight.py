import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linyuan"))
from seed_media_preflight import select_candidates, summarize


def row(seed, page, title="访谈", duration=1200):
    return {"id": f"bilibili_search:{seed}:p{page}", "bvid": seed,
            "page": page, "title": title, "duration_seconds": duration,
            "url": f"https://www.bilibili.com/video/{seed}?p={page}",
            "status": "new_metadata_candidate", "direct_dispatch": False,
            "media_verified": False}


def test_plan_is_diverse_and_never_admits_metadata(tmp_path):
    report = {"candidates": [
        row("BV1111111111", 1, "林园访谈", 1500),
        row("BV1111111111", 2, "普通片段", 200),
        row("BV2222222222", 1, "投资者交流会", 1400),
        row("BV2222222222", 2, "演讲", 1600),
        row("BV3333333333", 1, "论坛", 1300),
    ]}
    selected = select_candidates(report, limit=4, per_seed=2)
    assert len(selected) == 4
    assert len({r["bvid"] for r in selected[:3]}) == 3
    assert all(r["slug"].startswith("seed-preflight-") for r in selected)
    assert all(not r["direct_dispatch"] and not r["media_verified"] for r in selected)


def test_summary_keeps_source_pass_separate_from_finished_clip(tmp_path):
    for number, status in enumerate(("source_quality_passed", "source_quality_rejected")):
        folder = tmp_path / str(number)
        folder.mkdir()
        (folder / "preflight-result.json").write_text(json.dumps({
            "id": number, "status": status}), encoding="utf-8")
    result = summarize(tmp_path)
    assert result["tested"] == 2
    assert result["source_quality_passed"] == 1
    assert result["accepted_mp4_count"] == 0
    assert result["production_dispatch"] is False
