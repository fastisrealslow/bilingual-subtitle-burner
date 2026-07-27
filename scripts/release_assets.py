#!/usr/bin/env python3
"""按 ``queue.json`` 把每集产物摊平成 Release 资产，并生成 Release 正文。

    python scripts/release_assets.py \
        --queue deliver/<slug>/queue.json --out _release --notes _release/notes.md

``deliver/<slug>/`` 下单集直接放 ``final.mp4``、多集放在 ``ep01/`` 子目录里，
Release 上则一律是扁平的 ``ep01.mp4`` / ``ep01_cover_16x9.jpg``。这一步只做
这层改名搬运，YAML 里就只剩一句 ``gh release upload --clobber``。

退出码：0 成功 / 1 输入有误或产物缺失
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

# 每集目录里的产物名 → queue.json ``files`` 里的键
LOCAL_NAMES = {
    "video": "final.mp4",
    "cover_16x9": "cover_16x9.jpg",
    "cover_9x16": "cover_9x16.jpg",
}


def episode_source_dir(queue_dir: Path, episode: dict) -> Path:
    """单集落在 slug 根目录，多集落在 ``ep01/`` —— 两种都认。"""
    sub = queue_dir / str(episode.get("id", ""))
    return sub if sub.is_dir() else queue_dir


def plan_assets(queue: dict, queue_path: Path) -> list:
    """列出 ``(本地路径, Release 上的资产名)``，含 queue.json 自己。"""
    queue_dir = queue_path.parent
    plan = []
    for ep in queue.get("episodes", []):
        src_dir = episode_source_dir(queue_dir, ep)
        for key, local in LOCAL_NAMES.items():
            asset = ep.get("files", {}).get(key)
            if not asset:
                raise ValueError(f"{ep.get('id')} 的 files 缺少 {key}")
            plan.append((src_dir / local, asset))
    plan.append((queue_path, queue_path.name))
    return plan


def render_notes(queue: dict) -> str:
    """Release 正文：逐集列出标题、时长、直链。"""
    lines = [f"# {queue.get('speaker', '')} · {queue.get('slug', '')}", "",
             f"共 {len(queue.get('episodes', []))} 集，"
             f"生成于 {queue.get('generated_at', '')}，"
             f"commit `{queue.get('commit', '')}`。", ""]
    for ep in queue.get("episodes", []):
        mins, secs = divmod(int(round(float(ep.get("duration_sec", 0)))), 60)
        lines.append(f"## {ep.get('id')} {ep.get('title', '')}")
        lines.append(f"- 时长：{mins:d} 分 {secs:02d} 秒")
        lines.append(f"- 建议发布日：{ep.get('scheduled_date', '')}")
        lines.append(f"- 视频：{ep.get('urls', {}).get('video', '')}")
        lines.append(f"- 封面 16:9：{ep.get('urls', {}).get('cover_16x9', '')}")
        lines.append(f"- 封面 9:16：{ep.get('urls', {}).get('cover_9x16', '')}")
        lines.append("")
    return "\n".join(lines)


def stage(plan: list, out_dir: Path) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for src, asset in plan:
        if not src.is_file():
            raise FileNotFoundError(f"缺少产物 {src}")
        dst = out_dir / asset
        shutil.copyfile(src, dst)
        staged.append(dst)
    return staged


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="release_assets.py",
        description="按 queue.json 摊平每集产物，供 gh release upload 使用")
    p.add_argument("--queue", required=True, help="queue.json 路径")
    p.add_argument("--out", required=True, help="资产暂存目录")
    p.add_argument("--notes", default=None, help="把 Release 正文写到这个文件")
    args = p.parse_args(argv)

    queue_path = Path(args.queue)
    if not queue_path.is_file():
        print(f"[release] 找不到 {queue_path}", file=sys.stderr)
        return 1

    try:
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        staged = stage(plan_assets(queue, queue_path), Path(args.out))
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"[release] 准备资产失败：{e}", file=sys.stderr)
        return 1

    if args.notes:
        notes = Path(args.notes)
        notes.parent.mkdir(parents=True, exist_ok=True)
        notes.write_text(render_notes(queue), encoding="utf-8")

    print(f"[release] {len(staged)} 个资产已就位 → {args.out}")
    for path in staged:
        print(f"  {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
