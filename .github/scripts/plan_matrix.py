#!/usr/bin/env python3
"""把 workflow_dispatch 输入和 ``sources/*.json`` 收敛成同一份出片 matrix。

放在脚本里而不是内联进 YAML：这段有校验和默认值填充，内联写法既没法本地
跑也没法测。输出写进 ``$GITHUB_OUTPUT`` 的 ``matrix`` 键。
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "sources"
TRANSLATORS = ("deepseek-v3", "claude-sonnet-4.6")
DEFAULT_TRANSLATOR = "deepseek-v3"
COVER_CROP_RE = re.compile(r"^\d+:\d+:\d+:\d+$")
SUB_MODES = ("both", "zh-only")
DEFAULT_SUB_MODE = "both"
SUB_MARGIN_V_AUTO = "auto"


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

    # 手动封面时间点。matrix 里走字符串（空串 = 不指定），但「写了个非数字」
    # 要在 plan 阶段就验出来，否则得等 40 分钟的 runner 跑到最后一步才炸。
    cover_time = str(raw.get("cover_time_sec") or "").strip()
    if cover_time:
        try:
            valid = float(cover_time) >= 0
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                f"{origin}: cover_time_sec={cover_time!r} 无效，应为非负秒数")

    # 封面裁切（切掉源片烧死的英文硬字幕）。同样在 plan 阶段验格式。
    cover_crop = str(raw.get("cover_crop") or "").strip()
    if cover_crop and not COVER_CROP_RE.fullmatch(cover_crop):
        raise ValueError(
            f"{origin}: cover_crop={cover_crop!r} 无效，应为 W:H:X:Y（同 ffmpeg crop 滤镜）")

    # 字幕语种。zh-only 用于源片自带英文硬字幕的情况，只烧中文避免叠字。
    sub_mode = str(raw.get("sub_mode") or DEFAULT_SUB_MODE).strip()
    if sub_mode not in SUB_MODES:
        raise ValueError(
            f"{origin}: sub_mode={sub_mode!r} 无效，可选 {list(SUB_MODES)}")

    # auto 让 produce.py 逐条 cue 探测源片硬字幕带位置各自摆位，给整数则钉死。
    sub_margin_v = str(raw.get("sub_margin_v") or "").strip()
    if sub_margin_v and sub_margin_v != SUB_MARGIN_V_AUTO \
            and not re.fullmatch(r"\d+", sub_margin_v):
        raise ValueError(
            f"{origin}: sub_margin_v={sub_margin_v!r} 无效，"
            f"应为 {SUB_MARGIN_V_AUTO} 或非负整数像素")

    sub_avoid_gap = str(raw.get("sub_avoid_gap") or "").strip()
    if sub_avoid_gap and not re.fullmatch(r"\d+", sub_avoid_gap):
        raise ValueError(
            f"{origin}: sub_avoid_gap={sub_avoid_gap!r} 无效，应为非负整数像素")

    return {
        "source": source,
        "slug": slug,
        "title_override": str(raw.get("title_override") or ""),
        "translator": translator,
        # matrix 里统一成字符串，YAML 侧只做 "$DUAL" = "true" 的比较
        "dual": "true" if dual else "false",
        "cover_time_sec": cover_time,
        "cover_crop": cover_crop,
        "speaker": str(raw.get("speaker") or ""),
        "sub_mode": sub_mode,
        "sub_margin_v": sub_margin_v,
        "sub_avoid_gap": sub_avoid_gap,
    }


def from_dispatch(env: dict) -> list:
    return [normalize({
        "source": env.get("IN_SOURCE"),
        "slug": env.get("IN_SLUG"),
        "title_override": env.get("IN_TITLE"),
        "translator": env.get("IN_TRANSLATOR") or DEFAULT_TRANSLATOR,
        "dual": env.get("IN_DUAL"),
        "cover_time_sec": env.get("IN_COVER_TIME_SEC"),
        "cover_crop": env.get("IN_COVER_CROP"),
        "speaker": env.get("IN_SPEAKER"),
        "sub_mode": env.get("IN_SUB_MODE"),
        "sub_margin_v": env.get("IN_SUB_MARGIN_V"),
        "sub_avoid_gap": env.get("IN_SUB_AVOID_GAP"),
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
