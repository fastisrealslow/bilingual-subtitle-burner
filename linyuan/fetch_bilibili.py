#!/usr/bin/env python3
"""下载 B站视频（UP主「园园滚雪球」的完整原始素材）。

为什么要下 B站：
  该 UP 主的长视频（20分钟以上）本身就是完整的演讲/访谈/股东大会原始录像，
  不是切片。这些内容在其他平台往往找不到，直接下 B站是最快的补齐方式。

技术路径：
  播放页 window.__playinfo__ 里有 DASH 音视频流地址（无需登录即可拿到 480P/360P）。
  下载 video+audio 两条流后用 ffmpeg 合并。
  必须带 Referer: bilibili.com，否则 403。

用法：
  python3 fetch_bilibili.py --list                 # 只列出待下载
  python3 fetch_bilibili.py --min-dur 1200         # 下载 20 分钟以上的
  python3 fetch_bilibili.py --limit 5 --outdir videos/06_B站原片
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import os

from common import UA_PC as UA

BASE = Path(__file__).parent
LIST_JSON = BASE / os.environ.get("BILI_LIST", "up_videos.json")

_OPENER = None


def opener():
    """带 buvid 指纹的会话。直请求播放页会 412，必须先拿 cookie。"""
    global _OPENER
    if _OPENER is not None:
        return _OPENER
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9"),
                     ("Referer", "https://www.bilibili.com/")]
    try:
        op.open("https://www.bilibili.com/", timeout=20).read()
        spi = json.loads(op.open("https://api.bilibili.com/x/frontend/finger/spi",
                                 timeout=20).read().decode())
        for n, val in (("buvid3", spi["data"]["b_3"]), ("buvid4", spi["data"]["b_4"])):
            jar.set_cookie(http.cookiejar.Cookie(
                0, n, val, None, False, ".bilibili.com", True, False,
                "/", True, False, None, False, None, None, {}))
    except Exception as e:
        # cookie 初始化失败不致命（部分接口仍可用），但要告知，否则后续 412 很难排查
        print(f"[warn] buvid cookie 获取失败: {type(e).__name__}，可能触发 412", file=sys.stderr)
    _OPENER = op
    return op


def http(url, referer="https://www.bilibili.com/", timeout=60):
    op = opener()
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": referer,
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    return op.open(req, timeout=timeout)


def to_sec(d):
    if isinstance(d, int):
        return d
    parts = str(d).split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return int(parts[0])
    except Exception:
        return 0


def safe(s, n=64):
    return (re.sub(r'[\\/:*?"<>|\n\r\t]', "_", s).strip() or "video")[:n]


def get_streams(bvid):
    """取播放地址。

    不走播放页（直请求 412 风控），改走 API：
      view 拿 cid → playurl 拿 durl（html5 平台返回单文件 MP4，无需合并）
    """
    op = opener()
    v = json.loads(op.open(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=25
    ).read().decode("utf-8", "ignore"))
    if v.get("code") != 0:
        raise RuntimeError(f"view code={v.get('code')} {v.get('message')}")
    cid = v["data"]["cid"]

    p = json.loads(op.open(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
        "&qn=32&fnval=1&platform=html5&high_quality=1", timeout=25
    ).read().decode("utf-8", "ignore"))
    if p.get("code") != 0:
        raise RuntimeError(f"playurl code={p.get('code')}")
    durl = (p.get("data") or {}).get("durl") or []
    if durl:
        return durl[0]["url"], None

    dash = (p.get("data") or {}).get("dash") or {}
    vids = dash.get("video") or []
    auds = dash.get("audio") or []
    if not vids:
        raise RuntimeError("无可用流")
    vb = sorted(vids, key=lambda x: -(x.get("bandwidth") or 0))[0]
    ab = sorted(auds, key=lambda x: -(x.get("bandwidth") or 0))[0] if auds else None
    return (vb.get("baseUrl") or vb.get("base_url"),
            (ab.get("baseUrl") or ab.get("base_url")) if ab else None)


def dl(url, dest, referer):
    r = http(url, referer=referer, timeout=300)
    tmp = dest.with_suffix(dest.suffix + ".part")
    got = 0
    with open(tmp, "wb") as f:
        while True:
            chunk = r.read(524288)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
    tmp.rename(dest)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-dur", type=int, default=1200, help="最短时长(秒)，默认1200=20分钟")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--outdir", default="videos/06_B站原片")
    ap.add_argument("--list", action="store_true", help="只列出不下载")
    args = ap.parse_args()

    if not LIST_JSON.exists():
        print(f"缺少 {LIST_JSON}", file=sys.stderr)
        return 1
    data = json.loads(LIST_JSON.read_text(encoding="utf-8"))
    picked = [(b, v) for b, v in data.items() if to_sec(v.get("dur")) >= args.min_dur]
    picked.sort(key=lambda x: -to_sec(x[1].get("dur")))
    if args.limit:
        picked = picked[:args.limit]

    outdir = BASE / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"符合条件 {len(picked)} 条 → {outdir}\n")

    if args.list:
        for b, v in picked[:40]:
            print(f"  {v.get('dur'):>8}  {v.get('date')}  {b}  {v.get('title','')[:50]}")
        return 0

    has_ffmpeg = subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0
    ok = fail = skip = 0
    total = 0
    for i, (bvid, v) in enumerate(picked, 1):
        title = v.get("title", "")
        dest = outdir / f"{safe(title)}__{bvid}.mp4"
        if dest.exists() and dest.stat().st_size > 500000:
            print(f"[{i}/{len(picked)}] ⏭  {dest.name[:56]}")
            skip += 1
            continue
        ref = f"https://www.bilibili.com/video/{bvid}/"
        try:
            vurl, aurl = get_streams(bvid)
            vtmp = outdir / f".{bvid}.v.m4s"
            got = dl(vurl, vtmp, ref)
            if aurl and has_ffmpeg:
                atmp = outdir / f".{bvid}.a.m4s"
                got += dl(aurl, atmp, ref)
                r = subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error", "-i", str(vtmp), "-i", str(atmp),
                     "-c", "copy", str(dest)], capture_output=True)
                vtmp.unlink(missing_ok=True)
                atmp.unlink(missing_ok=True)
                if r.returncode != 0:
                    raise RuntimeError(f"ffmpeg: {r.stderr.decode()[:60]}")
            else:
                vtmp.rename(dest)
            total += got
            print(f"[{i}/{len(picked)}] ✅ {got/1048576:7.1f}MB  {dest.name[:52]}")
            ok += 1
        except Exception as e:
            print(f"[{i}/{len(picked)}] ❌ {type(e).__name__}: {str(e)[:44]}  {title[:32]}")
            fail += 1
        time.sleep(1.2)

    print(f"\n成功 {ok} · 跳过 {skip} · 失败 {fail} · 共 {total/1073741824:.2f}GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
