#!/usr/bin/env python3
"""体检 data.json 中所有视频直链的可用性。

注意：微博 CDN 需要 Referer: https://weibo.com/，否则 403。
下载这些视频时同样要带上对应 Referer。
"""
import collections
import json
from pathlib import Path
import sys
import concurrent.futures as cf
import urllib.request

from common import UA_PC as UA, REFERER

def check(t):
    src, title, url = t
    h = {"User-Agent": UA, "Range": "bytes=0-1023"}
    if REFERER.get(src):
        h["Referer"] = REFERER[src]
    try:
        # 用 Range GET 而非 HEAD：部分 CDN（如微博）拒绝 HEAD 但允许 GET
        r = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=15)
        ct = r.headers.get("Content-Type", "") or ""
        cr = r.headers.get("Content-Range", "")
        cl = int(cr.split("/")[-1]) if "/" in cr else int(r.headers.get("Content-Length") or 0)
        chunk = r.read(64)
        okay = ("video" in ct or "octet" in ct or cl > 200000 or len(chunk) > 0)
        return (src, title, "OK" if okay else f"?{ct[:16]}", cl)
    except Exception as e:
        return (src, title, type(e).__name__, 0)

def main():
    d = json.load(open(Path(__file__).parent / "dashboard" / "data.json"))
    targets = []
    for x in d:
        e = x.get("extra") or {}
        u = e.get("mp4_url") or e.get("video_url") or ""
        if u.startswith("http"):
            targets.append((x["source"], x["title"][:34], u))
    res = []
    with cf.ThreadPoolExecutor(10) as ex:
        for r in ex.map(check, targets):
            res.append(r)
    by = collections.defaultdict(lambda: [0, 0, 0])
    for s, _, st, cl in res:
        by[s][0] += 1
        if st == "OK":
            by[s][1] += 1
            by[s][2] += cl
    tot = ok = vol = 0
    print(f"{'来源':<20}{'可用/总':>10}{'容量':>11}")
    for s, (n, o, c) in sorted(by.items(), key=lambda x: -x[1][1]):
        print(f"  {s:<18}{o:>4}/{n:<4}{c/1073741824:>9.2f}GB")
        tot += n; ok += o; vol += c
    print(f"\n可下载 {ok}/{tot} ({ok/tot*100:.0f}%)  总计 {vol/1073741824:.2f}GB")
    bad = collections.Counter(st for _, _, st, _ in res if st != "OK")
    if bad:
        print("失败:", dict(bad))
    return 0

if __name__ == "__main__":
    sys.exit(main())
