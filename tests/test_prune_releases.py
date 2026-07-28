"""陈旧 Release 的清理（scripts/prune_releases.py）。

删 Release 不可逆，所以这里盯两件事：默认 dry-run 一个删除调用都不发；真删时
只碰索引里已经没有的批次。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import prune_releases as PR                        # noqa: E402


class FakeGh:
    """替掉 run_gh：记下每一次调用，按内存里的 Release 表作答。"""

    def __init__(self, releases):
        # tag → (created_at, [资产字节数])
        self.releases = dict(releases)
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        head = args[:2]
        if head == ["release", "list"]:
            return json.dumps([{"tagName": t, "createdAt": v[0]}
                               for t, v in self.releases.items()])
        if head == ["release", "view"]:
            return json.dumps({"assets": [{"size": s}
                                          for s in self.releases[args[2]][1]]})
        if head == ["release", "delete"]:
            self.releases.pop(args[2])
            return ""
        raise AssertionError(f"意外的 gh 调用：{args}")

    @property
    def deleted(self):
        return [a[2] for a in self.calls if a[:2] == ["release", "delete"]]


def install(monkeypatch, releases):
    fake = FakeGh(releases)
    monkeypatch.setattr(PR, "run_gh", fake)
    return fake


def write_index(tmp_path, *slugs):
    path = tmp_path / "index.json"
    episodes = [{"id": "ep01", "slug": s, "scheduled_date": "2026-08-01"}
                for s in slugs]
    path.write_text(json.dumps({"schema": 1, "updated_at": "x",
                                "episodes": episodes}, ensure_ascii=False),
                    encoding="utf-8")
    return path


THREE = {"clips-live": ("2026-07-27T00:00:00Z", [1024]),
         "clips-partner": ("2026-07-10T00:00:00Z", [2 * 1024 * 1024]),
         "clips-chain": ("2026-07-12T00:00:00Z", [3 * 1024 * 1024, 1024])}


# ── 孤儿判定 ────────────────────────────────────────────────────────────────

def test_orphans_are_the_clips_releases_missing_from_the_index():
    index = {"episodes": [{"id": "ep01", "slug": "live"}]}
    releases = [{"tag": "clips-live", "created_at": "a"},
                {"tag": "clips-partner", "created_at": "b"},
                {"tag": "clips-chain", "created_at": "c"}]

    assert [r["tag"] for r in PR.find_orphans(index, releases)] == [
        "clips-partner", "clips-chain"]


def test_releases_outside_the_clips_prefix_are_never_touched():
    index = {"episodes": [{"id": "ep01", "slug": "live"}]}
    releases = [{"tag": "clips-live", "created_at": "a"},
                {"tag": "v1.2.0", "created_at": "b"},
                {"tag": "nightly", "created_at": "c"}]

    assert PR.find_orphans(index, releases) == []


def test_every_indexed_batch_is_kept_even_when_all_are_stale():
    index = {"episodes": [{"id": "ep01", "slug": "a"},
                          {"id": "ep02", "slug": "b"}]}
    releases = [{"tag": "clips-a", "created_at": "x"},
                {"tag": "clips-b", "created_at": "y"}]

    assert PR.find_orphans(index, releases) == []


def test_an_index_pointing_at_a_missing_release_is_an_error():
    """索引和仓库对不上时谁是孤儿已经不可信，宁可炸掉也不能接着删。"""
    index = {"episodes": [{"id": "ep01", "slug": "gone"}]}
    releases = [{"tag": "clips-other", "created_at": "x"}]

    try:
        PR.find_orphans(index, releases)
    except ValueError as e:
        assert "clips-gone" in str(e)
    else:
        raise AssertionError("索引引用了不存在的 Release，应该报错")


def test_an_entry_without_slug_is_an_error():
    try:
        PR.find_orphans({"episodes": [{"id": "ep01"}]}, [])
    except ValueError as e:
        assert "slug" in str(e)
    else:
        raise AssertionError("没有 slug 的记录应该报错")


# ── dry-run ─────────────────────────────────────────────────────────────────

def test_dry_run_is_the_default_and_deletes_nothing(tmp_path, monkeypatch,
                                                    capsys):
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live")

    assert PR.main(["--index", str(index)]) == 0

    assert gh.deleted == []
    assert set(gh.releases) == set(THREE)
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "clips-partner" in out and "clips-chain" in out
    assert "clips-live" not in out


def test_dry_run_reports_tag_size_and_creation_time(tmp_path, monkeypatch,
                                                    capsys):
    install(monkeypatch, THREE)
    index = write_index(tmp_path, "live")
    PR.main(["--index", str(index)])

    out = capsys.readouterr().out
    assert "clips-partner  2.0 MB  建于 2026-07-10T00:00:00Z" in out
    assert "clips-chain  3.0 MB  建于 2026-07-12T00:00:00Z" in out


def test_nothing_to_do_when_every_release_is_indexed(tmp_path, monkeypatch,
                                                     capsys):
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live", "partner", "chain")

    assert PR.main(["--index", str(index)]) == 0
    assert gh.deleted == []
    assert "没有需要清理" in capsys.readouterr().out


# ── 真删 ────────────────────────────────────────────────────────────────────

def test_execute_deletes_only_the_unindexed_batches(tmp_path, monkeypatch):
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live")

    assert PR.main(["--index", str(index), "--execute"]) == 0

    assert gh.deleted == ["clips-partner", "clips-chain"]
    assert set(gh.releases) == {"clips-live"}


def test_execute_removes_the_tag_too(tmp_path, monkeypatch):
    """留下空 tag 的话，下一轮判定又会把它当成孤儿。"""
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live", "chain")
    PR.main(["--index", str(index), "--execute"])

    delete = next(a for a in gh.calls if a[:2] == ["release", "delete"])
    assert "--cleanup-tag" in delete and "--yes" in delete


def test_execute_stops_on_a_bad_index_without_deleting(tmp_path, monkeypatch,
                                                       capsys):
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live", "gone")

    assert PR.main(["--index", str(index), "--execute"]) == 1
    assert gh.deleted == []
    assert "clips-gone" in capsys.readouterr().err


# ── gh 出错不吞 ─────────────────────────────────────────────────────────────

def test_a_failing_gh_call_exits_one(tmp_path, monkeypatch, capsys):
    def boom(args):
        raise PR.GhError("gh release list 失败（退出码 1）：no auth")

    monkeypatch.setattr(PR, "run_gh", boom)
    assert PR.main(["--index", str(write_index(tmp_path, "live"))]) == 1
    assert "no auth" in capsys.readouterr().err


def test_run_gh_raises_on_a_nonzero_exit(monkeypatch):
    class Proc:
        returncode = 1
        stdout = ""
        stderr = "release not found"

    monkeypatch.setattr(PR.subprocess, "run", lambda *a, **k: Proc())
    try:
        PR.run_gh(["release", "view", "clips-x"])
    except PR.GhError as e:
        assert "release not found" in str(e)
    else:
        raise AssertionError("gh 非零退出应该抛 GhError")


def test_missing_index_exits_one(tmp_path, capsys):
    assert PR.main(["--index", str(tmp_path / "nope.json")]) == 1
    assert "找不到" in capsys.readouterr().err


# ── 传参 ────────────────────────────────────────────────────────────────────

def test_repo_flag_is_forwarded_to_gh(tmp_path, monkeypatch):
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live")
    PR.main(["--index", str(index), "--repo", "owner/name"])

    assert all("--repo" in call and "owner/name" in call for call in gh.calls)


def test_human_size_formats_each_unit():
    assert PR.human_size(512) == "512.0 B"
    assert PR.human_size(1000) == "1000.0 B"      # 按 1024 进位，不是 1000
    assert PR.human_size(1024) == "1.0 KB"
    assert PR.human_size(5 * 1024 * 1024) == "5.0 MB"
    assert PR.human_size(3 * 1024 ** 3) == "3.0 GB"
