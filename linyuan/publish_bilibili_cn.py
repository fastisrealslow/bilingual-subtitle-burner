#!/usr/bin/env python3
"""中文 pipeline 的 B 站投稿：从 deliver/<slug>/ 直接投。

与英文 pipeline 的区别：
- 英文：queue.json + episodes 结构，支持多集
- 中文：单个 slug 目录，一次出一集，直接投

用法：
    python3 linyuan/publish_bilibili_cn.py --slug <slug> [--dry-run]

环境变量：
    BILIBILI_COOKIES: biliup cookies.json 路径或内容
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_TID = 207  # 财经商业
DEFAULT_COPYRIGHT = 2  # 转载（翻译剪辑）
DEFAULT_LINE = "bda2"


def main():
    ap = argparse.ArgumentParser(description="中文 pipeline B 站投稿")
    ap.add_argument("--slug", required=True, help="slug，如 ly-yiyao-0813")
    ap.add_argument("--cookies", default=None, help="cookies.json 路径")
    ap.add_argument("--tid", type=int, default=DEFAULT_TID, help="分区 id")
    ap.add_argument("--copyright", type=int, default=DEFAULT_COPYRIGHT, choices=[1, 2])
    ap.add_argument("--line", default=DEFAULT_LINE)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # 定位产物
    base = Path(__file__).parent / "deliver" / args.slug
    video = base / "final.mp4"
    cover = base / "cover_16x9.jpg"  # produce_cn.py 不生成封面，暂用视频首帧
    meta = base / "meta.json"

    if not video.is_file():
        print(f"❌ 找不到成片 {video}", file=sys.stderr)
        return 1

    # 从 meta.json 或 slug 生成标题/描述
    # meta.json 没有 title/tags，需要从 slug 推断
    if meta.is_file():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            occasion = m.get("occasion", "")
            speaker = m.get("speaker", "林园")
            segments = m.get("segments", [])
            # 生成标题：场合 + 主讲人
            title = f"{occasion}｜{speaker}" if occasion else f"{speaker}发言"
            # 生成描述：段落摘要
            desc_parts = [f"- {s.get('reason', '')}" for s in segments[:3]]
            desc = f"精选段落：\n" + "\n".join(desc_parts) if desc_parts else ""
            # 标签：从 occasion 提取关键词
            tags = ["林园", "价值投资"]
            if "股东大会" in occasion:
                tags.append("股东大会")
            if "片仔癀" in occasion:
                tags.append("片仔癀")
        except Exception as e:
            print(f"⚠️  读 meta.json 失败：{e}", file=sys.stderr)
            title, desc, tags = args.slug, "", ["林园"]
    else:
        title, desc, tags = args.slug, "", ["林园"]

    # cookies
    raw_cookies = (args.cookies or os.environ.get("BILIBILI_COOKIES") or "").strip()
    if not raw_cookies:
        print(f"❌ 缺少 BILIBILI_COOKIES", file=sys.stderr)
        return 1
    cookies = Path(raw_cookies)
    if not cookies.is_file():
        print(f"❌ cookie 文件不存在：{cookies}", file=sys.stderr)
        return 1

    # 拼 biliup 命令
    cmd = ["biliup", "-u", str(cookies), "upload", str(video),
           "--title", title,
           "--tid", str(args.tid),
           "--desc", desc,
           "--copyright", str(args.copyright),
           "--line", args.line]
    for tag in tags:
        cmd += ["--tag", tag]

    if args.dry_run:
        print("【dry-run】将执行：")
        print(" ".join(cmd))
        return 0

    print(f"📤 投稿：{title}")
    print(f"   视频：{video}")
    print(f"   大小：{video.stat().st_size / 1024 / 1024:.1f} MB")
    r = subprocess.run(cmd)
    if r.returncode == 0:
        print("✅ 投稿成功")
    else:
        print(f"❌ 投稿失败（exit {r.returncode}）", file=sys.stderr)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
