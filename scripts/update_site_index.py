#!/usr/bin/env python3
"""把一个 slug 的 ``queue.json`` 合并进手机页的唯一数据源 ``site/data/index.json``。

    python scripts/update_site_index.py \
        --queue deliver/<slug>/queue.json --index site/data/index.json

合并规则（规格第二章）：扁平数组，每条附 ``slug`` 和 ``speaker``；同 slug + id
视为同一条，重跑时覆盖旧记录而不是追加；整表按 ``scheduled_date`` 升序。

页面读这个静态文件而不是打 GitHub API —— 未登录 60 次/小时/IP 的限流一到，
手机页就白屏。

合并完还会做一次下架：索引只保留最近 ``KEEP_BATCHES`` 个批次，更早的整批移出。
一个 slug 就是一个批次（对应一个 ``clips-<slug>`` Release），要么整批留、要么
整批走，不会只剩半批。详见 ``spec/publish_chain.md``。

退出码：0 成功 / 1 输入有误
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

INDEX_SCHEMA = 1

# 一个 slug 一个 Release，tag 形如 clips-<slug>（与 produce.py 同一约定）
RELEASE_TAG_PREFIX = "clips-"

# 索引里保留的批次数。一批 2 集、每天发一条，3 批约等于一周的存量：
# 手机页「往期」还能翻到上一轮素材，又不至于让重复内容一直堆着。
KEEP_BATCHES = 3


def load_index(path: Path) -> dict:
    """读现有索引；不存在就给一份空的。"""
    if not path.is_file():
        return {"schema": INDEX_SCHEMA, "updated_at": None, "episodes": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("episodes"), list):
        raise ValueError(f"{path} 不是合法的索引文件（缺 episodes 数组）")
    return data


def sort_key(ep: dict) -> tuple:
    """按 scheduled_date 升序；同一天内按 slug + id 稳定排，避免每次重跑抖动。"""
    return (str(ep.get("scheduled_date") or ""), str(ep.get("slug") or ""),
            str(ep.get("id") or ""))


def release_tag(slug: str) -> str:
    return f"{RELEASE_TAG_PREFIX}{slug}"


def batch_of(ep: dict) -> str:
    """一条记录属于哪个批次。没有 slug 就没法归批，直接报错而不是丢进无名批。"""
    slug = ep.get("slug")
    if not slug:
        raise ValueError(f"索引里有一条没有 slug 的记录（id={ep.get('id')!r}），"
                         f"无法判断它属于哪个批次")
    return str(slug)


def group_batches(episodes: list) -> dict:
    """slug → 该批次的集，保持索引里的原有顺序。"""
    batches: dict = {}
    for ep in episodes:
        batches.setdefault(batch_of(ep), []).append(ep)
    return batches


def batch_rank(slug: str, episodes: list) -> tuple:
    """批次新旧的判定依据：批内最大的 ``batch_at``（即 queue.json 的
    ``generated_at``），同刻再按 slug 兜底，保证排序稳定、可复现。

    只补过半批的批次按「最近一次合并」算新，这样重跑补集不会把整批带老。
    早于本次改动写进索引的旧记录没有 ``batch_at``，取空串排在最老 —— 它们本来
    就是最该先下架的。
    """
    return (max(str(ep.get("batch_at") or "") for ep in episodes), slug)


def prune_batches(episodes: list, keep: int = KEEP_BATCHES) -> tuple:
    """只留最近 ``keep`` 个批次。返回 ``(保留的集, 被下架的 slug 列表)``。

    按批次整体取舍，所以同一批的集不会被拆散。
    """
    if keep < 1:
        raise ValueError(f"保留批次数必须 >= 1，收到 {keep}")

    batches = group_batches(episodes)
    ordered = sorted(batches, key=lambda s: batch_rank(s, batches[s]))
    dropped = ordered[:-keep] if len(ordered) > keep else []
    return [ep for ep in episodes if batch_of(ep) not in set(dropped)], dropped


def merge(index: dict, queue: dict, updated_at: datetime | None = None,
          keep: int = KEEP_BATCHES) -> tuple:
    """把 queue 里的每集并进 index，再下架超出保留窗口的旧批次。

    同 slug+id 覆盖，不追加。返回 ``(新索引, 被下架的 slug 列表)``。
    """
    slug = queue.get("slug")
    speaker = queue.get("speaker", "")
    if not slug:
        raise ValueError("queue.json 缺少 slug")

    # 批次新旧全靠它，缺了就只能瞎猜哪批该下架 —— 宁可在这里炸掉
    batch_at = queue.get("generated_at")
    if not batch_at:
        raise ValueError(f"queue.json（slug={slug}）缺少 generated_at，"
                         f"无法判断批次新旧")

    episodes = [dict(ep) for ep in index.get("episodes", [])]
    by_key = {(ep.get("slug"), ep.get("id")): i for i, ep in enumerate(episodes)}

    for ep in queue.get("episodes", []):
        merged = {**ep, "slug": slug, "speaker": speaker,
                  "batch_at": batch_at, "release_tag": release_tag(slug)}
        key = (slug, ep.get("id"))
        if key in by_key:
            episodes[by_key[key]] = merged
        else:
            by_key[key] = len(episodes)
            episodes.append(merged)

    kept, dropped = prune_batches(episodes, keep)
    now = updated_at or datetime.now(timezone.utc)
    return {
        "schema": INDEX_SCHEMA,
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "episodes": sorted(kept, key=sort_key),
    }, dropped


def write_index(path: Path, index: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="update_site_index.py",
        description="把 deliver/<slug>/queue.json 合并进 site/data/index.json")
    p.add_argument("--queue", required=True, help="queue.json 路径")
    p.add_argument("--index", required=True,
                   help="site/data/index.json 路径，不存在则创建")
    p.add_argument("--keep-batches", type=int, default=KEEP_BATCHES,
                   help=f"索引里保留最近几个批次（默认 {KEEP_BATCHES}）")
    args = p.parse_args(argv)

    queue_path = Path(args.queue)
    if not queue_path.is_file():
        print(f"[site-index] 找不到 {queue_path}", file=sys.stderr)
        return 1

    index_path = Path(args.index)
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        index, dropped = merge(load_index(index_path), queue,
                               keep=args.keep_batches)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[site-index] 合并失败：{e}", file=sys.stderr)
        return 1

    write_index(index_path, index)
    print(f"[site-index] {queue.get('slug')} 的 "
          f"{len(queue.get('episodes', []))} 集已合并，索引共 "
          f"{len(index['episodes'])} 集 → {index_path}")
    for slug in dropped:
        print(f"[site-index] 下架旧批次 {slug}（Release {release_tag(slug)} "
              f"仍在，用 scripts/prune_releases.py 清理）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
