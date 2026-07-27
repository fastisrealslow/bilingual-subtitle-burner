#!/usr/bin/env python3
"""把 workflow_dispatch 输入和 ``sources/*.json`` 收敛成同一份出片 matrix。

放在脚本里而不是内联进 YAML：这段有校验和默认值填充，内联写法既没法本地
跑也没法测。输出写进 ``$GITHUB_OUTPUT`` 的 ``matrix`` 键。
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "sources"
TRANSLATORS = ("deepseek-v3", "claude-sonnet-4.6")
DEFAULT_TRANSLATOR = "deepseek-v3"


def normalize(raw: dict, origin: str) -> dict:
    """校验一条任务并补默认值。缺 source/slug 直接报错，不要静默跳过。"""
    source = str(raw.get("source") or "").strip()
    slug = str(raw.get("slug") or "").strip()
    if not source or not slug:
        raise ValueError(f"{origin}: 必须同时提供 source 和 slug")

    translator = str(raw.get("translator") or DEFAULT_TRANSLATOR).strip()
    if translator not in TRANSLATORS:
        raise ValueError(
            f"{origin}: translator={translator!r} 无效，可选 {list(TRANSLATORS)}")

    dual = raw.get("dual", False)
    if isinstance(dual, str):
        dual = dual.strip().lower() == "true"

    return {
        "source": source,
        "slug": slug,
        "title_override": str(raw.get("title_override") or ""),
        "translator": translator,
        # matrix 里统一成字符串，YAML 侧只做 "$DUAL" = "true" 的比较
        "dual": "true" if dual else "false",
    }


def from_dispatch(env: dict) -> list:
    return [normalize({
        "source": env.get("IN_SOURCE"),
        "slug": env.get("IN_SLUG"),
        "title_override": env.get("IN_TITLE"),
        "translator": env.get("IN_TRANSLATOR") or DEFAULT_TRANSLATOR,
        "dual": env.get("IN_DUAL"),
    }, "workflow_dispatch")]


def from_sources(sources_dir: Path) -> list:
    jobs = []
    for path in sorted(sources_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        for item in (raw if isinstance(raw, list) else [raw]):
            jobs.append(normalize(item, path.name))

    slugs = [j["slug"] for j in jobs]
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    if dupes:
        # 同名 slug 会让两个 job 抢同一个 artifact 名字，先拦下来
        raise ValueError(f"sources/ 里 slug 重复：{dupes}")
    return jobs


def build(env: dict, sources_dir: Path = SOURCES_DIR) -> list:
    if env.get("EVENT") == "workflow_dispatch":
        return from_dispatch(env)
    return from_sources(sources_dir)


def main() -> int:
    try:
        jobs = build(os.environ)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"::error::出片任务配置有误：{e}", file=sys.stderr)
        return 1

    matrix = json.dumps(jobs, ensure_ascii=False)
    print(f"共 {len(jobs)} 条出片任务：{matrix}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"matrix={matrix}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
