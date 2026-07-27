#!/usr/bin/env python3
"""把一个 slug 的 ``queue.json`` 合并进手机页的唯一数据源 ``site/data/index.json``。

    python scripts/update_site_index.py \
        --queue deliver/<slug>/queue.json --index site/data/index.json

合并规则（规格第二章）：扁平数组，每条附 ``slug`` 和 ``speaker``；同 slug + id
视为同一条，重跑时覆盖旧记录而不是追加；整表按 ``scheduled_date`` 升序。

页面读这个静态文件而不是打 GitHub API —— 未登录 60 次/小时/IP 的限流一到，
手机页就白屏。

退出码：0 成功 / 1 输入有误
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

INDEX_SCHEMA = 1


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


def merge(index: dict, queue: dict, updated_at: datetime | None = None) -> dict:
    """把 queue 里的每集并进 index。同 slug+id 覆盖，不追加。"""
    slug = queue.get("slug")
    speaker = queue.get("speaker", "")
    if not slug:
        raise ValueError("queue.json 缺少 slug")

    episodes = [dict(ep) for ep in index.get("episodes", [])]
    by_key = {(ep.get("slug"), ep.get("id")): i for i, ep in enumerate(episodes)}

    for ep in queue.get("episodes", []):
        merged = {**ep, "slug": slug, "speaker": speaker}
        key = (slug, ep.get("id"))
        if key in by_key:
            episodes[by_key[key]] = merged
        else:
            by_key[key] = len(episodes)
            episodes.append(merged)

    now = updated_at or datetime.now(timezone.utc)
    return {
        "schema": INDEX_SCHEMA,
        "updated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "episodes": sorted(episodes, key=sort_key),
    }


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
    args = p.parse_args(argv)

    queue_path = Path(args.queue)
    if not queue_path.is_file():
        print(f"[site-index] 找不到 {queue_path}", file=sys.stderr)
        return 1

    index_path = Path(args.index)
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        index = merge(load_index(index_path), queue)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[site-index] 合并失败：{e}", file=sys.stderr)
        return 1

    write_index(index_path, index)
    print(f"[site-index] {queue.get('slug')} 的 "
          f"{len(queue.get('episodes', []))} 集已合并，索引共 "
          f"{len(index['episodes'])} 集 → {index_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
