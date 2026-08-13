#!/usr/bin/env python3
"""桥接：把 linyuan-poc 监控到的视频，喂给 bilingual-subtitle-burner 出片。

两个项目的接缝
--------------
linyuan-poc 产出 ``videos/**/*.mp4`` + ``dashboard/data.json``；
bilingual-subtitle-burner 的 ``produce.py`` 吃 ``--source <路径或URL> --slug <名字>``。
这个脚本负责在中间选片、排序、去重，并生成可直接执行的出片命令。

为什么喂本地文件而不是 URL
--------------------------
``produce.py`` 取源走裸 yt-dlp。我们最有价值的素材是 B站原片，而 B站对
数据中心 IP 直接 412（linyuan-poc 是绕 API 拿的流，produce.py 没有这条路）。
本地文件已经在磁盘上，``--source`` 支持本地路径，直接喂路径最稳。

选片优先级
----------
1. 股东大会现场 —— 一手素材，二创的源头
2. 长视频（≥20 分钟）—— 完整演讲/访谈，够切 3 段金句
3. 专访访谈
短切片不选：produce.py 要从中挑 3 段金句拼成 3 分钟成片，源太短会退 2。

用法
----
    python3 bridge_produce.py                    # 列出候选（不执行）
    python3 bridge_produce.py --emit-json out.json   # 生成批量任务 JSON
    python3 bridge_produce.py --run --limit 1    # 实际调用 produce.py
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
VIDEOS = BASE / "videos"
DATA = BASE / "dashboard" / "data.json"
STATE = BASE / "produced.json"          # 已出片记录，避免重复烧钱

SPEAKER = "林园"
# 林园视频是中文源。produce.py 默认 --language en / direction en2zh，
# 中文源必须显式改过来（见下方 check_burner_support）。
LANGUAGE = "zh"
DIRECTION = "zh2en"

# 目录优先级：数字越小越先出片
DIR_PRIORITY = {
    "01_股东大会现场": 1,
    "02_演讲": 2,
    "03_专访访谈": 3,
    "06_B站原片": 4,
    "05_其他": 5,
}
SKIP_DIRS = {"04_短切片"}
MIN_DURATION_SEC = 480      # 8 分钟。produce.py 要挑 3 段金句拼成 ~3 分钟成片，
                            # 8 分钟源够用；再短就会在 highlight 阶段退 2。
                            # 门槛从 600 降下来是因为同仁堂股东会只有 9:11，
                            # 而股东大会现场是优先级最高的一手素材。
MIN_SIZE_MB = 8


def load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {"produced": []}


def save_state(state):
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def slugify(name, category, used):
    """产物目录名：只允许字母数字 . _ -（produce.py 的硬约束）。

    中文标题不能直接当 slug，改用「linyuan-<平台>-<原始ID>」，
    这样出片目录能一眼对回监控库里的那条源。
    """
    plat, ident = "", ""
    m = re.search(r"__(BV[A-Za-z0-9]+)\.mp4$", name)          # B站原片
    if m:
        plat, ident = "bili", m.group(1)
    else:
        mp = re.search(r"__(douyin|haokan|netease|tencent|weibo)", name)
        if mp:
            plat = {"douyin": "dy", "haokan": "hk", "netease": "wy",
                    "tencent": "tx", "weibo": "wb"}[mp.group(1)]
        mi = re.search(r"(\d{15,25})", name)                   # 抖音/好看 的长数字 ID
        if mi:
            ident = mi.group(1)[-10:]
    if not ident:
        # 没有可用 ID 时用文件名摩尔哈希，保证同一文件每次算出同一个 slug
        import hashlib
        ident = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    tag = {"01_股东大会现场": "sh", "02_演讲": "sp",
           "03_专访访谈": "iv"}.get(category, "")
    parts = ["linyuan"] + [p for p in (tag, plat, ident) if p]
    base = "-".join(parts)
    slug = base
    i = 2
    while slug in used:
        slug = f"{base}-{i}"
        i += 1
    used.add(slug)
    return slug


def probe_duration(path):
    """没有 ffprobe 时退回用文件大小粗估，只用于排序和过滤。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def collect(state):
    if not VIDEOS.exists():
        print(f"找不到 {VIDEOS}，请先跑 monitor_v2.py + fetch_videos.py",
              file=sys.stderr)
        return []

    done = set(state.get("produced", []))
    used_slugs = set()
    items = []
    for mp4 in sorted(VIDEOS.rglob("*.mp4")):
        cat = mp4.parent.name
        if cat in SKIP_DIRS:
            continue
        size_mb = mp4.stat().st_size / 1048576
        if size_mb < MIN_SIZE_MB:
            continue
        key = mp4.name
        if key in done:
            continue
        dur = probe_duration(mp4)
        if dur and dur < MIN_DURATION_SEC:
            continue
        items.append({
            "path": mp4,
            "category": cat,
            "priority": DIR_PRIORITY.get(cat, 9),
            "size_mb": round(size_mb, 1),
            "duration_sec": round(dur, 1),
            "title": mp4.stem.split("__")[0],
        })

    items.sort(key=lambda x: (x["priority"], -x["size_mb"]))
    for it in items:
        it["slug"] = slugify(it["path"].name, it["category"], used_slugs)
    return items


def build_command(item):
    """生成 produce_cn.py 命令。

    合并后直接调同目录的 produce_cn.py（原生支持中文源，
    不需要 burner 的 produce.py 那套 --direction 补丁）。
    """
    cmd = [sys.executable, str(BASE / "produce_cn.py"),
           "--source", str(item["path"]),
           "--slug", item["slug"],
           "--speaker", SPEAKER]
    if item.get("occasion"):
        cmd += ["--occasion", item["occasion"]]
    return cmd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只取前 N 条")
    ap.add_argument("--emit-json", metavar="FILE",
                    help="生成 burner 的 sources/*.json 批量任务")
    ap.add_argument("--run", action="store_true", help="实际执行 produce.py")
    args = ap.parse_args()

    state = load_state()
    items = collect(state)
    if args.limit:
        items = items[:args.limit]

    if not items:
        print("没有待出片的候选（可能都已出过，见 produced.json）")
        return 0

    print(f"{'优先级':<6}{'分类':<16}{'时长':>8}{'大小':>9}  slug / 标题")
    print("-" * 78)
    for it in items:
        dur = f"{int(it['duration_sec'])//60}:{int(it['duration_sec'])%60:02d}" \
              if it["duration_sec"] else "—"
        print(f"{it['priority']:<6}{it['category']:<16}{dur:>8}"
              f"{it['size_mb']:>7.0f}MB  {it['slug']}")
        print(f"{'':30}{it['title'][:56]}")
    print(f"\n共 {len(items)} 条候选")

    if args.emit_json:
        # 直接生成 produce_cn.py 可用的任务清单
        jobs = [{"source": str(it["path"]), "slug": it["slug"],
                 "speaker": SPEAKER, "occasion": it.get("occasion", "")}
                for it in items]
        Path(args.emit_json).write_text(
            json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已写入 {args.emit_json}（{len(jobs)} 条任务）")

    if not args.run:
        print("\n未加 --run，仅列出。手动执行示例：")
        if items:
            print("  " + " ".join(build_command(items[0])))
        return 0

    succeeded = 0
    for i, it in enumerate(items, 1):
        cmd = build_command(it)
        print(f"\n[{i}/{len(items)}] {it['slug']} ← {it['title'][:40]}")
        r = subprocess.run(cmd, cwd=BASE)
        if r.returncode == 0:
            state.setdefault("produced", []).append(it["path"].name)
            save_state(state)
            succeeded += 1
        else:
            note = {0: "成功", 1: "配置错误", 2: "内容不达标",
                    3: "外部依赖失败"}.get(r.returncode, "未知")
            print(f"  退出码 {r.returncode}（{note}）")
            if r.returncode == 2:      # 内容不达标，重试无意，记下别再选
                state.setdefault("produced", []).append(it["path"].name)
                save_state(state)
    print(f"\n出片成功 {succeeded}/{len(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
