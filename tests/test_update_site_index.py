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


RELEASE = "https://github.com/o/r/releases/download/clips-munger"


def cover_episode(eid, date, title="标题"):
    """带 16:9 封面的一集 —— produce.py 真实产出的形状。"""
    ep = episode(eid, date, title)
    ep["files"]["cover_16x9"] = f"{eid}_cover_16x9.jpg"
    ep["urls"]["video"] = f"{RELEASE}/{eid}.mp4"
    ep["urls"]["cover_16x9"] = f"{RELEASE}/{eid}_cover_16x9.jpg"
    return ep


def make_cover(deliver_dir, eid, multi, payload=b"\xff\xd8\xff\xdbJPEG"):
    """按 release_assets 的约定落一张源封面：单集在 slug 根，多集在 ep01/ 下。"""
    src_dir = deliver_dir / eid if multi else deliver_dir
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "cover_16x9.jpg").write_bytes(payload)


def deliver(tmp_path, slug, episodes, multi=True):
    """搭一个 deliver/<slug>/ 目录，返回 queue.json 路径。"""
    d = tmp_path / "deliver" / slug
    d.mkdir(parents=True, exist_ok=True)
    for ep in episodes:
        if ep.get("files", {}).get("cover_16x9"):
            make_cover(d, ep["id"], multi)
    path = d / "queue.json"
    path.write_text(json.dumps(queue(slug, "查理·芒格", episodes),
                               ensure_ascii=False), encoding="utf-8")
    return path


def covers_in(site_root):
    root = site_root / "covers"
    return sorted(str(p.relative_to(root)) for p in root.rglob("*.jpg")) \
        if root.is_dir() else []


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


# ── 封面入站：改路径不改图 ──────────────────────────────────────────────────
# Release 资产地址会 302 到 release-assets.githubusercontent.com，还带
# Content-Disposition: attachment，大陆 iPhone 上经常加载不出来。封面改由
# 站点同源托管；视频太大，继续留在 Release。

def test_cover_lands_in_the_site_and_url_becomes_relative(tmp_path):
    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28")])
    site = tmp_path / "site"

    assert USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
                     "--site-root", str(site)]) == 0

    assert covers_in(site) == ["munger/ep01_16x9.jpg"]
    ep = read(site / "data/index.json")["episodes"][0]
    assert ep["urls"]["cover_16x9"] == "covers/munger/ep01_16x9.jpg"


def test_relative_cover_resolves_to_the_file_that_was_written(tmp_path):
    """页面拿 index.html 所在目录去解析这个相对路径，解不到就是灰块。"""
    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28"),
                                     cover_episode("ep02", "2026-07-29")])
    site = tmp_path / "site"
    USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
              "--site-root", str(site)])

    for ep in read(site / "data/index.json")["episodes"]:
        rel = ep["urls"]["cover_16x9"]
        assert not rel.startswith("http")
        assert (site / rel).is_file(), f"{rel} 在站点目录里不存在"


def test_cover_bytes_are_copied_verbatim(tmp_path):
    """这次只改「封面放在哪」，图片本身一个字节都不该变。"""
    ep = cover_episode("ep01", "2026-07-28")
    q = deliver(tmp_path, "munger", [ep])
    src = q.parent / "ep01" / "cover_16x9.jpg"
    src.write_bytes(b"\xff\xd8\xff\xdb" + bytes(range(256)) * 4)
    site = tmp_path / "site"

    USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
              "--site-root", str(site)])

    assert (site / "covers/munger/ep01_16x9.jpg").read_bytes() == src.read_bytes()


def test_single_episode_cover_comes_from_the_slug_root(tmp_path):
    """单集不分 ep01/ 子目录 —— 和 release_assets.py 共用同一条约定。"""
    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28")],
                multi=False)
    site = tmp_path / "site"

    assert USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
                     "--site-root", str(site)]) == 0
    assert covers_in(site) == ["munger/ep01_16x9.jpg"]


def test_video_url_still_points_at_the_release(tmp_path):
    """视频太大，不进仓库。"""
    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28")])
    site = tmp_path / "site"
    USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
              "--site-root", str(site)])

    ep = read(site / "data/index.json")["episodes"][0]
    assert ep["urls"]["video"] == f"{RELEASE}/ep01.mp4"
    assert not list((site / "covers").rglob("*.mp4"))


def test_site_root_defaults_to_the_index_grandparent(tmp_path):
    """CI 之外手跑时不写 --site-root 也该落在 site/covers/。"""
    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28")])
    site = tmp_path / "site"

    assert USI.main(["--queue", str(q),
                     "--index", str(site / "data/index.json")]) == 0
    assert (site / "covers/munger/ep01_16x9.jpg").is_file()


def test_declared_cover_missing_on_disk_exits_one(tmp_path, capsys):
    """声明了封面却找不到文件，就地停下，别让页面上线一个灰块。"""
    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28")])
    (q.parent / "ep01" / "cover_16x9.jpg").unlink()
    site = tmp_path / "site"

    assert USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
                     "--site-root", str(site)]) == 1
    assert "封面" in capsys.readouterr().err
    assert not (site / "data/index.json").exists()


def test_slug_escaping_the_site_dir_is_rejected(tmp_path, capsys):
    """slug 直接拼进落盘路径，`../` 不能把文件写到站点目录外面。"""
    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28")])
    payload = json.loads(q.read_text(encoding="utf-8"))
    payload["slug"] = "../../etc"
    q.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    site = tmp_path / "site"

    assert USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
                     "--site-root", str(site)]) == 1
    assert not (tmp_path / "etc").exists()


# ── 旧数据兼容：绝对地址照常能用 ────────────────────────────────────────────

def test_absolute_cover_from_older_data_survives_a_merge(tmp_path):
    """已经在索引里的老记录还挂着 Release 绝对地址，不该被改也不该被丢。"""
    site = tmp_path / "site"
    (site / "data").mkdir(parents=True)
    old_url = "https://github.com/o/r/releases/download/clips-dalio/ep01_cover_16x9.jpg"
    (site / "data/index.json").write_text(json.dumps(
        {"schema": 1, "updated_at": "x", "episodes": [
            {**episode("ep01", "2026-07-20"), "slug": "dalio", "speaker": "达利欧",
             "urls": {"video": "https://example.com/ep01.mp4",
                      "cover_16x9": old_url}}]}, ensure_ascii=False),
        encoding="utf-8")

    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28")])
    assert USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
                     "--site-root", str(site)]) == 0

    by_slug = {e["slug"]: e for e in read(site / "data/index.json")["episodes"]}
    assert by_slug["dalio"]["urls"]["cover_16x9"] == old_url
    assert by_slug["munger"]["urls"]["cover_16x9"] == "covers/munger/ep01_16x9.jpg"


def test_queue_without_a_declared_cover_keeps_its_urls(tmp_path):
    """没声明 files.cover_16x9 的老 queue 原样放过，不炸也不改写。"""
    q = deliver(tmp_path, "munger", [episode("ep01", "2026-07-28")])
    site = tmp_path / "site"

    assert USI.main(["--queue", str(q), "--index", str(site / "data/index.json"),
                     "--site-root", str(site)]) == 0
    ep = read(site / "data/index.json")["episodes"][0]
    assert ep["urls"] == {"video": "https://example.com/ep01.mp4"}
    assert covers_in(site) == []


# ── 保留策略：只留索引引用到的那些 ──────────────────────────────────────────

def test_cover_of_an_episode_dropped_from_the_index_is_pruned(tmp_path):
    """索引里删掉的集，封面跟着回收，否则仓库只涨不降。

    注意 ``merge`` 本身不会删记录：批次从 4 集重跑成 2 集时 ep03/ep04 仍留在
    索引里，封面也该留着让页面继续显示。真正的孤儿来自有人把记录从
    index.json 里摘掉，或者集的 id 变了。
    """
    site = tmp_path / "site"
    index = site / "data/index.json"
    four = [cover_episode(f"ep0{i}", f"2026-07-2{i}") for i in (1, 2, 3, 4)]
    USI.main(["--queue", str(deliver(tmp_path, "munger", four)),
              "--index", str(index), "--site-root", str(site)])
    assert len(covers_in(site)) == 4

    data = read(index)
    data["episodes"] = [e for e in data["episodes"] if e["id"] in ("ep01", "ep02")]
    index.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    two = [cover_episode(f"ep0{i}", f"2026-07-2{i}") for i in (1, 2)]
    USI.main(["--queue", str(deliver(tmp_path, "munger", two)),
              "--index", str(index), "--site-root", str(site)])

    assert covers_in(site) == ["munger/ep01_16x9.jpg", "munger/ep02_16x9.jpg"]


def test_rerunning_a_smaller_batch_keeps_the_older_episodes_covers(tmp_path):
    """4 集重跑成 2 集时 ep03/ep04 还在索引里，封面不能被当孤儿删掉。"""
    site = tmp_path / "site"
    index = site / "data/index.json"
    four = [cover_episode(f"ep0{i}", f"2026-07-2{i}") for i in (1, 2, 3, 4)]
    USI.main(["--queue", str(deliver(tmp_path, "munger", four)),
              "--index", str(index), "--site-root", str(site)])

    two = [cover_episode(f"ep0{i}", f"2026-07-2{i}") for i in (1, 2)]
    USI.main(["--queue", str(deliver(tmp_path, "munger", two)),
              "--index", str(index), "--site-root", str(site)])

    assert len(covers_in(site)) == 4
    for ep in read(index)["episodes"]:
        assert (site / ep["urls"]["cover_16x9"]).is_file()


def test_pruning_keeps_other_slugs(tmp_path):
    """回收只认索引，不认 slug —— 别的 slug 的封面不能被顺手删掉。"""
    site = tmp_path / "site"
    index = site / "data/index.json"
    USI.main(["--queue", str(deliver(tmp_path, "dalio",
                                     [cover_episode("ep01", "2026-07-20")])),
              "--index", str(index), "--site-root", str(site)])
    USI.main(["--queue", str(deliver(tmp_path, "munger",
                                     [cover_episode("ep01", "2026-07-28")])),
              "--index", str(index), "--site-root", str(site)])

    assert covers_in(site) == ["dalio/ep01_16x9.jpg", "munger/ep01_16x9.jpg"]


def test_pruning_empties_a_retired_slug_directory(tmp_path):
    """整个 slug 从索引里下线后，连它的目录一起收走。"""
    site = tmp_path / "site"
    index = site / "data/index.json"
    USI.main(["--queue", str(deliver(tmp_path, "dalio",
                                     [cover_episode("ep01", "2026-07-20")])),
              "--index", str(index), "--site-root", str(site)])
    assert (site / "covers/dalio").is_dir()

    index.write_text(json.dumps({"schema": 1, "updated_at": "x", "episodes": []}),
                     encoding="utf-8")
    USI.main(["--queue", str(deliver(tmp_path, "munger",
                                     [cover_episode("ep01", "2026-07-28")])),
              "--index", str(index), "--site-root", str(site)])

    assert covers_in(site) == ["munger/ep01_16x9.jpg"]
    assert not (site / "covers/dalio").exists()


def test_pruning_spares_covers_of_absolute_url_records(tmp_path):
    """老记录的封面在 Release 上，站点目录里本来就没有它的文件，别误伤别人。"""
    site = tmp_path / "site"
    (site / "covers/munger").mkdir(parents=True)
    kept = site / "covers/munger/ep01_16x9.jpg"
    kept.write_bytes(b"jpeg")
    index = {"schema": 1, "updated_at": "x", "episodes": [
        {"slug": "munger", "id": "ep01",
         "urls": {"cover_16x9": "covers/munger/ep01_16x9.jpg"}},
        {"slug": "dalio", "id": "ep01",
         "urls": {"cover_16x9": f"{RELEASE}/ep01_cover_16x9.jpg"}}]}

    assert USI.prune_covers(index, site) == []
    assert kept.is_file()


def test_rerunning_the_same_queue_leaves_covers_alone(tmp_path):
    """幂等：重跑不该把刚写进去的封面当孤儿删掉。"""
    q = deliver(tmp_path, "munger", [cover_episode("ep01", "2026-07-28"),
                                     cover_episode("ep02", "2026-07-29")])
    site = tmp_path / "site"
    index = site / "data/index.json"

    USI.main(["--queue", str(q), "--index", str(index), "--site-root", str(site)])
    first = read(index)["episodes"]
    USI.main(["--queue", str(q), "--index", str(index), "--site-root", str(site)])

    assert read(index)["episodes"] == first
    assert covers_in(site) == ["munger/ep01_16x9.jpg", "munger/ep02_16x9.jpg"]


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
