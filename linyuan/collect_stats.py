#!/usr/bin/env python3
"""采集本号（园来滚雪球）在 B站的播放数据，产出 site/stats.json 供 log.html 展示。

为什么走搜索 API 而不是空间接口：
  B站空间接口 x/space/wbi/arc/search 需要 wbi 动态签名，直接调 412；
  搜索接口配合 fetch_bilibili 的指纹会话可稳定拿到数据（监控每天在用）。
局限（页面上也会标注）：
  搜索只返回被索引到的稿件，实测 60/78，会漏约两成，总量偏低；
  但漏的是随机的，趋势与中位数的相对比较仍可靠。

「新规则片 / 旧规则片」怎么区分：
  按投稿时间切分 —— FC 新代码 2026-09-02 11:00 部署，之后投出去的才走
  「林园：原话金句」前缀式标题 + ≥7 分金句选段 + OCR 裁切。
  （最初想用「标题是否含｜林园」判断，实测踩坑：这个号早期发过银行卡开箱、
   小米手环、小猫咪等无关内容，标题本来就没有｜，被误判成新规则，
   其中一条 24448 播放直接把新规则中位数拉到失真。）
"""
import json
import re
import statistics
import sys
import time
import urllib.parse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch_bilibili import http  # noqa: E402

UP_NAME = "园来滚雪球"
OUT = Path(__file__).parent.parent / "site" / "stats.json"
CST = timezone(timedelta(hours=8))


def api(url, retries=3):
    for i in range(retries):
        try:
            return json.loads(http(url).read().decode("utf-8", "ignore"))
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"  重试 {i+1}/{retries}: {type(e).__name__}", file=sys.stderr)
            time.sleep(3)


def strip_tags(s):
    return re.sub(r"<[^>]+>", "", s or "")


def fetch_ours(max_pages=6):
    """按发布时间抓本号稿件。多关键词union，尽量补全搜索索引的遗漏。"""
    seen = {}
    for kw in (UP_NAME, "林园"):
        for pn in range(1, max_pages + 1):
            try:
                d = api("https://api.bilibili.com/x/web-interface/search/type"
                        f"?search_type=video&keyword={urllib.parse.quote(kw)}"
                        f"&page={pn}&order=pubdate")
            except Exception as e:
                print(f"[stats] {kw} 第{pn}页失败: {e}", file=sys.stderr)
                break
            res = (d.get("data") or {}).get("result") or []
            if not res:
                break
            for v in res:
                if v.get("author") == UP_NAME:
                    seen[v["bvid"]] = v
            time.sleep(1.2)
    return list(seen.values())


# FC 部署新代码的时间点（2026-09-02 11:00 CST），之后投稿的算新规则片
NEW_RULE_SINCE = int(datetime(2026, 9, 2, 11, 0, tzinfo=CST).timestamp())
# 这个号早期发过与林园无关的内容（银行卡开箱/手环/学车/猫），统计时要排除
OFF_TOPIC = re.compile(r"不务正业|借记卡|信用卡|开箱|手环|小米|学车|小猫|猫咪")


def is_relevant(title):
    return "林园" in title and not OFF_TOPIC.search(title)


def is_new_rule(item):
    return item["pubdate"] >= NEW_RULE_SINCE


def build(videos):
    items = []
    skipped = 0
    for v in videos:
        t = strip_tags(v.get("title", ""))
        if not is_relevant(t):
            skipped += 1
            continue
        items.append({
            "bvid": v.get("bvid"),
            "title": t,
            "play": v.get("play", 0) or 0,
            "danmaku": v.get("video_review", 0) or 0,
            "like": v.get("like", 0) or 0,
            "pubdate": v.get("pubdate", 0) or 0,
            "duration": v.get("duration", ""),
        })
    for it in items:
        it["new_rule"] = is_new_rule(it)
    if skipped:
        print(f"[stats] 排除与林园无关的旧内容 {skipped} 条")
    items.sort(key=lambda x: -x["pubdate"])

    byday = defaultdict(list)
    for it in items:
        if it["pubdate"]:
            byday[datetime.fromtimestamp(it["pubdate"], CST).strftime("%Y-%m-%d")].append(it)
    daily = []
    for day in sorted(byday, reverse=True)[:30]:
        plays = [x["play"] for x in byday[day]]
        daily.append({
            "date": day, "count": len(plays), "total": sum(plays),
            "median": round(statistics.median(plays), 1),
            "max": max(plays),
            "new_rule_count": sum(1 for x in byday[day] if x["new_rule"]),
        })

    def group(sel):
        p = [x["play"] for x in items if sel(x)]
        if not p:
            return {"count": 0, "total": 0, "median": 0, "mean": 0, "max": 0}
        return {"count": len(p), "total": sum(p),
                "median": round(statistics.median(p), 1),
                "mean": round(statistics.mean(p), 1), "max": max(p)}

    return {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "up_name": UP_NAME,
        "note": "数据来自 B站搜索 API，只覆盖被索引到的稿件（实测约 8 成），总量偏低但趋势可比",
        "overall": group(lambda x: True),
        # 新旧规则对比：这是判断 2026-09-02 改造是否有效的核心指标
        "new_rule": group(lambda x: x["new_rule"]),
        "old_rule": group(lambda x: not x["new_rule"]),
        "daily": daily,
        "top": items[:200],
    }


def main():
    print("[stats] 抓取本号稿件...")
    videos = fetch_ours()
    print(f"[stats] 抓到 {len(videos)} 条")
    if not videos:
        print("[stats] 没抓到数据，保留原文件不覆盖", file=sys.stderr)
        return 1
    data = build(videos)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    o, n, ol = data["overall"], data["new_rule"], data["old_rule"]
    print(f"[stats] 全部 {o['count']} 条 总播放 {o['total']} 中位 {o['median']}")
    print(f"[stats] 新规则 {n['count']} 条 中位 {n['median']} | 旧规则 {ol['count']} 条 中位 {ol['median']}")
    print(f"[stats] → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
