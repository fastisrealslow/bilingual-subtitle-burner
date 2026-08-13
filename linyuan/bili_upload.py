#!/usr/bin/env python3
"""投稿：把 deliver/<slug>/ 的成片发到 B站。

依赖 bili_login.py 产出的 cookies.json（一次性登录，有效期数月）。

安全默认值（都可以用参数关掉，但默认保守）：
  · 定时发布，而非立即公开 —— 给你留检查和撤回的时间
  · 已投过的记进 uploaded.json，绝不重复投
  · 投稿前强制校验登录态，失效直接退出而不是投一半

用法：
    python3 bili_upload.py --slug tongrentang --dry-run   # 只打印将要执行的命令
    python3 bili_upload.py --slug tongrentang             # 定时发布（默认 +4h）
    python3 bili_upload.py --slug tongrentang --now       # 立即公开
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
DELIVER = BASE / "deliver"
COOKIES = BASE / "cookies.json"
STATE = BASE / "uploaded.json"
BILIUP = "/home/node/.local/bin/biliup"

# 208 = 知识/财经，林园这类投资内容归这里
DEFAULT_TID = 208
DEFAULT_TAGS = ["价值投资", "投资", "林园", "股市", "财经"]
# B站要求定时发布距提交 > 4 小时
MIN_SCHEDULE_HOURS = 4.2


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"uploaded": []}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_login():
    r = subprocess.run([sys.executable, str(BASE / "bili_login.py"), "--check"],
                       capture_output=True, text=True)
    print(r.stdout.strip())
    if r.returncode != 0:
        sys.exit("登录态无效。先跑：python3 bili_login.py")


def build_desc(meta):
    """简介：说明素材出处和处理方式，不夸大。"""
    occasion = meta.get("occasion", "")
    lines = []
    if occasion:
        lines.append(f"素材来源：{occasion}")
    lines.append("字幕由语音识别生成后人工校对，中英双语。")
    lines.append("")
    lines.append("本视频仅作信息分享，不构成任何投资建议。")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True, help="deliver/ 下的目录名")
    ap.add_argument("--title", help="标题，默认取 meta.json")
    ap.add_argument("--tid", type=int, default=DEFAULT_TID, help="分区 ID")
    ap.add_argument("--tags", default=",".join(DEFAULT_TAGS))
    ap.add_argument("--cover", help="封面图路径")
    ap.add_argument("--now", action="store_true",
                    help="立即公开（默认改为定时发布，留检查时间）")
    ap.add_argument("--delay-hours", type=float, default=MIN_SCHEDULE_HOURS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="忽略已投记录")
    args = ap.parse_args()

    d = DELIVER / args.slug
    video = d / "final.mp4"
    if not video.is_file():
        sys.exit(f"找不到成片：{video}")

    meta = {}
    mp = d / "meta.json"
    if mp.exists():
        meta = json.loads(mp.read_text(encoding="utf-8"))

    state = load_state()
    if args.slug in state["uploaded"] and not args.force:
        print(f"⏭  {args.slug} 已投过（见 uploaded.json），跳过。"
              f"要重投加 --force")
        return 0

    title = args.title or meta.get("title") or f"林园谈投资 · {args.slug}"
    if len(title) > 80:
        sys.exit(f"标题超 80 字：{len(title)}")

    cmd = [BILIUP, "-u", str(COOKIES), "upload", str(video),
           "--tid", str(args.tid),
           "--title", title,
           "--desc", build_desc(meta),
           "--tag", args.tags,
           "--copyright", "2",              # 2=转载：素材非自己拍摄
           "--source", meta.get("source_url") or meta.get("occasion", "网络"),
           ]
    cover = args.cover or (d / "cover.jpg" if (d / "cover.jpg").exists() else None)
    if cover:
        cmd += ["--cover", str(cover)]
    if not args.now:
        dtime = int(time.time() + args.delay_hours * 3600)
        cmd += ["--dtime", str(dtime)]

    size_mb = video.stat().st_size / 1048576
    print(f"\n稿件：{title}")
    print(f"文件：{video.name}  {size_mb:.1f}MB")
    print(f"分区：{args.tid}   标签：{args.tags}")
    print(f"发布：{'立即公开' if args.now else f'定时 +{args.delay_hours}h'}")
    print(f"授权：转载（素材非原创拍摄）\n")

    if args.dry_run:
        print("[dry-run] 将执行：")
        print("  " + " ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd))
        return 0

    ensure_login()
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"\n❌ 投稿失败，退出码 {r.returncode}", file=sys.stderr)
        return r.returncode
    state["uploaded"].append(args.slug)
    save_state(state)
    print(f"\n✅ 已提交。{'' if args.now else '定时发布前可在创作中心撤回。'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
