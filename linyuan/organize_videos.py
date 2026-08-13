#!/usr/bin/env python3
"""按场合把 videos/ 下的视频归类到子目录。

分类依据是标题关键词，优先级从高到低（一个视频只归一类）：
  01_股东大会现场 — 最有价值，二创素材的主要来源
  02_演讲         — 北大演讲、投资峰会等完整演讲
  03_专访访谈     — 媒体专访、会客厅对话
  04_短切片       — 5MB 以下的金句片段
  05_其他

用法：
  python3 organize_videos.py          # 预览（不动文件）
  python3 organize_videos.py --apply  # 实际移动
"""
import argparse
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).parent
SRC = BASE / "videos"

RULES = [
    ("01_股东大会现场", ("股东大会", "股东会")),
    ("02_演讲", ("演讲", "分享会", "峰会", "报告会", "新书发布")),
    ("03_专访访谈", ("专访", "访谈", "对话", "会客厅", "接受采访")),
]
SMALL_CLIP_MB = 5.0


def classify(path):
    name = path.name
    for folder, kws in RULES:
        if any(k in name for k in kws):
            return folder
    if path.stat().st_size < SMALL_CLIP_MB * 1048576:
        return "04_短切片"
    return "05_其他"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际执行移动")
    args = ap.parse_args()

    if not SRC.exists():
        print(f"找不到 {SRC}", file=sys.stderr)
        return 1

    files = [p for p in SRC.glob("*.mp4") if p.is_file()]
    if not files:
        print("videos/ 下没有 mp4")
        return 0

    plan = {}
    for p in files:
        plan.setdefault(classify(p), []).append(p)

    total = 0
    for folder in sorted(plan):
        items = sorted(plan[folder], key=lambda x: -x.stat().st_size)
        size = sum(x.stat().st_size for x in items)
        total += size
        print(f"\n{folder}  ({len(items)} 个, {size/1073741824:.2f}GB)")
        for p in items[:6]:
            print(f"    {p.stat().st_size/1048576:7.1f}MB  {p.name[:62]}")
        if len(items) > 6:
            print(f"    … 还有 {len(items)-6} 个")

    print(f"\n合计 {len(files)} 个 · {total/1073741824:.2f}GB")

    if not args.apply:
        print("\n（预览模式，加 --apply 实际移动）")
        return 0

    for folder, items in plan.items():
        d = SRC / folder
        d.mkdir(exist_ok=True)
        for p in items:
            target = d / p.name
            if target.exists():
                continue
            shutil.move(str(p), str(target))
    print("\n✅ 已整理完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
