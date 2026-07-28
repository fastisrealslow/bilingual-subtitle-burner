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


def queue(slug, speaker, episodes, generated_at="2026-07-27T12:00:00Z"):
    return {"schema": 1, "slug": slug, "speaker": speaker,
            "release_tag": f"clips-{slug}", "generated_at": generated_at,
            "episodes": episodes}


def indexed(slug, episodes, batch_at="2026-07-27T12:00:00Z", speaker="谁"):
    """索引里已有的一批（merge 的输出形状）。"""
    return [{**ep, "slug": slug, "speaker": speaker, "batch_at": batch_at,
             "release_tag": f"clips-{slug}"} for ep in episodes]


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
    merged, _ = USI.merge(
        old, queue("munger", "查理·芒格",
                   [episode("ep01", "2026-07-30", "新标题")]), FIXED_NOW)

    assert len(merged["episodes"]) == 1
    assert merged["episodes"][0]["title"] == "新标题"
    assert merged["episodes"][0]["scheduled_date"] == "2026-07-30"


def test_same_id_from_a_different_slug_is_a_separate_entry():
    old = {"schema": 1, "updated_at": "x", "episodes": [
        {**episode("ep01", "2026-07-28"), "slug": "dalio", "speaker": "达利欧"}]}
    merged, _ = USI.merge(
        old, queue("munger", "查理·芒格", [episode("ep01", "2026-07-29")]),
        FIXED_NOW)

    assert len(merged["episodes"]) == 2
    assert {e["slug"] for e in merged["episodes"]} == {"dalio", "munger"}


def test_entries_are_sorted_by_scheduled_date():
    old = {"schema": 1, "updated_at": "x", "episodes": [
        {**episode("ep01", "2026-08-05"), "slug": "dalio", "speaker": "达利欧"}]}
    merged, _ = USI.merge(
        old, queue("munger", "查理·芒格", [episode("ep01", "2026-07-28"),
                                        episode("ep02", "2026-08-20")]),
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
    merged, _ = USI.merge(
        {"schema": 1, "updated_at": "2020-01-01T00:00:00Z", "episodes": []},
        queue("s", "谁", [episode("ep01", "2026-07-28")]), FIXED_NOW)
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


# ── 批次保留窗口 ────────────────────────────────────────────────────────────
#
# 同一段素材重跑会换一个 slug，两批内容重复只是封面排版不同。索引只留最近
# KEEP_BATCHES 批，更早的整批下架，避免页面上堆出重复条目。

def batches(*specs):
    """按 (slug, batch_at, 集数) 拼出一份索引里的 episodes。"""
    eps = []
    for slug, batch_at, count in specs:
        eps += indexed(slug, [episode(f"ep{i:02d}", f"2026-08-{i:02d}")
                              for i in range(1, count + 1)], batch_at)
    return eps


def slugs_of(eps):
    return [e["slug"] for e in eps]


def test_default_keeps_three_batches():
    assert USI.KEEP_BATCHES == 3


def test_exactly_keep_batches_drops_nothing():
    eps = batches(("a", "2026-07-01T00:00:00Z", 2),
                  ("b", "2026-07-02T00:00:00Z", 2),
                  ("c", "2026-07-03T00:00:00Z", 2))
    kept, dropped = USI.prune_batches(eps, keep=3)

    assert dropped == []
    assert kept == eps


def test_one_batch_over_the_window_drops_only_the_oldest():
    eps = batches(("a", "2026-07-01T00:00:00Z", 2),
                  ("b", "2026-07-02T00:00:00Z", 2),
                  ("c", "2026-07-03T00:00:00Z", 2),
                  ("d", "2026-07-04T00:00:00Z", 2))
    kept, dropped = USI.prune_batches(eps, keep=3)

    assert dropped == ["a"]
    assert set(slugs_of(kept)) == {"b", "c", "d"}


def test_keep_one_leaves_only_the_newest_batch():
    eps = batches(("a", "2026-07-01T00:00:00Z", 2),
                  ("b", "2026-07-02T00:00:00Z", 2))
    kept, dropped = USI.prune_batches(eps, keep=1)

    assert dropped == ["a"]
    assert set(slugs_of(kept)) == {"b"}


def test_a_batch_is_never_split():
    """下架是整批的事：走的批一集不剩，留的批一集不少。"""
    eps = batches(("a", "2026-07-01T00:00:00Z", 3),
                  ("b", "2026-07-02T00:00:00Z", 4),
                  ("c", "2026-07-03T00:00:00Z", 2),
                  ("d", "2026-07-04T00:00:00Z", 5))
    kept, dropped = USI.prune_batches(eps, keep=3)

    assert dropped == ["a"]
    assert slugs_of(kept).count("a") == 0
    assert slugs_of(kept).count("b") == 4
    assert slugs_of(kept).count("c") == 2
    assert slugs_of(kept).count("d") == 5


def test_recency_follows_batch_at_not_position():
    """索引按 scheduled_date 排序，批次的先后跟它在数组里的位置无关。"""
    eps = batches(("new", "2026-07-09T00:00:00Z", 1),
                  ("old", "2026-07-01T00:00:00Z", 1),
                  ("mid", "2026-07-05T00:00:00Z", 1))
    kept, dropped = USI.prune_batches(eps, keep=2)

    assert dropped == ["old"]
    assert set(slugs_of(kept)) == {"mid", "new"}


def test_a_partially_rerun_batch_counts_as_its_newest_merge():
    """只补了半批时，整批按最近一次合并算新，不会被误判成旧批带走。"""
    eps = (indexed("a", [episode("ep01", "2026-08-01")], "2026-07-01T00:00:00Z")
           + indexed("a", [episode("ep02", "2026-08-02")],
                     "2026-07-09T00:00:00Z")
           + batches(("b", "2026-07-05T00:00:00Z", 1),
                     ("c", "2026-07-06T00:00:00Z", 1)))
    kept, dropped = USI.prune_batches(eps, keep=2)

    assert dropped == ["b"]
    assert slugs_of(kept).count("a") == 2


def test_legacy_entries_without_batch_at_sort_oldest():
    """本次改动之前写进索引的记录没有 batch_at，排在最老、最先下架。"""
    eps = (indexed("legacy", [episode("ep01", "2026-08-01")], batch_at=None)
           + batches(("b", "2026-07-05T00:00:00Z", 1),
                     ("c", "2026-07-06T00:00:00Z", 1)))
    kept, dropped = USI.prune_batches(eps, keep=2)

    assert dropped == ["legacy"]
    assert set(slugs_of(kept)) == {"b", "c"}


def test_merge_stamps_batch_at_and_release_tag():
    merged, _ = USI.merge({"schema": 1, "updated_at": "x", "episodes": []},
                          queue("munger", "芒格",
                                [episode("ep01", "2026-07-28")],
                                generated_at="2026-07-27T09:30:00Z"),
                          FIXED_NOW)

    ep = merged["episodes"][0]
    assert ep["batch_at"] == "2026-07-27T09:30:00Z"
    assert ep["release_tag"] == "clips-munger"


def test_merge_drops_the_oldest_batch_and_reports_it():
    old = {"schema": 1, "updated_at": "x",
           "episodes": batches(("a", "2026-07-01T00:00:00Z", 2),
                               ("b", "2026-07-02T00:00:00Z", 2),
                               ("c", "2026-07-03T00:00:00Z", 2))}
    merged, dropped = USI.merge(
        old, queue("d", "谁", [episode("ep01", "2026-09-01")],
                   generated_at="2026-07-04T00:00:00Z"), FIXED_NOW)

    assert dropped == ["a"]
    assert set(slugs_of(merged["episodes"])) == {"b", "c", "d"}


def test_rerunning_a_slug_refreshes_it_instead_of_adding_a_batch():
    """重跑同一个 slug 只是更新那一批，不占掉新的保留名额。"""
    old = {"schema": 1, "updated_at": "x",
           "episodes": batches(("a", "2026-07-01T00:00:00Z", 1),
                               ("b", "2026-07-02T00:00:00Z", 1),
                               ("c", "2026-07-03T00:00:00Z", 1))}
    merged, dropped = USI.merge(
        old, queue("a", "谁", [episode("ep01", "2026-08-01")],
                   generated_at="2026-07-04T00:00:00Z"), FIXED_NOW)

    assert dropped == []
    assert set(slugs_of(merged["episodes"])) == {"a", "b", "c"}


def test_keep_batches_is_configurable_from_the_cli(tmp_path):
    index = tmp_path / "index.json"
    USI.write_index(index, {"schema": 1, "updated_at": "x",
                            "episodes": batches(
                                ("a", "2026-07-01T00:00:00Z", 2),
                                ("b", "2026-07-02T00:00:00Z", 2))})
    q = write_queue(tmp_path, queue("c", "谁", [episode("ep01", "2026-09-01")],
                                    generated_at="2026-07-03T00:00:00Z"))

    assert USI.main(["--queue", str(q), "--index", str(index),
                     "--keep-batches", "1"]) == 0
    assert {e["slug"] for e in read(index)["episodes"]} == {"c"}


def test_the_cli_logs_which_batch_was_dropped(tmp_path, capsys):
    index = tmp_path / "index.json"
    USI.write_index(index, {"schema": 1, "updated_at": "x",
                            "episodes": batches(
                                ("a", "2026-07-01T00:00:00Z", 2))})
    q = write_queue(tmp_path, queue("b", "谁", [episode("ep01", "2026-09-01")],
                                    generated_at="2026-07-03T00:00:00Z"))
    USI.main(["--queue", str(q), "--index", str(index), "--keep-batches", "1"])

    out = capsys.readouterr().out
    assert "下架旧批次 a" in out
    assert "clips-a" in out


# ── 预期外的状态要炸，不要静默 ──────────────────────────────────────────────

def test_queue_without_generated_at_is_rejected():
    q = queue("munger", "芒格", [episode("ep01", "2026-07-28")])
    del q["generated_at"]

    try:
        USI.merge({"schema": 1, "updated_at": "x", "episodes": []}, q,
                  FIXED_NOW)
    except ValueError as e:
        assert "generated_at" in str(e)
    else:
        raise AssertionError("缺 generated_at 应该报错")


def test_an_entry_without_slug_is_rejected():
    try:
        USI.prune_batches([episode("ep01", "2026-08-01")])
    except ValueError as e:
        assert "slug" in str(e)
    else:
        raise AssertionError("没有 slug 的记录应该报错")


def test_keep_below_one_is_rejected():
    eps = batches(("a", "2026-07-01T00:00:00Z", 1))
    try:
        USI.prune_batches(eps, keep=0)
    except ValueError as e:
        assert "保留批次数" in str(e)
    else:
        raise AssertionError("keep=0 会清空索引，应该报错")
