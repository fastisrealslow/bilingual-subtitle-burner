#!/usr/bin/env python3
"""删掉已经不在 ``site/data/index.json`` 里的批次 Release（默认只看不删）。

    # 只打印将要删除什么
    python scripts/prune_releases.py --index site/data/index.json

    # 真删（不可逆）
    python scripts/prune_releases.py --index site/data/index.json --execute

索引下架（``update_site_index.py`` 的保留窗口）只把条目从 index.json 里摘掉，
Release 还在仓库里占空间。这个脚本负责收尾：把 ``clips-`` 开头、且索引里已经
没有对应批次的 Release 连同 tag 一起删掉。

两条安全线：

1. 默认 dry-run。删除不可逆，必须显式加 ``--execute`` 才会真的调 gh。
2. 只删索引里没有的。索引里引用了一个不存在的 Release 属于预期外状态 ——
   直接报错退出，而不是假装没看见继续删别的。

不带 ``--`` 前缀的 tag（别的用途的 Release）一律不碰。

删除走 REST：先 ``DELETE /repos/{repo}/releases/{id}``，再
``DELETE /repos/{repo}/git/refs/tags/{tag}``。不用 ``gh release delete
--cleanup-tag`` —— 同一个 token 下那条子命令会 401，直接打 REST 却能过。

退出码：0 成功 / 1 输入有误、状态异常或 Release 没删掉 /
2 Release 都删了但有 tag 没清干净（需要人工收尾）

命令行参数错误算「输入有误」，退 1。argparse 默认把用法错误退 2，会和上面的
「有 tag 需要人工收尾」撞号 —— 敲错一个 flag，照退出码表读出来的结论是「去仓库
里收拾残留的空 ref」，而其实一个删除请求都没发出去。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

from cli_exit import EXIT_CONFIG, ConfigErrorArgumentParser
from update_site_index import (RELEASE_TAG_PREFIX, batch_of, load_index,
                               release_tag)

# gh release list 默认只给 30 条，批次多了会漏判成孤儿
LIST_LIMIT = 200


class GhError(RuntimeError):
    """gh 调用失败。"""

    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        # 状态码只从原始 stderr 认：拼好的消息里带着 tag 名，
        # tag 里恰好有 "404" 就会误判。
        self.stderr = stderr


def run_gh(args: list) -> str:
    """跑一条 gh 命令，返回 stdout。非零退出就抛，不吞。"""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args)} 失败（退出码 {proc.returncode}）："
                      f"{proc.stderr.strip()}", proc.stderr)
    return proc.stdout


def is_not_found(err: GhError) -> bool:
    """这次失败是不是 404。"""
    return bool(re.search(r"HTTP 404\b", err.stderr))


def repo_args(repo: str | None) -> list:
    return ["--repo", repo] if repo else []


def api_repo(repo: str | None) -> str:
    """REST 路径里的 owner/name。``gh api`` 没有 ``--repo``，但认这对占位符。"""
    return repo if repo else "{owner}/{repo}"


def list_releases(repo: str | None = None) -> list:
    """仓库里所有 Release 的 ``{tag, created_at}``。"""
    out = run_gh(["release", "list", "--limit", str(LIST_LIMIT),
                  "--json", "tagName,createdAt", *repo_args(repo)])
    return [{"tag": r["tagName"], "created_at": r.get("createdAt", "")}
            for r in json.loads(out)]


def release_detail(tag: str, repo: str | None = None) -> dict:
    """一个 Release 的数值 id 和资产字节数之和。

    ``id`` 是 REST 的 ``databaseId``：删除只认它，不认 tag 名。
    """
    out = run_gh(["release", "view", tag, "--json", "databaseId,assets",
                  *repo_args(repo)])
    data = json.loads(out)
    return {"id": data["databaseId"],
            "size": sum(int(a.get("size", 0))
                        for a in data.get("assets", []))}


def delete_release(release_id: int, repo: str | None = None) -> None:
    """删 Release 本体。"""
    run_gh(["api", "-X", "DELETE",
            f"repos/{api_repo(repo)}/releases/{release_id}"])


def delete_tag_ref(tag: str, repo: str | None = None) -> bool:
    """删 tag ref —— 留着空 tag 下次判定又会当成孤儿。

    返回是否真的删掉了：ref 本来就不存在（404）说明目标状态已经达成，不算错。
    """
    try:
        run_gh(["api", "-X", "DELETE",
                f"repos/{api_repo(repo)}/git/refs/tags/{tag}"])
    except GhError as e:
        if is_not_found(e):
            return False
        raise
    return True


def indexed_tags(index: dict) -> set:
    """索引里还活着的批次 tag。"""
    return {release_tag(batch_of(ep)) for ep in index.get("episodes", [])}


def find_orphans(index: dict, releases: list) -> list:
    """``clips-`` 开头、但索引里已经没有的 Release。

    反过来，索引引用了一个不存在的 Release 说明索引和仓库对不上了 —— 这时候
    谁是孤儿已经不可信，直接抛错。
    """
    live = indexed_tags(index)
    existing = {r["tag"] for r in releases}
    missing = sorted(live - existing)
    if missing:
        raise ValueError(f"索引引用了不存在的 Release：{'、'.join(missing)}；"
                         f"仓库和索引对不上，先修好再清理")
    return [r for r in releases
            if r["tag"].startswith(RELEASE_TAG_PREFIX) and r["tag"] not in live]


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def main(argv=None) -> int:
    p = ConfigErrorArgumentParser(
        prog="prune_releases.py",
        description="删掉已经不在 site/data/index.json 里的批次 Release",
        epilog="退出码：0 成功 / 1 参数或输入有误、Release 没删掉 / "
               "2 Release 已删但有 tag 需要人工收尾")
    p.add_argument("--index", required=True, help="site/data/index.json 路径")
    p.add_argument("--repo", default=None,
                   help="owner/name，默认用 gh 当前仓库")
    p.add_argument("--execute", action="store_true",
                   help="真的删除。不加这个开关只打印将要删什么")
    args = p.parse_args(argv)

    index_path = Path(args.index)
    if not index_path.is_file():
        print(f"[prune] 找不到 {index_path}", file=sys.stderr)
        return EXIT_CONFIG

    try:
        index = load_index(index_path)
        orphans = find_orphans(index, list_releases(args.repo))
        for r in orphans:
            r.update(release_detail(r["tag"], args.repo))
    except (ValueError, GhError, json.JSONDecodeError) as e:
        print(f"[prune] {e}", file=sys.stderr)
        return EXIT_CONFIG

    if not orphans:
        print("[prune] 没有需要清理的 Release")
        return 0

    mode = "删除" if args.execute else "将要删除（dry-run，未真删）"
    total = sum(r["size"] for r in orphans)
    print(f"[prune] {mode} {len(orphans)} 个 Release，"
          f"共 {human_size(total)}：")
    for r in orphans:
        print(f"  {r['tag']}  {human_size(r['size'])}  "
              f"建于 {r['created_at']}")

    if not args.execute:
        print("[prune] dry-run 结束，加 --execute 才会真的删除")
        return 0

    # Release 删不掉就停手：多半是 token 权限问题，后面几个只会同样失败。
    # tag 没删掉则继续 —— 占空间的 Release 已经删了，剩个空 ref 是收尾问题。
    stale_tags = []
    try:
        for r in orphans:
            delete_release(r["id"], args.repo)
            try:
                removed = delete_tag_ref(r["tag"], args.repo)
            except GhError as e:
                stale_tags.append(r["tag"])
                print(f"[prune] 警告：{r['tag']} 的 Release 已删除，"
                      f"但 tag 没删掉：{e}", file=sys.stderr)
            else:
                print(f"[prune] 已删除 {r['tag']}"
                      f"（{'含 tag' if removed else 'tag 本来就不在'}）")
    except GhError as e:
        print(f"[prune] {e}", file=sys.stderr)
        return 1

    if stale_tags:
        print(f"[prune] {len(stale_tags)} 个 tag 需要人工收尾："
              f"{'、'.join(stale_tags)}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
