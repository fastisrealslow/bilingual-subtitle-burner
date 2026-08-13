#!/usr/bin/env python3
"""批量下载已抓到的视频直链。

为什么需要：部分平台（尤其微博）的 CDN 直链带 Expires，实测仅 1 小时有效。
光存链接没用，必须落盘成文件才算真正「拿到一手视频」。

用法：
  python3 fetch_videos.py                 # 下载股东大会现场类（默认）
  python3 fetch_videos.py --all           # 下载全部
  python3 fetch_videos.py --source douyin_video
  python3 fetch_videos.py --limit 10 --outdir videos
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

from common import UA_PC as UA, REFERER

BASE = Path(__file__).parent
DATA = BASE / "dashboard" / "data.json"

MEETING_KW = ("股东大会", "股东会")


def safe_name(s, maxlen=60):
    s = re.sub(r"[\\/:*?\"<>|\n\r\t]", "_", s).strip()
    return s[:maxlen] or "video"


def refresh_url(source, item_url, old_url, extra_vid=""):
    """直链过期时重新解析。

    抖音/微博的 CDN 地址都带时效，存库后会 403。
    这里根据来源类型现场重取一份新的。
    """
    try:
        if source == "douyin_video":
            m = re.search(r"/video/(\d{15,25})", item_url or "")
            if not m:
                return ""
            vid = m.group(1)
            ua = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
            req = urllib.request.Request(
                f"https://www.iesdouyin.com/share/video/{vid}/",
                headers={"User-Agent": ua, "Referer": "https://www.douyin.com/"})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            mm = re.search(r"_ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
            if not mm:
                return ""
            data = json.loads(mm.group(1))
            for _, v in (data.get("loaderData") or {}).items():
                if not isinstance(v, dict):
                    continue
                lst = ((v.get("videoInfoRes") or {}).get("item_list")) or []
                if lst:
                    urls = ((lst[0].get("video") or {}).get("play_addr") or {}).get("url_list") or []
                    if urls:
                        return urls[0].replace("/playwm/", "/play/")
        elif source == "tencent_live":
            # 腾讯 CDN 地址带 vkey 且会过期，但用 vid 可随时重新解析
            if not extra_vid:
                return ""
            u = (f"https://vv.video.qq.com/getinfo?vids={extra_vid}"
                 "&platform=101001&charge=0&otype=json&defn=shd")
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Referer": "https://v.qq.com/"})
            raw = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
            data = json.loads(re.sub(r"^QZOutputJson=|;$", "", raw.strip()))
            vi = (data.get("vl") or {}).get("vi") or []
            if not vi:
                return ""
            v = vi[0]
            fn, fk = v.get("fn", ""), v.get("fvkey", "")
            hosts = [x.get("url", "") for x in ((v.get("ul") or {}).get("ui") or []) if x.get("url")]
            if fn and hosts:
                return f"{hosts[0]}{fn}?vkey={fk}" if fk else f"{hosts[0]}{fn}"
        elif source == "weibo_search":
            # 微博需重跑搜索才能拿新链接（直链仅 1 小时有效）
            return ""
    except Exception:
        return ""
    return ""


def download(url, dest, referer=None, timeout=180):
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        total = int(r.headers.get("Content-Length") or 0)
        tmp = dest.with_suffix(dest.suffix + ".part")
        got = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
        tmp.rename(dest)
        return got, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="下载全部，而非仅股东大会类")
    ap.add_argument("--source", help="只下指定来源")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--outdir", default="videos")
    args = ap.parse_args()

    if not DATA.exists():
        print(f"找不到 {DATA}，请先运行 monitor_v2.py", file=sys.stderr)
        return 1

    items = json.loads(DATA.read_text(encoding="utf-8"))
    picked = []
    for x in items:
        e = x.get("extra") or {}
        url = e.get("mp4_url") or e.get("video_url") or ""
        if not url.startswith("http"):
            continue
        if args.source and x["source"] != args.source:
            continue
        if not args.all and not any(k in x["title"] for k in MEETING_KW):
            continue
        picked.append((x["source"], x["title"], url, x.get("url", ""), e.get("vid", "")))

    if args.limit:
        picked = picked[:args.limit]

    outdir = BASE / args.outdir
    outdir.mkdir(exist_ok=True)
    print(f"待下载 {len(picked)} 条 → {outdir}\n")

    ok = fail = skip = 0
    total_bytes = 0
    for i, (src, title, url, page_url, vid) in enumerate(picked, 1):
        dest = outdir / f"{safe_name(title)}__{src}.mp4"
        if dest.exists() and dest.stat().st_size > 100000:
            print(f"[{i}/{len(picked)}] ⏭  已存在 {dest.name[:52]}")
            skip += 1
            continue
        try:
            got, total = download(url, dest, REFERER.get(src))
            total_bytes += got
            print(f"[{i}/{len(picked)}] ✅ {got/1048576:6.2f}MB  {dest.name[:52]}")
            ok += 1
        except Exception as ex:
            # 直链可能已过期，尝试重新解析一次
            retried = False
            if "403" in str(ex) or "404" in str(ex):
                fresh = refresh_url(src, page_url, url, vid)
                if fresh:
                    try:
                        got, total = download(fresh, dest, REFERER.get(src))
                        total_bytes += got
                        print(f"[{i}/{len(picked)}] ✅ {got/1048576:6.2f}MB  {dest.name[:46]} （已刷新链接）")
                        ok += 1
                        retried = True
                    except Exception as ex2:
                        ex = ex2
            if not retried:
                print(f"[{i}/{len(picked)}] ❌ {type(ex).__name__}: {str(ex)[:40]}  {title[:34]}")
                fail += 1
        time.sleep(0.5)

    print(f"\n成功 {ok} · 跳过 {skip} · 失败 {fail} · 共 {total_bytes/1073741824:.2f}GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
