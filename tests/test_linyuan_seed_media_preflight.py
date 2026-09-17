import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linyuan"))
from seed_media_preflight import (
    _load_exclusions,
    latest_acceptance_batch,
    select_candidates,
    summarize,
)


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


def test_previous_acceptance_manifest_is_excluded(tmp_path):
    previous = tmp_path / "seed-expansion-acceptance-previous.json"
    previous.write_text(json.dumps({"samples": [
        {"id": 201, "source_url": "https://www.bilibili.com/video/BV1111111111?p=1"},
    ]}), encoding="utf-8")
    excluded_ids, excluded_urls = _load_exclusions([previous])
    report = {"candidates": [
        row("BV1111111111", 1, "林园访谈", 1500),
        row("BV1111111111", 2, "林园访谈", 1400),
        row("BV2222222222", 1, "投资者交流会", 1300),
    ]}
    selected = select_candidates(report, limit=2, per_seed=2,
                                 excluded_ids=excluded_ids,
                                 excluded_urls=excluded_urls)
    assert {item["url"] for item in selected} == {
        "https://www.bilibili.com/video/BV2222222222?p=1",
        "https://www.bilibili.com/video/BV1111111111?p=2",
    }


def test_latest_acceptance_batch_uses_highest_sample_id(tmp_path):
    (tmp_path / "seed-expansion-acceptance-old.json").write_text(json.dumps({
        "samples": [{"id": 201}, {"id": 202}],
    }), encoding="utf-8")
    (tmp_path / "seed-expansion-acceptance-next.json").write_text(json.dumps({
        "samples": [{"id": 207}, {"id": 208}, {"id": 209}],
    }), encoding="utf-8")
    assert latest_acceptance_batch(tmp_path) == {
        "sample_ids": "207,208,209",
        "manifest_path": "simulations/seed-expansion-acceptance-next.json",
    }
