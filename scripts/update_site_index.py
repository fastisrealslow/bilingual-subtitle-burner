#!/usr/bin/env python3
"""把一个 slug 的 ``queue.json`` 合并进手机页的唯一数据源 ``site/data/index.json``。

    python scripts/update_site_index.py \
        --queue deliver/<slug>/queue.json --index site/data/index.json \
        --site-root site

合并规则（规格第二章）：扁平数组，每条附 ``slug`` 和 ``speaker``；同 slug + id
视为同一条，重跑时覆盖旧记录而不是追加；整表按 ``scheduled_date`` 升序。

页面读这个静态文件而不是打 GitHub API —— 未登录 60 次/小时/IP 的限流一到，
手机页就白屏。

顺带把 16:9 封面**搬进站点目录**（``site/covers/<slug>/<id>_16x9.jpg``）并把
``urls.cover_16x9`` 改写成站内相对路径。Release 资产地址会 302 到
``release-assets.githubusercontent.com``，还带 ``Content-Disposition: attachment``，
大陆 iPhone 上经常加载不出来；同源走 Cloudflare CDN 就没这个问题。视频太大，
继续留在 Release，``urls.video`` 不动。

退出码：0 成功 / 1 输入有误
"""

import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from release_assets import episode_source_dir

INDEX_SCHEMA = 1

# 站点里存封面的子目录，相对 ``--site-root``。页面上的相对路径也以它开头。
COVERS_DIRNAME = "covers"
# queue.json ``files`` / ``urls`` 里的键，以及每集目录里的源文件名
COVER_KEY = "cover_16x9"
LOCAL_COVER_NAME = "cover_16x9.jpg"
# slug 和 id 会直接拼进落盘路径，按 produce.py 对 slug 的同一条规则收口，
# 免得 queue.json 里一个 ``../`` 把文件写到站点目录外面去
SAFE_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")


def safe_name(value: str, what: str) -> str:
    """校验一个要拼进路径的名字，不合法就抛。"""
    name = str(value or "")
    if not SAFE_NAME_RE.fullmatch(name) or set(name) == {"."}:
        raise ValueError(f"{what} {name!r} 不能用作目录名")
    return name


def is_remote_url(value: str) -> bool:
    """旧数据里的封面是 Release 绝对地址，这类不归站点目录管。"""
    return value.startswith(("http://", "https://", "//"))


def cover_relpath(slug: str, ep_id: str) -> str:
    """页面用的站内相对路径，相对 ``site/index.html`` 所在目录。"""
    return f"{COVERS_DIRNAME}/{slug}/{ep_id}_16x9.jpg"


def load_index(path: Path) -> dict:
    """读现有索引；不存在就给一份空的。"""
    if not path.is_file():
        return {"schema": INDEX_SCHEMA, "updated_at": None, "episodes": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("episodes"), list):
        raise ValueError(f"{path} 不是合法的索引文件（缺 episodes 数组）")
    return data


def localize_covers(queue: dict, queue_path: Path, site_root: Path) -> tuple:
    """把该批次的 16:9 封面复制进站点目录，并改写 ``urls.cover_16x9``。

    只处理 ``files.cover_16x9`` 声明过的集：声明了却找不到文件就抛，缺封面的
    页面只会剩个灰块，不如在流水线里直接停下来。没声明的原样放过。
    """
    slug = safe_name(queue.get("slug"), "slug")
    queue_dir = queue_path.parent
    covers_dir = site_root / COVERS_DIRNAME / slug

    episodes, copied = [], []
    for raw in queue.get("episodes", []):
        ep = dict(raw)
        if ep.get("files", {}).get(COVER_KEY):
            ep_id = safe_name(ep.get("id"), "id")
            src = episode_source_dir(queue_dir, ep) / LOCAL_COVER_NAME
            if not src.is_file():
                raise FileNotFoundError(f"缺少封面 {src}")
            covers_dir.mkdir(parents=True, exist_ok=True)
            dst = covers_dir / f"{ep_id}_16x9.jpg"
            shutil.copyfile(src, dst)
            copied.append(dst)
            ep["urls"] = {**ep.get("urls", {}),
                          COVER_KEY: cover_relpath(slug, ep_id)}
        episodes.append(ep)

    return {**queue, "episodes": episodes}, copied


def prune_covers(index: dict, site_root: Path) -> list:
    """删掉 ``covers/`` 下没有被索引引用的 jpg。

    保留策略：**当前 index.json 引用到的那些，一张不多**。集的 id 变了、有人把
    记录从索引里摘掉、或者某个 slug 整个下线，多出来的封面就在这里回收。

    注意 ``merge`` 只增不删：4 集的批次重跑成 2 集时 ep03/ep04 仍在索引里，
    封面也该留着让页面继续显示 —— 那不是孤儿。
    """
    root = site_root / COVERS_DIRNAME
    if not root.is_dir():
        return []

    keep = set()
    for ep in index.get("episodes", []):
        url = ep.get("urls", {}).get(COVER_KEY)
        if isinstance(url, str) and url and not is_remote_url(url):
            keep.add((site_root / url).resolve())

    removed = []
    for path in sorted(root.rglob("*.jpg")):
        if path.resolve() not in keep:
            path.unlink()
            removed.append(path)

    # 空掉的 slug 目录一并收走，从深到浅
    for path in sorted(root.rglob("*"), reverse=True):
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    return removed


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
    p.add_argument("--site-root", default=None,
                   help="站点根目录，封面落到 <site-root>/covers/ 下。"
                        "默认取 --index 的上上级目录")
    args = p.parse_args(argv)

    queue_path = Path(args.queue)
    if not queue_path.is_file():
        print(f"[site-index] 找不到 {queue_path}", file=sys.stderr)
        return 1

    index_path = Path(args.index)
    site_root = Path(args.site_root) if args.site_root else index_path.parent.parent
    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        queue, copied = localize_covers(queue, queue_path, site_root)
        index = merge(load_index(index_path), queue)
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[site-index] 合并失败：{e}", file=sys.stderr)
        return 1

    write_index(index_path, index)
    removed = prune_covers(index, site_root)
    print(f"[site-index] {queue.get('slug')} 的 "
          f"{len(queue.get('episodes', []))} 集已合并，索引共 "
          f"{len(index['episodes'])} 集 → {index_path}")
    print(f"[site-index] 封面入站 {len(copied)} 张，回收孤儿封面 {len(removed)} 张")
    return 0


if __name__ == "__main__":
    sys.exit(main())
