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

退出码：0 成功 / 1 输入有误或状态异常
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from update_site_index import (RELEASE_TAG_PREFIX, batch_of, load_index,
                               release_tag)

# gh release list 默认只给 30 条，批次多了会漏判成孤儿
LIST_LIMIT = 200


class GhError(RuntimeError):
    """gh 调用失败。"""


def run_gh(args: list) -> str:
    """跑一条 gh 命令，返回 stdout。非零退出就抛，不吞。"""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GhError(f"gh {' '.join(args)} 失败（退出码 {proc.returncode}）："
                      f"{proc.stderr.strip()}")
    return proc.stdout


def repo_args(repo: str | None) -> list:
    return ["--repo", repo] if repo else []


def list_releases(repo: str | None = None) -> list:
    """仓库里所有 Release 的 ``{tag, created_at}``。"""
    out = run_gh(["release", "list", "--limit", str(LIST_LIMIT),
                  "--json", "tagName,createdAt", *repo_args(repo)])
    return [{"tag": r["tagName"], "created_at": r.get("createdAt", "")}
            for r in json.loads(out)]


def release_size(tag: str, repo: str | None = None) -> int:
    """一个 Release 所有资产的字节数之和。"""
    out = run_gh(["release", "view", tag, "--json", "assets",
                  *repo_args(repo)])
    return sum(int(a.get("size", 0))
               for a in json.loads(out).get("assets", []))


def delete_release(tag: str, repo: str | None = None) -> None:
    """删 Release 并连带删 tag —— 留着空 tag 下次判定又会当成孤儿。"""
    run_gh(["release", "delete", tag, "--yes", "--cleanup-tag",
            *repo_args(repo)])


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
    p = argparse.ArgumentParser(
        prog="prune_releases.py",
        description="删掉已经不在 site/data/index.json 里的批次 Release")
    p.add_argument("--index", required=True, help="site/data/index.json 路径")
    p.add_argument("--repo", default=None,
                   help="owner/name，默认用 gh 当前仓库")
    p.add_argument("--execute", action="store_true",
                   help="真的删除。不加这个开关只打印将要删什么")
    args = p.parse_args(argv)

    index_path = Path(args.index)
    if not index_path.is_file():
        print(f"[prune] 找不到 {index_path}", file=sys.stderr)
        return 1

    try:
        index = load_index(index_path)
        orphans = find_orphans(index, list_releases(args.repo))
        for r in orphans:
            r["size"] = release_size(r["tag"], args.repo)
    except (ValueError, GhError, json.JSONDecodeError) as e:
        print(f"[prune] {e}", file=sys.stderr)
        return 1

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

    try:
        for r in orphans:
            delete_release(r["tag"], args.repo)
            print(f"[prune] 已删除 {r['tag']}（含 tag）")
    except GhError as e:
        print(f"[prune] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
