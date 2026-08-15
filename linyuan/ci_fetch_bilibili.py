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
import re
import sys
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def opener():
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9"),
                     ("Referer", "https://www.bilibili.com/")]
    try:
        op.open("https://www.bilibili.com/", timeout=20).read()
        spi = json.loads(op.open(
            "https://api.bilibili.com/x/frontend/finger/spi", timeout=20).read().decode())
        import http.cookiejar as cj
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


def via_embed(op, bvid):
    """策略 C：embed 播放页的 __playinfo__ 直接带流地址，连 playurl 都省了。"""
    html = op.open(
        f"https://player.bilibili.com/player.html?bvid={bvid}&autoplay=0",
        timeout=30).read().decode("utf-8", "ignore")
    m = re.search(r"__playinfo__\s*=\s*(\{.*?\})\s*</script>", html, re.S) \
        or re.search(r"__playinfo__\s*=\s*(\{.*)", html)
    if not m:
        raise RuntimeError("embed 页没有 __playinfo__")
    info = json.loads(m.group(1))
    durl = (info.get("data") or {}).get("durl") or []
    if not durl:
        raise RuntimeError("__playinfo__ 无 durl")
    return durl[0]["url"]


def playurl(op, bvid, cid):
    p = json.loads(op.open(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
        "&qn=32&fnval=1&platform=html5&high_quality=1", timeout=30).read())
    if p.get("code") != 0:
        raise RuntimeError(f"playurl code={p.get('code')}")
    durl = (p.get("data") or {}).get("durl") or []
    if not durl:
        raise RuntimeError("无可用流")
    return durl[0]["url"]


def download(op, url, referer, out):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer})
    with op.open(req, timeout=300) as r, open(out, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)


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
            stream = fn(op)
            print(f"  拿到流地址，下载中...")
            download(op, stream, args.url, args.out)
            print(f"✓ {name} 成功")
            return
        except Exception as e:
            last = e
            print(f"  ✗ {e}", file=sys.stderr)
    sys.exit(f"所有策略失败，最后错误：{last}")


if __name__ == "__main__":
    main()
