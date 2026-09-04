#!/usr/bin/env python3
"""长期运行健康巡检。

为什么需要：这套系统靠 6 处硬编码标识（腾讯 suid、网易标签ID 等）工作，
平台改版后接口往往**静默失败**——返回成功但零数据，不检查就发现不了。
另外视频库按日增速一年会涨到几十 GB，需要提前预警。

用法：
  python3 healthcheck.py           # 巡检
  python3 healthcheck.py --json    # 机器可读输出（供 CI 判断退出码）
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from common import http_get

BASE = Path(__file__).parent
DB = BASE / "monitor_v2.db"
DATA = BASE / "dashboard" / "data.json"
VIDEOS = BASE / "videos"

# 容量阈值
DATA_JSON_WARN_MB = 8      # 前端一次性加载，超过就会卡
VIDEO_WARN_GB = 40
DB_WARN_MB = 50


def check_endpoints():
    """探测关键接口是否仍然有效（防静默失败）。"""
    out = []
    probes = [
        ("腾讯 作者流", "https://i.news.qq.com/getSubNewsMixedList?offset_info=0"
                     "&guestSuid=8QMY3Hxd7YIevjs%3D&tabId=om_video&caller=1&from_scene=105",
         "https://news.qq.com/",
         lambda b: len((json.loads(b).get("newslist") or [])) > 0),
        ("腾讯 视频信息", "https://i.news.qq.com/getWebVideo?id=20260716V0AH6U00"
                      "&appver=29_android_7.6.10",
         "https://news.qq.com/",
         lambda b: json.loads(b).get("ret") == 0),
        # B站搜索必须带 Referer，否则返回 code=0 但零结果（静默失败）
        ("B站 搜索", "https://api.bilibili.com/x/web-interface/wbi/search/type"
                   "?search_type=video&keyword=%E6%9E%97%E5%9B%AD&page=1",
         "https://www.bilibili.com/",
         lambda b: len((json.loads(b).get("data") or {}).get("result") or []) > 0),
        ("网易 标签页", "https://money.163.com/keywords/6/9/679756ed/1.html",
         "https://money.163.com/",
         lambda b: "163.com/v/video" in b),
        ("巨潮 股票列表", "http://www.cninfo.com.cn/new/data/szse_stock.json",
         "http://www.cninfo.com.cn/",
         lambda b: len(json.loads(b).get("stockList") or []) > 1000),
    ]
    for name, url, ref, ok_fn in probes:
        try:
            body = http_get(url, referer=ref, timeout=20)
            ok = ok_fn(body)
            out.append((name, "OK" if ok else "空数据(疑似失效)", ok))
        except Exception as e:
            out.append((name, f"{type(e).__name__}", False))
    return out


def check_freshness():
    """数据是否还在更新——长期不变说明抓取实际已失效。"""
    if not DB.exists():
        return None, "数据库不存在"
    conn = sqlite3.connect(DB)
    row = conn.execute("SELECT MAX(updated_at) FROM items").fetchone()
    conn.close()
    if not row or not row[0]:
        return None, "无数据"
    try:
        last = datetime.fromisoformat(row[0])
    except ValueError:
        return None, f"时间格式异常: {row[0]}"
    age = (datetime.now() - last).total_seconds() / 3600
    return age, f"最近更新 {age:.1f} 小时前"


def check_capacity():
    out = []
    if DATA.exists():
        mb = DATA.stat().st_size / 1048576
        out.append(("data.json", f"{mb:.1f}MB", mb < DATA_JSON_WARN_MB))
    if DB.exists():
        mb = DB.stat().st_size / 1048576
        out.append(("数据库", f"{mb:.1f}MB", mb < DB_WARN_MB))
    if VIDEOS.exists():
        gb = sum(f.stat().st_size for f in VIDEOS.rglob("*.mp4")) / 1073741824
        out.append(("视频库", f"{gb:.1f}GB", gb < VIDEO_WARN_GB))
    return out


def check_seeds():
    """种子池是否长期未更新——它不会自己增长。"""
    out = []
    for f, key in (("douyin_seeds.json", "urls"),
                   ("haokan_seeds.json", "vids"),
                   ("netease_seeds.json", "vcodes"),
                   ("yicai_seeds.json", "ids")):
        p = BASE / f
        if not p.exists():
            out.append((f, "缺失", False))
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        upd = d.get("updated_at", "")
        n = len(d.get(key, []))
        stale = False
        if upd:
            try:
                stale = (datetime.now() - datetime.fromisoformat(upd)) > timedelta(days=45)
            except ValueError:
                pass
        out.append((f.replace("_seeds.json", ""), f"{n} 个 / 更新于 {upd or '?'}", not stale))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    ep = check_endpoints()
    age, age_msg = check_freshness()
    cap = check_capacity()
    seeds = check_seeds()

    fails = [n for n, _, ok in ep if not ok]
    warns = [n for n, _, ok in cap if not ok] + [n for n, _, ok in seeds if not ok]
    if age is not None and age > 48:
        warns.append("数据陈旧")

    if args.json:
        print(json.dumps({
            "endpoints": [{"name": n, "status": s, "ok": o} for n, s, o in ep],
            "freshness_hours": age, "capacity": [{"name": n, "value": v, "ok": o} for n, v, o in cap],
            "seeds": [{"name": n, "value": v, "ok": o} for n, v, o in seeds],
            "fail": fails, "warn": warns,
        }, ensure_ascii=False, indent=1))
        return 1 if fails else 0

    print("═" * 56)
    print("接口可用性（防静默失败）")
    print("═" * 56)
    for n, s, ok in ep:
        print(f"  {'✅' if ok else '❌'} {n:16} {s}")

    print("\n数据新鲜度")
    print(f"  {'✅' if (age or 0) <= 48 else '⚠️ '} {age_msg}")

    print("\n容量")
    for n, v, ok in cap:
        print(f"  {'✅' if ok else '⚠️ '} {n:12} {v}")

    print("\n种子池（不会自增长，需定期人工补充）")
    for n, v, ok in seeds:
        print(f"  {'✅' if ok else '⚠️ '} {n:12} {v}")

    print("\n" + "═" * 56)
    if fails:
        print(f"❌ 接口异常: {', '.join(fails)} — 需立即排查")
    if warns:
        print(f"⚠️  需关注: {', '.join(warns)}")
    if not fails and not warns:
        print("✅ 全部正常")
    print("═" * 56)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
