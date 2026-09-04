#!/usr/bin/env python3
"""
种子池管理：供 agent / 子 agent 调用，把搜索发现的链接合并进种子文件。

设计原因：
  Pod 内直连搜索引擎均不可用（Bing 不收录抖音/好看视频页、百度返回风控页、
  DDG 超时、好看视频站内搜索重定向首页、相关推荐与主题无关）。
  搜索发现能力只存在于 OpenClaw 的 web_search 工具中，Python 侧无法调用。
  因此种子发现由 agent 侧完成，本模块只负责「合并 + 去重 + 持久化」这段确定性逻辑。

用法：
  python3 seed_manager.py add-douyin <url|id> [...]
  python3 seed_manager.py add-haokan <vid|url> [...]
  python3 seed_manager.py add-netease <vcode|url> [...]
  python3 seed_manager.py add-yicai <article-id|url> [...]
  python3 seed_manager.py add-douyin --stdin      # 从 stdin 读，每行一个
  python3 seed_manager.py stats
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
DOUYIN_SEEDS = BASE / "douyin_seeds.json"
HAOKAN_SEEDS = BASE / "haokan_seeds.json"
NETEASE_SEEDS = BASE / "netease_seeds.json"
YICAI_SEEDS = BASE / "yicai_seeds.json"

DOUYIN_ID_RE = re.compile(r"(\d{15,25})")
HAOKAN_VID_RE = re.compile(r"(\d{15,25})")
NETEASE_URL_RE = re.compile(r"/v/video/([A-Za-z0-9]{7,16})\.html", re.I)
NETEASE_VCODE_RE = re.compile(r"^[A-Za-z0-9]{7,16}$")
YICAI_URL_RE = re.compile(r"/video/(\d{6,12})\.html", re.I)
YICAI_ID_RE = re.compile(r"^\d{6,12}$")


def _load(path, key):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"_comment": "", "updated_at": "", key: []}


def _save(path, data, key):
    data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(data[key])


def normalize_douyin(raw):
    """接受完整 URL 或纯数字 id，统一输出标准视频页 URL。"""
    raw = raw.strip().strip('"\'')
    if not raw:
        return None
    m = DOUYIN_ID_RE.search(raw)
    if not m:
        return None
    return f"https://www.douyin.com/video/{m.group(1)}"


def normalize_haokan(raw):
    """接受完整 URL 或纯 vid，统一输出 vid 字符串。"""
    raw = raw.strip().strip('"\'')
    if not raw:
        return None
    m = HAOKAN_VID_RE.search(raw)
    return m.group(1) if m else None


def normalize_netease(raw):
    """接受网易播放页或 vcode，统一输出大写 vcode。"""
    raw = raw.strip().strip('"\'')
    if not raw:
        return None
    match = NETEASE_URL_RE.search(raw)
    value = match.group(1) if match else raw
    return value.upper() if NETEASE_VCODE_RE.fullmatch(value) else None


def normalize_yicai(raw):
    """接受第一财经播放页或文章数字 ID。"""
    raw = raw.strip().strip('"\'')
    if not raw:
        return None
    match = YICAI_URL_RE.search(raw)
    value = match.group(1) if match else raw
    return value if YICAI_ID_RE.fullmatch(value) else None


def add_seeds(kind, raw_items):
    if kind == "douyin":
        path, key, norm = DOUYIN_SEEDS, "urls", normalize_douyin
    elif kind == "haokan":
        path, key, norm = HAOKAN_SEEDS, "vids", normalize_haokan
    elif kind == "netease":
        path, key, norm = NETEASE_SEEDS, "vcodes", normalize_netease
    elif kind == "yicai":
        path, key, norm = YICAI_SEEDS, "ids", normalize_yicai
    else:
        raise ValueError(f"不支持的种子类型: {kind}")

    data = _load(path, key)
    existing = list(data.get(key, []))
    existing_ids = set()
    for entry in existing:
        normalized = norm(str(entry))
        if normalized:
            existing_ids.add(normalized)

    added, skipped, invalid = [], 0, 0
    for raw in raw_items:
        v = norm(raw)
        if not v:
            invalid += 1
            continue
        if v in existing_ids:
            skipped += 1
            continue
        existing_ids.add(v)
        existing.append(v)
        added.append(v)

    data[key] = existing
    total = _save(path, data, key)
    return {"added": added, "added_count": len(added), "skipped": skipped,
            "invalid": invalid, "total": total, "file": path.name}


def stats():
    out = {}
    for kind, path, key in (("douyin", DOUYIN_SEEDS, "urls"),
                            ("haokan", HAOKAN_SEEDS, "vids"),
                            ("netease", NETEASE_SEEDS, "vcodes"),
                            ("yicai", YICAI_SEEDS, "ids")):
        d = _load(path, key)
        out[kind] = {"count": len(d.get(key, [])), "updated_at": d.get("updated_at", "")}
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    args = sys.argv[2:]

    if cmd == "stats":
        for k, v in stats().items():
            print(f"{k:8} {v['count']:3} 个种子   更新于 {v['updated_at']}")
        return 0

    if cmd in ("add-douyin", "add-haokan", "add-netease", "add-yicai"):
        kind = cmd.removeprefix("add-")
        if args and args[0] == "--stdin":
            items = [ln for ln in sys.stdin.read().splitlines() if ln.strip()]
        else:
            items = args
        if not items:
            print("没有输入任何种子", file=sys.stderr)
            return 1
        r = add_seeds(kind, items)
        print(f"[{kind}] 新增 {r['added_count']}，重复跳过 {r['skipped']}，"
              f"无效 {r['invalid']}，当前共 {r['total']} 个 → {r['file']}")
        for a in r["added"]:
            print("  +", a)
        return 0

    print(f"未知命令: {cmd}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
