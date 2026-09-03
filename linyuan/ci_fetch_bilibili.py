#!/usr/bin/env python3
"""CI 侧 B站取源：多策略降级。

实测的 WAF 封控表（2026-08-15）：
    view API    GitHub ❌  FC ❌  沙箱 ✅
    search API  GitHub ✅  FC ❌  沙箱 ✅
    CDN 上传    GitHub ❌  FC ✅  沙箱 ✅
view 被封不代表全死：pagelist 拿 cid、embed 页直接带 __playinfo__，
这些端点的风控级别不同，逐条路试。

用法：
    python3 ci_fetch_bilibili.py --url https://www.bilibili.com/video/BVxx --out video.mp4
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _configured_cookie_entries():
    """读取 biliup cookies.json；只返回条目，任何异常都不打印凭据内容。"""
    raw = (os.environ.get("BILIBILI_COOKIES") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw.lstrip("\ufeff"))
        entries = ((data.get("cookie_info") or {}).get("cookies")
                   or data.get("cookies") or [])
        if isinstance(entries, list):
            return [x for x in entries
                    if isinstance(x, dict) and x.get("name") and x.get("value")]
        if isinstance(data, dict) and data.get("SESSDATA"):
            return [{"name": key, "value": value}
                    for key, value in data.items() if isinstance(value, str)]
    except (AttributeError, TypeError, ValueError):
        pass
    print("[warn] BILIBILI_COOKIES 格式无效，改用匿名高清取流", file=sys.stderr)
    return []


def opener():
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    import http.cookiejar as cj
    for item in _configured_cookie_entries():
        jar.set_cookie(cj.Cookie(
            0, item["name"], item["value"], None, False,
            item.get("domain") or ".bilibili.com", True, False,
            item.get("path") or "/", True, False, None, False,
            None, None, {}))
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9"),
                     ("Referer", "https://www.bilibili.com/")]
    try:
        op.open("https://www.bilibili.com/", timeout=20).read()
        spi = json.loads(op.open(
            "https://api.bilibili.com/x/frontend/finger/spi", timeout=20).read().decode())
        for n, val in (("buvid3", spi["data"]["b_3"]), ("buvid4", spi["data"]["b_4"])):
            jar.set_cookie(cj.Cookie(0, n, val, None, False, ".bilibili.com", True,
                                     False, "/", True, False, None, False, None, None, {}))
    except Exception as e:
        print(f"[warn] buvid 获取失败: {e}", file=sys.stderr)
    return op


def via_view(op, bvid):
    """策略 A：view API 拿 cid。"""
    v = json.loads(op.open(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=30).read())
    if v.get("code") != 0:
        raise RuntimeError(f"view code={v.get('code')}")
    return v["data"]["cid"]


def via_pagelist(op, bvid):
    """策略 B：pagelist 拿 cid（风控级别和 view 不同）。"""
    r = json.loads(op.open(
        f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}", timeout=30).read())
    if r.get("code") != 0 or not r.get("data"):
        raise RuntimeError(f"pagelist code={r.get('code')}")
    return r["data"][0]["cid"]


def _urls(stream):
    if not stream:
        return []
    values = [stream.get("baseUrl") or stream.get("base_url") or stream.get("url")]
    backups = stream.get("backupUrl") or stream.get("backup_url") or []
    values.extend([backups] if isinstance(backups, str) else backups)
    return list(dict.fromkeys(x for x in values if x))


def select_streams(info):
    """优先选择不超过 1080P 的最高画质 DASH，并优先 H.264 兼容编码。"""
    data = (info or {}).get("data") or (info or {}).get("result") or {}
    dash = data.get("dash") or {}
    videos = [x for x in (dash.get("video") or [])
              if int(x.get("height") or 0) <= 1080]
    if videos:
        top_height = max(int(x.get("height") or 0) for x in videos)
        top = [x for x in videos if int(x.get("height") or 0) == top_height]
        avc = [x for x in top if int(x.get("codecid") or 0) == 7]
        video = max(avc or top, key=lambda x: int(x.get("bandwidth") or 0))
        audios = dash.get("audio") or []
        audio = max(audios, key=lambda x: int(x.get("bandwidth") or 0)) if audios else None
        return {"video": _urls(video), "audio": _urls(audio),
                "height": top_height, "quality": data.get("quality")}
    durl = data.get("durl") or []
    if durl:
        return {"video": _urls(durl[0]), "audio": [],
                "height": 0, "quality": data.get("quality")}
    raise RuntimeError("无可用 DASH/durl 流")


def via_embed(op, bvid):
    """策略 C：embed 播放页的 __playinfo__ 直接带流地址，连 playurl 都省了。"""
    html = op.open(
        f"https://player.bilibili.com/player.html?bvid={bvid}&autoplay=0",
        timeout=30).read().decode("utf-8", "ignore")
    m = re.search(r"__playinfo__\s*=\s*(\{.*?\})\s*</script>", html, re.S) \
        or re.search(r"__playinfo__\s*=\s*(\{.*)", html)
    if not m:
        raise RuntimeError("embed 页没有 __playinfo__")
    return select_streams(json.loads(m.group(1)))


def playurl(op, bvid, cid):
    p = json.loads(op.open(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
        "&qn=80&fnval=4048&fourk=0&high_quality=1", timeout=30).read())
    if p.get("code") != 0:
        raise RuntimeError(f"playurl code={p.get('code')}")
    return select_streams(p)


def download_one(op, urls, referer, out, attempts=3):
    """镜像轮换 + 临时文件 + Content-Length 校验，避免半截 MP4 被当成功。"""
    out = Path(out)
    last = None
    for attempt in range(attempts):
        for url in urls:
            tmp = out.with_suffix(out.suffix + ".part")
            tmp.unlink(missing_ok=True)
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": UA, "Referer": referer})
                with op.open(req, timeout=600) as response, tmp.open("wb") as handle:
                    expected = int(response.headers.get("Content-Length") or 0)
                    while True:
                        chunk = response.read(1 << 20)
                        if not chunk:
                            break
                        handle.write(chunk)
                actual = tmp.stat().st_size
                if actual < 10240 or (expected and actual != expected):
                    raise RuntimeError(f"下载不完整：{actual}/{expected or '?'} bytes")
                tmp.replace(out)
                return
            except Exception as exc:
                last = exc
                tmp.unlink(missing_ok=True)
        if attempt + 1 < attempts:
            time.sleep(2 ** attempt)
    raise RuntimeError(f"所有 CDN 镜像下载失败：{last}")


def download(op, streams, referer, out):
    out = Path(out)
    if not streams.get("audio"):
        download_one(op, streams["video"], referer, out)
        return
    video = out.with_suffix(".video.m4s")
    audio = out.with_suffix(".audio.m4s")
    try:
        download_one(op, streams["video"], referer, video)
        download_one(op, streams["audio"], referer, audio)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
            "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
            "-c", "copy", "-movflags", "+faststart", str(out),
        ], check=True)
    finally:
        video.unlink(missing_ok=True)
        audio.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    m = re.search(r"(BV\w+)", args.url)
    if not m:
        sys.exit("URL 里没有 BV 号")
    bvid = m.group(1)

    strategies = [
        ("view→playurl", lambda op: playurl(op, bvid, via_view(op, bvid))),
        ("pagelist→playurl", lambda op: playurl(op, bvid, via_pagelist(op, bvid))),
        ("embed __playinfo__", lambda op: via_embed(op, bvid)),
    ]
    last = None
    for name, fn in strategies:
        op = opener()
        try:
            print(f"→ 策略 {name}")
            streams = fn(op)
            height = streams.get("height") or "未知"
            print(f"  拿到最高可用流（{height}P），下载中...")
            download(op, streams, args.url, args.out)
            print(f"✓ {name} 成功")
            return
        except Exception as e:
            last = e
            print(f"  ✗ {e}", file=sys.stderr)
    sys.exit(f"所有策略失败，最后错误：{last}")


if __name__ == "__main__":
    main()
