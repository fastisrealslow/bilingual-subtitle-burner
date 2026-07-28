"""陈旧 Release 的清理（scripts/prune_releases.py）。

删 Release 不可逆，所以这里盯三件事：默认 dry-run 一个删除调用都不发；真删时
只碰索引里已经没有的批次；删除走的是两条 REST DELETE（release id + tag ref），
不是会 401 的 ``gh release delete --cleanup-tag``。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import prune_releases as PR                        # noqa: E402


def gh_404(path):
    return PR.GhError(f"gh api {path} 失败（退出码 1）：HTTP 404: Not Found",
                      "gh: Not Found (HTTP 404)\n")


class FakeGh:
    """替掉 run_gh：记下每一次调用，按内存里的 Release 表作答。

    删除按 REST 路径路由，并且真的拿 id 反查 Release —— id 拼错就跟 GitHub 一样
    回 404，免得测试只验证「调用发出去了」。
    """

    def __init__(self, releases):
        # tag → (created_at, [资产字节数])
        self.releases = dict(releases)
        self.ids = {t: 1000 + n for n, t in enumerate(sorted(self.releases))}
        self.tag_refs = set(self.releases)
        self.fails = {}                       # REST 路径 → 要抛的 GhError
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        head = args[:2]
        if head == ["release", "list"]:
            return json.dumps([{"tagName": t, "createdAt": v[0]}
                               for t, v in self.releases.items()])
        if head == ["release", "view"]:
            tag = args[2]
            assert "databaseId" in args[4], "删除要用 id，view 必须取 databaseId"
            return json.dumps({"databaseId": self.ids[tag],
                               "assets": [{"size": s}
                                          for s in self.releases[tag][1]]})
        if head == ["api", "-X"]:
            assert args[2] == "DELETE", f"意外的 REST 方法：{args}"
            return self._delete(args[3])
        raise AssertionError(f"意外的 gh 调用：{args}")

    def _delete(self, path):
        if path in self.fails:
            raise self.fails[path]
        _, sep, tag = path.partition("/git/refs/tags/")
        if sep:
            if tag not in self.tag_refs:
                raise gh_404(path)
            self.tag_refs.discard(tag)
            return ""
        _, sep, rid = path.partition("/releases/")
        if sep:
            tag = next((t for t, i in self.ids.items() if str(i) == rid), None)
            if tag is None or tag not in self.releases:
                raise gh_404(path)
            self.releases.pop(tag)
            return ""
        raise AssertionError(f"意外的 REST 路径：{path}")

    @property
    def api_deletes(self):
        """按顺序发出的 REST DELETE 路径。"""
        return [a[3] for a in self.calls if a[:3] == ["api", "-X", "DELETE"]]

    @property
    def deleted(self):
        """发出过 Release DELETE 的 tag，按调用顺序（含失败的尝试）。"""
        by_id = {str(i): t for t, i in self.ids.items()}
        return [by_id.get(rid, rid) for rid in
                (p.rpartition("/")[2] for p in self.api_deletes
                 if "/releases/" in p)]

    @property
    def deleted_tag_refs(self):
        """发出过 tag ref DELETE 的 tag，按调用顺序（含失败的尝试）。"""
        return [p.rpartition("/")[2] for p in self.api_deletes
                if "/git/refs/tags/" in p]


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

    assert gh.api_deletes == []
    assert set(gh.releases) == set(THREE)
    assert gh.tag_refs == set(THREE)
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

    assert gh.deleted_tag_refs == ["clips-partner"]
    assert gh.tag_refs == {"clips-live", "clips-chain"}


def test_execute_sends_exactly_two_rest_deletes_per_orphan(tmp_path,
                                                           monkeypatch):
    """先按 id 删 Release，再删 tag ref —— 不走会 401 的 gh release delete。"""
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live")

    assert PR.main(["--index", str(index), "--repo", "o/n", "--execute"]) == 0

    assert gh.api_deletes == [
        f"repos/o/n/releases/{gh.ids['clips-partner']}",
        "repos/o/n/git/refs/tags/clips-partner",
        f"repos/o/n/releases/{gh.ids['clips-chain']}",
        "repos/o/n/git/refs/tags/clips-chain"]
    assert not any(a[:2] == ["release", "delete"] for a in gh.calls)


def test_the_release_id_comes_from_the_release_not_a_neighbour(tmp_path,
                                                               monkeypatch):
    """删除只认 databaseId，拿错一个就会删掉别人的 Release。"""
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live", "chain")

    assert PR.main(["--index", str(index), "--execute"]) == 0

    assert gh.api_deletes[0].endswith(f"/releases/{gh.ids['clips-partner']}")
    assert set(gh.releases) == {"clips-live", "clips-chain"}


def test_a_tag_ref_that_is_already_gone_is_not_an_error(tmp_path, monkeypatch,
                                                        capsys):
    """404 说明目标状态本来就达成了，没什么要收尾的。"""
    gh = install(monkeypatch, THREE)
    gh.tag_refs.discard("clips-partner")
    index = write_index(tmp_path, "live", "chain")

    assert PR.main(["--index", str(index), "--execute"]) == 0

    assert gh.deleted == ["clips-partner"]
    assert "tag 本来就不在" in capsys.readouterr().out


def test_a_failing_tag_ref_delete_warns_but_keeps_going(tmp_path, monkeypatch,
                                                        capsys):
    """Release 已经删了，剩个空 ref 是收尾问题，不能算整体失败、也不能吞掉。"""
    gh = install(monkeypatch, THREE)
    gh.fails["repos/o/n/git/refs/tags/clips-partner"] = PR.GhError(
        "gh api 失败（退出码 1）：HTTP 403: Forbidden", "HTTP 403: Forbidden\n")
    index = write_index(tmp_path, "live")

    assert PR.main(["--index", str(index), "--repo", "o/n", "--execute"]) == 2

    # 两个 Release 都删掉了，后一个的 tag 也没被前一个的失败带偏
    assert set(gh.releases) == {"clips-live"}
    assert gh.deleted_tag_refs == ["clips-partner", "clips-chain"]
    assert gh.tag_refs == {"clips-live", "clips-partner"}   # 只剩没删掉的那个
    err = capsys.readouterr().err
    assert "clips-partner" in err and "403" in err and "人工收尾" in err


def test_a_failing_release_delete_exits_one_and_stops(tmp_path, monkeypatch,
                                                      capsys):
    """删不掉多半是 token 权限问题，后面几个只会同样失败。"""
    gh = install(monkeypatch, THREE)
    gh.fails[f"repos/o/n/releases/{gh.ids['clips-partner']}"] = PR.GhError(
        "gh api 失败（退出码 1）：HTTP 401: Requires authentication",
        "HTTP 401: Requires authentication\n")
    index = write_index(tmp_path, "live")

    assert PR.main(["--index", str(index), "--repo", "o/n", "--execute"]) == 1

    # 第一个就失败：不再往下删，也不该去动它的 tag
    assert gh.deleted == ["clips-partner"]
    assert gh.deleted_tag_refs == []
    assert set(gh.releases) == set(THREE) and gh.tag_refs == set(THREE)
    assert "401" in capsys.readouterr().err


def test_is_not_found_only_matches_404(monkeypatch):
    assert PR.is_not_found(PR.GhError("x", "gh: Not Found (HTTP 404)\n"))
    assert not PR.is_not_found(
        PR.GhError("x", "HTTP 401: Requires authentication\n"))
    # tag 名里带 404 不能被当成状态码
    assert not PR.is_not_found(
        PR.GhError("gh api repos/o/n/git/refs/tags/clips-404 失败", ""))


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

    assert gh.calls and all("--repo" in call and "owner/name" in call
                            for call in gh.calls)


def test_without_repo_the_rest_paths_use_ghs_placeholders(tmp_path,
                                                          monkeypatch):
    """``gh api`` 没有 --repo，只能靠 {owner}/{repo} 占位符落到当前仓库。"""
    gh = install(monkeypatch, THREE)
    index = write_index(tmp_path, "live", "chain")
    PR.main(["--index", str(index), "--execute"])

    assert gh.api_deletes == [
        f"repos/{{owner}}/{{repo}}/releases/{gh.ids['clips-partner']}",
        "repos/{owner}/{repo}/git/refs/tags/clips-partner"]
    assert not any("--repo" in call for call in gh.calls)


def test_human_size_formats_each_unit():
    assert PR.human_size(512) == "512.0 B"
    assert PR.human_size(1000) == "1000.0 B"      # 按 1024 进位，不是 1000
    assert PR.human_size(1024) == "1.0 KB"
    assert PR.human_size(5 * 1024 * 1024) == "5.0 MB"
    assert PR.human_size(3 * 1024 ** 3) == "3.0 GB"
