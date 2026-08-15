#!/usr/bin/env python3
"""CI 侧 B站取源：view → playurl → 下载。

为什么存在：B站 API 的 WAF 是按 IP 段挑的 —— GitHub runner 能调（监控每天
在 CI 调 search API 都成功），阿里云 FC 反而被 412。所以 B站源的下载放在 CI。

只依赖标准库。用法：
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    m = re.search(r"(BV\w+)", args.url)
    if not m:
        sys.exit("URL 里没有 BV 号")
    bvid = m.group(1)
    op = opener()

    print(f"[1/3] view {bvid}")
    v = json.loads(op.open(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=30).read())
    if v.get("code") != 0:
        sys.exit(f"view 失败: code={v.get('code')} {v.get('message')}")
    cid = v["data"]["cid"]

    print("[2/3] playurl")
    p = json.loads(op.open(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
        "&qn=32&fnval=1&platform=html5&high_quality=1", timeout=30).read())
    if p.get("code") != 0:
        sys.exit(f"playurl 失败: code={p.get('code')}")
    durl = (p.get("data") or {}).get("durl") or []
    if not durl:
        sys.exit("无可用流")

    print(f"[3/3] 下载（{durl[0].get('size', 0)/1048576:.0f}MB）")
    req = urllib.request.Request(durl[0]["url"],
                                 headers={"User-Agent": UA, "Referer": args.url})
    with op.open(req, timeout=300) as r, open(args.out, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    print("✓ 完成")


if __name__ == "__main__":
    main()
