#!/usr/bin/env python3
"""每天 N 条「自动出片 + 投稿」的本机调度器（cron 驱动）。

为什么需要本机这一环
--------------------
微博/B站 CDN 都封数据中心 IP（微博 403、B站 412，CI 实测），视频字节只能在
境内网络下载。所以整条链分两段：

    本机（境内）  monitor 抓完的 data.json → 挑 N 条 → 下载视频
                  → 传 GitHub Release（staging 中转）→ dispatch CI
    CI（GitHub）  取源 → ASR → 金句 → 翻译 → 烧录 → 投稿 B站
                  （投稿需已配置 BILIBILI_COOKIES secret）

依赖的凭据（都在本机仓库外，不进 git）：
    ~/.config/linyuan/github_token   — PAT，需 repo + actions 权限
    B站 cookies 不在本机用，投稿发生在 CI 侧。

用法：
    python3 stage_and_dispatch.py --max 3            # 正式跑
    python3 stage_and_dispatch.py --max 3 --dry-run  # 只看选片结果
"""
import argparse
import base64
import hashlib
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
STATE_DIR = BASE / ".automation"            # 本机状态，已 gitignore
STATE = STATE_DIR / "dispatched.json"
TOKEN_FILE = Path.home() / ".config" / "linyuan" / "github_token"

REPO = "fastisrealslow/bilingual-subtitle-burner"
API = f"https://api.github.com/repos/{REPO}"
RELEASE_TAG = "staging"

# 选片约束：太短挑不出 3 段金句，太长 CI 的 4 核 ASR 拖不起
MIN_DUR, MAX_DUR = 120, 1200
# B站定时发布：第 i 条依次往后推（小时）。B站要求定时必须明显晚于当前时间
DELAY_LADDER = [4, 7, 10, 13, 16]


def log(*a):
    print(f"[{datetime.now():%H:%M:%S}]", *a, flush=True)


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"dispatched": []}


def save_state(st):
    STATE_DIR.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def gh(token, method, path, payload=None, raw_body=None, ctype="application/json"):
    data = raw_body if raw_body is not None else (
        json.dumps(payload).encode() if payload is not None else None)
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=120) as r:
        body = r.read()
        return json.loads(body.decode()) if body else {}


def fetch_items(token):
    """从 API 拿最新 data.json。

    必须走 API 而不是 git pull —— 本机代理会缓存 git smart-HTTP 的 refs
    通告，pull 到的可能是几小时前的旧数据（实测踩过）。
    用 raw accept 而不是默认的 base64 content —— 文件 ~1MB，base64 多走
    三分之一流量，被代理截断过一次（IncompleteRead）。加重试。
    """
    # urllib 走代理大响应必被截（~630KB 阈值，实测三次全挂）；
    # curl --compressed 走 gzip（体积降到 1/10）+ 自带重试，稳。
    r = subprocess.run(
        ["curl", "-sfL", "--http1.1", "--compressed", "--max-time", "120",
         "--retry", "3", "--retry-all-errors",
         "-H", f"Authorization: Bearer {token}",
         "-H", "Accept: application/vnd.github.raw",
         f"{API}/contents/linyuan/dashboard/data.json?ref=main"],
        capture_output=True, timeout=400)
    if r.returncode != 0:
        raise RuntimeError(f"拉取 data.json 失败：curl exit {r.returncode}")
    j = json.loads(r.stdout.decode(), strict=False)  # 抓取的标题带控制字符
    return j if isinstance(j, list) else j.get("items", [])


def pick(items, state, n):
    """挑 N 条候选：有视频直链、时长合适、没调度过，新的优先。"""
    done = set(state.get("dispatched", [])) | set(state.get("rejected", []))
    cands = []
    for it in items:
        url = it.get("video_url") or ""
        src_page = it.get("url") or ""
        if not url and "bilibili.com/video/" not in src_page:
            continue                                  # 没有可下载的字节
        key = it.get("id") or src_page or url
        if not key or key in done:
            continue
        cands.append({
            "key": key,
            "title": it.get("title", "")[:60],
            "video_url": url,
            "page_url": src_page,
            "source": it.get("source", ""),
            "publish_time": it.get("publish_time", ""),
        })
    cands.sort(key=lambda c: c["publish_time"], reverse=True)   # 时效优先
    return cands[:n]


def download(cand, workdir):
    """下载视频字节。微博直链 curl；B站复用 fetch_bilibili 的 API 绕风控
    （本机也是数据中心 IP，裸 yt-dlp 直连播放页会被 412 —— 不能偷懒）。"""
    workdir.mkdir(parents=True, exist_ok=True)
    out = workdir / f"{cand['slug']}.mp4"
    if cand["video_url"]:
        r = subprocess.run(["curl", "-sL", "--max-time", "300", "-o", str(out),
                            cand["video_url"]])
        ok = r.returncode == 0
    else:
        ok = False
        m = re.search(r"(BV\w+)", cand["page_url"])
        if m:
            try:
                from fetch_bilibili import get_streams, dl
                vurl, aurl = get_streams(m.group(1))
                dl(vurl, out, referer=cand["page_url"])
                if aurl:                        # dash 分离流：补音轨
                    apath = out.with_suffix(".m4a")
                    dl(aurl, apath, referer=cand["page_url"])
                    merged = out.with_suffix(".merged.mp4")
                    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                                    "-i", str(out), "-i", str(apath),
                                    "-c", "copy", str(merged)], check=True)
                    merged.rename(out)
                    apath.unlink()
                ok = True
            except Exception as e:
                log(f"    B站取流失败：{e}")
    if not ok or not out.exists() or out.stat().st_size < 100_000:
        return None
    # 时长校验
    d = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(out)],
                       capture_output=True, text=True).stdout.strip()
    try:
        dur = float(d)
    except ValueError:
        return None
    if not (MIN_DUR <= dur <= MAX_DUR):
        log(f"    时长 {dur:.0f}s 不在 [{MIN_DUR},{MAX_DUR}]，跳过")
        return None
    cand["duration"] = dur
    return out


def get_or_create_staging_release(token):
    try:
        return gh(token, "GET", f"/releases/tags/{RELEASE_TAG}")["id"]
    except Exception:
        rel = gh(token, "POST", "/releases", {
            "tag_name": RELEASE_TAG, "target_commitish": "main",
            "name": "Staging 素材中转",
            "body": "自动调度上传的源视频，供 CI 取源。定期清理。",
            "prerelease": True})
        return rel["id"]


def upload_asset(token, rel_id, path):
    name = path.name
    # 同名 asset 先删，否则 422
    assets = gh(token, "GET", f"/releases/{rel_id}/assets?per_page=100")
    for a in (assets if isinstance(assets, list) else assets.get("assets", [])):
        if a["name"] == name:
            gh(token, "DELETE", f"/releases/assets/{a['id']}")
    up_url = f"https://uploads.github.com/repos/{REPO}/releases/{rel_id}/assets?name={name}"
    req = urllib.request.Request(up_url, data=path.read_bytes(), method="POST", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "video/mp4"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.loads(r.read().decode())
    assert d.get("state") == "uploaded", d
    return f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}/{name}"


def cleanup_assets(token, rel_id, keep=10):
    """staging 不是网盘：只留最近 keep 个，防体积膨胀。"""
    assets = gh(token, "GET", f"/releases/{rel_id}/assets?per_page=100")
    if isinstance(assets, dict):
        assets = assets.get("assets", [])
    assets.sort(key=lambda a: a["created_at"], reverse=True)
    for a in assets[keep:]:
        gh(token, "DELETE", f"/releases/assets/{a['id']}")
        log(f"  清理旧 asset: {a['name']}")


def dispatch(token, cand, asset_url, delay_hours):
    slug = cand["slug"]
    gh(token, "POST", "/actions/workflows/linyuan-produce-cn.yml/dispatches", {
        "ref": "main",
        "inputs": {"source": asset_url, "slug": slug,
                   "speaker": "林园", "occasion": cand["title"][:30],
                   "delay_hours": str(delay_hours)}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=3, help="每天最多出几条")
    ap.add_argument("--dry-run", action="store_true", help="只选片，不下载不调度")
    args = ap.parse_args()

    if not TOKEN_FILE.exists():
        sys.exit(f"缺 token：把 GitHub PAT 写到 {TOKEN_FILE}（chmod 600）")
    token = TOKEN_FILE.read_text().strip()

    state = load_state()
    items = fetch_items(token)
    cands = pick(items, state, args.max)
    log(f"候选 {len(cands)} 条（目标 {args.max} 条）")

    for i, c in enumerate(cands):
        h = hashlib.md5(c["key"].encode()).hexdigest()[:6]
        c["slug"] = f"ly-{datetime.now():%m%d}-{h}"
        delay = DELAY_LADDER[i] if i < len(DELAY_LADDER) else DELAY_LADDER[-1]
        log(f"[{i+1}/{len(cands)}] {c['title']}  （{c['source']}，定时 +{delay}h）")
        if args.dry_run:
            continue

        path = download(c, STATE_DIR / "src")
        if not path:
            # 不合格的也记下来（太短/下不了），否则明天还会选中它
            state.setdefault("rejected", []).append(c["key"])
            save_state(state)
            log("    下载失败或不合格，已标记跳过")
            continue
        log(f"    已下载 {path.stat().st_size/1048576:.1f}MB / {c['duration']:.0f}s")

        rel_id = get_or_create_staging_release(token)
        asset_url = upload_asset(token, rel_id, path)
        log(f"    已传 staging release")

        dispatch(token, c, asset_url, delay)
        state.setdefault("dispatched", []).append(c["key"])
        save_state(state)
        log(f"    已 dispatch（slug={c['slug']}）")
        path.unlink()                                # 本地不留视频
        time.sleep(5)                                # 别密集打 API

    if not args.dry_run and cands:
        try:
            cleanup_assets(token, get_or_create_staging_release(token))
        except Exception as e:
            log(f"  清理旧 asset 失败（不致命）：{e}")
    log("完成")


if __name__ == "__main__":
    main()
