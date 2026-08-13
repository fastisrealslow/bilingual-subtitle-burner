#!/usr/bin/env python3
"""枚举 B站 UP 主「园园滚雪球」的全部视频，持久化到 up_videos.json。

为什么不用空间页 API：
  x/space/wbi/arc/search 有硬风控，即使实现 wbi 签名 + buvid 指纹仍返回 412。
  改用「合集」接口 seasons_archives_list，无此限制，且能拿到全量。

关键：必须先访问首页 + finger/spi 拿 buvid3/buvid4 cookie，否则同样被限流。

用法：
  python3 fetch_up_list.py            # 抓取并保存
  python3 fetch_up_list.py --stats    # 只看统计
"""
import argparse
import http.cookiejar
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "up_videos.json"
MID = "1700344493"

from common import UA_PC as UA


def make_opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA),
                     ("Referer", f"https://space.bilibili.com/{MID}/video"),
                     ("Accept", "application/json, text/plain, */*"),
                     ("Accept-Language", "zh-CN,zh;q=0.9")]
    try:
        op.open("https://www.bilibili.com/", timeout=20).read()
        spi = json.loads(op.open("https://api.bilibili.com/x/frontend/finger/spi",
                                 timeout=20).read().decode())
        for n, v in (("buvid3", spi["data"]["b_3"]), ("buvid4", spi["data"]["b_4"])):
            jar.set_cookie(http.cookiejar.Cookie(
                0, n, v, None, False, ".bilibili.com", True, False,
                "/", True, False, None, False, None, None, {}))
    except Exception as e:
        print(f"[warn] cookie 初始化: {type(e).__name__}", file=sys.stderr)
    return op


def get_seasons(op):
    u = (f"https://api.bilibili.com/x/polymer/web-space/seasons_series_list"
         f"?mid={MID}&page_num=1&page_size=20")
    d = json.loads(op.open(u, timeout=25).read().decode("utf-8", "ignore"))
    out = []
    il = (d.get("data") or {}).get("items_lists") or {}
    for s in (il.get("seasons_list") or []):
        m = s.get("meta") or {}
        out.append((m.get("season_id"), m.get("name", ""), m.get("total", 0)))
    return out


def fetch_season(op, sid, name, total, store):
    got, pn, fail = 0, 1, 0
    while pn <= 30 and fail < 4:
        u = (f"https://api.bilibili.com/x/polymer/web-space/seasons_archives_list"
             f"?mid={MID}&season_id={sid}&sort_reverse=false&page_num={pn}&page_size=30")
        try:
            d = json.loads(op.open(u, timeout=30).read().decode("utf-8", "ignore"))
            if d.get("code") != 0:
                fail += 1
                time.sleep(10)
                continue
            arcs = (d.get("data") or {}).get("archives") or []
            if not arcs:
                break
            for a in arcs:
                store[a["bvid"]] = {
                    "title": a.get("title", ""),
                    "date": time.strftime("%Y-%m-%d", time.localtime(a.get("pubdate", 0))),
                    "dur": a.get("duration"),
                    "play": (a.get("stat") or {}).get("view", 0),
                    "season": name,
                }
            got += len(arcs)
            pn += 1
            fail = 0
            if got >= total:
                break
            time.sleep(random.uniform(2.0, 3.5))
        except Exception:
            fail += 1
            time.sleep(12)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.stats:
        if not OUT.exists():
            print("尚未抓取")
            return 1
        d = json.loads(OUT.read_text(encoding="utf-8"))
        import collections
        print(f"共 {len(d)} 条")
        for s, n in collections.Counter(v.get("season", "?") for v in d.values()).most_common():
            print(f"  {s:24} {n}")
        return 0

    op = make_opener()
    seasons = get_seasons(op)
    print(f"发现 {len(seasons)} 个合集")
    store = {}
    if OUT.exists():
        try:
            store = json.loads(OUT.read_text(encoding="utf-8"))
            print(f"已有 {len(store)} 条，增量更新")
        except Exception:
            pass
    before = len(store)
    for sid, name, total in seasons:
        n = fetch_season(op, sid, name, total, store)
        print(f"  {name[:22]:24} {n}/{total}")
    OUT.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{before} → {len(store)} 条 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
