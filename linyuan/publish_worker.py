#!/usr/bin/env python3
"""B站投稿 worker：把 CI 出好的成片投出去（在境内网络环境运行）。

为什么存在
----------
B站 upos 上传通道对海外数据中心 IP 物理断流（CI 实测：连接建立后零吞吐，
16 分钟 tcp_retries2 超时，MSS 钳制无效）。但境内阿里云实测 30MB/s 秒传。
所以投稿这一棒放在本机：轮询 Actions 上出好的成片 → 下载 → biliup 投稿。

工作方式
--------
stage_and_dispatch.py 触发 CI 时会把 {slug, source_url, title, delay_hours}
写进 .automation/pending.json。本 worker 定期（cron）：
  1. 查 produce-cn 最近成功的 run
  2. 找 pending 里 slug 对应的 deliver artifact
  3. 下载解压 → biliup 投稿（转载带 --source，否则 B站 21021 拒稿）
  4. 记录 published.json（slug → bvid），之后汇报
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
STATE_DIR = BASE / ".automation"
PENDING = STATE_DIR / "pending.json"
PUBLISHED = STATE_DIR / "published.json"
COOKIES = Path.home() / ".config" / "linyuan" / "bili_cookies.json"
TOKEN_FILE = Path.home() / ".config" / "linyuan" / "github_token"

REPO = "fastisrealslow/bilingual-subtitle-burner"
API = f"https://api.github.com/repos/{REPO}"
TID = 207          # 财经商业
COPYRIGHT = 2      # 转载（转载时 --source 必填，B站 code 21021）


def log(*a):
    print(f"[{datetime.now():%m-%d %H:%M:%S}]", *a, flush=True)


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save(path, obj):
    STATE_DIR.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def gh(token, path):
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def find_artifact(token, slug):
    """在最近成功的 produce-cn run 里找 deliver-<slug> artifact。"""
    runs = gh(token, "/actions/workflows/linyuan-produce-cn.yml/runs"
                     "?status=success&per_page=10").get("workflow_runs", [])
    for run in runs:
        arts = gh(token, f"/actions/runs/{run['id']}/artifacts"
                        ).get("artifacts", [])
        for a in arts:
            if a["name"] == f"deliver-{slug}" and not a.get("expired"):
                return a["archive_download_url"]
    return None


def download_artifact(token, url, dest):
    r = subprocess.run(
        ["curl", "-sfL", "--http1.1", "--max-time", "300", "--retry", "3",
         "-C", "-",                         # 断点续传：代理截断后重试接着下
         "-H", f"Authorization: Bearer {token}", "-o", str(dest), url],
        timeout=660)
    return r.returncode == 0 and dest.exists() and dest.stat().st_size > 100_000


def publish(item, video):
    title = item.get("title") or item["slug"]
    # 标题必须含「林园」（硬性要求），但改为前缀式而非「｜林园」后缀。
    # 2026-09-01 竞品实测：高播放标题是「林园：+原话金句」，我们的「…｜林园」
    # 后缀是第三人称摘要体，同期播放中位 23 vs 竞品 584~1956（差 33 倍）。
    if "林园" not in title:
        title = f"林园：{title}"
    title = title[:78]
    cmd = ["biliup", "-u", str(COOKIES), "upload", str(video),
           "--title", title, "--tid", str(TID),
           "--copyright", str(COPYRIGHT),
           "--source", item.get("source_url") or "https://www.bilibili.com",
           "--tag", "林园,价值投资", "--limit", "1"]
    delay = int(item.get("delay_hours") or 0)
    if delay > 0:
        dtime = int(time.time()) + delay * 3600      # 定时发布，必须 >4h
        cmd += ["--dtime", str(dtime)]
    log(f"  投稿: {title}" + (f"（定时 +{delay}h）" if delay else "（立即）"))
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r'"bvid":\s*String\("(\w+)"\)', out) or re.search(r'BV\w{10}', out)
    if r.returncode == 0 and '"code": 0' in out.replace('"code":0', '"code": 0'):
        return m.group(0) if m else ""
    tail = [ln for ln in out.splitlines() if "code" in ln or "Error" in ln][-2:]
    raise RuntimeError("; ".join(tail)[:200] or f"exit {r.returncode}")


def main():
    if not COOKIES.exists():
        sys.exit(f"缺 cookies：{COOKIES}")
    token = TOKEN_FILE.read_text().strip()
    pending = load(PENDING, [])
    published = load(PUBLISHED, {})
    if not pending:
        log("无待投稿件")
        return 0

    done = 0
    for item in list(pending):
        slug = item["slug"]
        if slug in published:
            pending.remove(item)
            continue
        log(f"处理 {slug} ...")
        url = find_artifact(token, slug)
        if not url:
            log("  artifact 还没出现（CI 仍在跑），下轮再看")
            continue
        zpath = STATE_DIR / f"{slug}.zip"
        if not download_artifact(token, url, zpath):
            log("  下载失败，下轮重试")
            continue
        ext = STATE_DIR / slug
        subprocess.run(["unzip", "-oq", str(zpath), "-d", str(ext)])
        video = ext / "final.mp4"
        if not video.exists():
            log("  artifact 里没有 final.mp4，跳过")
            continue
        try:
            bvid = publish(item, video)
        except Exception as e:
            log(f"  投稿失败：{e}")
            continue
        published[slug] = {"bvid": bvid, "ts": int(time.time()),
                           "title": item.get("title")}
        pending.remove(item)
        save(PUBLISHED, published)
        save(PENDING, pending)
        log(f"  ✅ 已投：https://www.bilibili.com/video/{bvid}")
        done += 1
        zpath.unlink(missing_ok=True)

    log(f"本轮投稿 {done} 条，队列剩 {len(pending)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
