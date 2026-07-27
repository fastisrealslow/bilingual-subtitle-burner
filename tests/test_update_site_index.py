"""site/data/index.json 的合并（scripts/update_site_index.py）。

手机页只读这一个静态文件，合并错了页面上就会出现重复条目或者顺序乱掉。
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import update_site_index as USI                   # noqa: E402

FIXED_NOW = datetime(2026, 7, 27, 14, 47, 0, tzinfo=timezone.utc)


def episode(eid, date, title="标题"):
    return {"index": int(eid[2:]), "id": eid, "title": title,
            "duration_sec": 198.92, "source_start_sec": 0.0,
            "source_end_sec": 200.0, "cue_count": 47,
            "tags": ["查理·芒格"], "desc": "简介",
            "files": {"video": f"{eid}.mp4"},
            "urls": {"video": f"https://example.com/{eid}.mp4"},
            "sha256": {"video": "a" * 64}, "scheduled_date": date,
            "status": "pending", "publish": {"bilibili": None, "douyin": None}}


def queue(slug, speaker, episodes):
    return {"schema": 1, "slug": slug, "speaker": speaker,
            "release_tag": f"clips-{slug}", "episodes": episodes}


def write_queue(tmp_path, payload, name="queue.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


# ── 建表 ────────────────────────────────────────────────────────────────────

def test_creates_the_index_when_missing(tmp_path):
    q = write_queue(tmp_path, queue("munger", "查理·芒格",
                                    [episode("ep01", "2026-07-28")]))
    index = tmp_path / "site" / "data" / "index.json"

    assert USI.main(["--queue", str(q), "--index", str(index)]) == 0

    data = read(index)
    assert data["schema"] == 1
    assert data["updated_at"].endswith("Z")
    assert [e["id"] for e in data["episodes"]] == ["ep01"]


def test_every_entry_carries_slug_and_speaker(tmp_path):
    q = write_queue(tmp_path, queue("munger", "查理·芒格",
                                    [episode("ep01", "2026-07-28")]))
    index = tmp_path / "index.json"
    USI.main(["--queue", str(q), "--index", str(index)])

    ep = read(index)["episodes"][0]
    assert ep["slug"] == "munger"
    assert ep["speaker"] == "查理·芒格"
    # queue 里的字段原样带过来，页面不用再去别处取
    assert ep["urls"]["video"] == "https://example.com/ep01.mp4"


# ── 合并规则 ────────────────────────────────────────────────────────────────

def test_same_slug_and_id_is_overwritten_not_appended():
    old = {"schema": 1, "updated_at": "x", "episodes": [
        {**episode("ep01", "2026-07-28", "旧标题"), "slug": "munger",
         "speaker": "查理·芒格"}]}
    merged = USI.merge(old, queue("munger", "查理·芒格",
                                  [episode("ep01", "2026-07-30", "新标题")]),
                       FIXED_NOW)

    assert len(merged["episodes"]) == 1
    assert merged["episodes"][0]["title"] == "新标题"
    assert merged["episodes"][0]["scheduled_date"] == "2026-07-30"


def test_same_id_from_a_different_slug_is_a_separate_entry():
    old = {"schema": 1, "updated_at": "x", "episodes": [
        {**episode("ep01", "2026-07-28"), "slug": "dalio", "speaker": "达利欧"}]}
    merged = USI.merge(old, queue("munger", "查理·芒格",
                                  [episode("ep01", "2026-07-29")]), FIXED_NOW)

    assert len(merged["episodes"]) == 2
    assert {e["slug"] for e in merged["episodes"]} == {"dalio", "munger"}


def test_entries_are_sorted_by_scheduled_date():
    old = {"schema": 1, "updated_at": "x", "episodes": [
        {**episode("ep01", "2026-08-05"), "slug": "dalio", "speaker": "达利欧"}]}
    merged = USI.merge(old, queue("munger", "查理·芒格", [
        episode("ep01", "2026-07-28"), episode("ep02", "2026-08-20")]),
        FIXED_NOW)

    assert [e["scheduled_date"] for e in merged["episodes"]] == [
        "2026-07-28", "2026-08-05", "2026-08-20"]


def test_rerunning_the_same_queue_is_idempotent(tmp_path):
    q = write_queue(tmp_path, queue("munger", "查理·芒格", [
        episode("ep01", "2026-07-28"), episode("ep02", "2026-07-29")]))
    index = tmp_path / "index.json"

    USI.main(["--queue", str(q), "--index", str(index)])
    first = read(index)["episodes"]
    USI.main(["--queue", str(q), "--index", str(index)])
    second = read(index)["episodes"]

    assert first == second
    assert len(second) == 2


def test_updated_at_is_refreshed():
    merged = USI.merge({"schema": 1, "updated_at": "2020-01-01T00:00:00Z",
                        "episodes": []},
                       queue("s", "谁", [episode("ep01", "2026-07-28")]),
                       FIXED_NOW)
    assert merged["updated_at"] == "2026-07-27T14:47:00Z"


# ── 输入有误 ────────────────────────────────────────────────────────────────

def test_missing_queue_file_exits_one(tmp_path, capsys):
    assert USI.main(["--queue", str(tmp_path / "nope.json"),
                     "--index", str(tmp_path / "i.json")]) == 1
    assert "找不到" in capsys.readouterr().err


def test_queue_without_slug_exits_one(tmp_path, capsys):
    q = write_queue(tmp_path, {"schema": 1, "episodes": []})
    assert USI.main(["--queue", str(q),
                     "--index", str(tmp_path / "i.json")]) == 1
    assert "slug" in capsys.readouterr().err


def test_corrupt_index_exits_one_instead_of_clobbering(tmp_path, capsys):
    q = write_queue(tmp_path, queue("munger", "芒格",
                                    [episode("ep01", "2026-07-28")]))
    index = tmp_path / "index.json"
    index.write_text('{"schema": 1}', encoding="utf-8")

    assert USI.main(["--queue", str(q), "--index", str(index)]) == 1
    assert read(index) == {"schema": 1}      # 原文件没被覆盖
