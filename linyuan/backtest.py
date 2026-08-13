#!/usr/bin/env python3
"""回测：验证监控系统的核心能力是否真实有效。

回测的三个命题：
  A. 时间领先   — 股东大会公告是否真的早于 UP 主二创发布
  B. 内容覆盖   — 主题覆盖率、长视频覆盖率
  C. 数据可用   — 落盘视频是否为完整可播文件（非 0 字节 / 非 HTML 错误页）

用法：python3 backtest.py
"""
import collections
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
UP = BASE / "up_videos.json"
DATA = BASE / "dashboard" / "data.json"
VID = BASE / "videos"

KEYS = ["医药", "中药", "老龄化", "估值", "牛市", "白酒", "茅台", "五粮液", "片仔癀",
        "达仁堂", "同仁堂", "云南白药", "AI", "科技", "分红", "股东大会", "演讲",
        "专访", "王石", "巴菲特", "消费", "食品", "饮料", "券商", "地产", "垄断", "泡沫"]


def sig(t):
    return {k for k in KEYS if k in t}


def sec(d):
    if isinstance(d, int):
        return d
    p = str(d).split(":")
    try:
        if len(p) == 2:
            return int(p[0]) * 60 + int(p[1])
        if len(p) == 3:
            return int(p[0]) * 3600 + int(p[1]) * 60 + int(p[2])
        return int(p[0])
    except Exception:
        return 0


def test_lead_time(up, mine):
    """A. 每场股东大会，公告是否早于二创？"""
    print("═" * 60)
    print("A. 时间领先性回测")
    print("═" * 60)
    sm = [x for x in mine if x["source"] == "shareholder_meeting"
          and (x.get("extra") or {}).get("meeting_date")]
    rows = []
    for a in sm:
        e = a["extra"]
        md, co, ann = e.get("meeting_date"), e.get("company", ""), a["publish_time"][:10]
        if not md or not co:
            continue
        hits = []
        for bv, v in up.items():
            if co[:2] in v["title"] and v["date"] >= md:
                gap = (datetime.fromisoformat(v["date"]) - datetime.fromisoformat(md)).days
                if 0 <= gap <= 14:
                    hits.append(v["date"])
        if hits:
            first = min(hits)
            lead = (datetime.fromisoformat(first) - datetime.fromisoformat(ann)).days
            rows.append((co, ann, md, first, lead, len(hits)))
    if not rows:
        print("  ⚠ 无可回测样本")
        return None
    rows.sort(key=lambda r: -r[4])
    print(f"  {'公司':<8}{'公告':<12}{'会议':<12}{'首个二创':<12}{'领先':>6}")
    for co, ann, md, ugc, lead, n in rows:
        flag = "✅" if lead > 0 else "❌"
        print(f"  {co:<8}{ann:<12}{md:<12}{ugc:<12}{lead:>4}天 {flag}")
    wins = sum(1 for r in rows if r[4] > 0)
    avg = sum(r[4] for r in rows) / len(rows)
    print(f"\n  结论: {wins}/{len(rows)} 场领先，平均 {avg:.0f} 天")
    return wins == len(rows)


def test_coverage(up, mine):
    """B. 内容覆盖率"""
    print("\n" + "═" * 60)
    print("B. 内容覆盖率回测")
    print("═" * 60)
    myvid = [x for x in mine if (x.get("extra") or {}).get("mp4_url")]
    bili = {p.name.split("__")[-1].replace(".mp4", "")
            for p in (VID / "06_B站原片").glob("*.mp4")} if (VID / "06_B站原片").exists() else set()

    uc, mc = collections.Counter(), collections.Counter()
    for v in up.values():
        for k in sig(v["title"]):
            uc[k] += 1
    for x in myvid:
        for k in sig(x["title"]):
            mc[k] += 1
    for b in bili:
        for k in sig(up.get(b, {}).get("title", "")):
            mc[k] += 1

    weak, tot, cov = [], 0, 0
    for k, n in uc.most_common():
        if n < 8:
            continue
        m = mc.get(k, 0)
        tot += 1
        ok = m >= n * 0.15
        cov += ok
        if not ok:
            weak.append((k, n, m))
    print(f"  主题达标率: {cov}/{tot} = {cov/tot*100:.0f}%")
    if weak:
        print("  未达标: " + ", ".join(f"{k}(她{n}/我{m})" for k, n, m in weak))

    longs = [b for b, v in up.items() if sec(v.get("dur")) >= 1200]
    hit = len(set(longs) & bili)
    print(f"  长视频覆盖: {hit}/{len(longs)} = {hit/len(longs)*100:.0f}%")
    print(f"  原片总量:   {len(bili)}/{len(up)} = {len(bili)/len(up)*100:.1f}%")
    return cov == tot and hit == len(longs)


def test_integrity():
    """C. 落盘视频完整性抽检"""
    print("\n" + "═" * 60)
    print("C. 视频文件完整性回测")
    print("═" * 60)
    files = sorted(VID.rglob("*.mp4"))
    if not files:
        print("  ⚠ 无视频文件")
        return False
    total = sum(f.stat().st_size for f in files)
    tiny = [f for f in files if f.stat().st_size < 10240]
    bad = []
    # 抽检文件头（MP4 应含 ftyp）
    import random
    for f in random.sample(files, min(20, len(files))):
        try:
            with open(f, "rb") as fh:
                head = fh.read(64)
            if b"ftyp" not in head:
                bad.append(f.name)
        except Exception:
            bad.append(f.name)
    print(f"  文件总数: {len(files)} 个 · {total/1073741824:.2f}GB")
    print(f"  异常小文件(<10KB): {len(tiny)}")
    print(f"  抽检 20 个，文件头异常: {len(bad)}")
    if bad:
        for b in bad[:5]:
            print(f"    ✗ {b[:56]}")
    ok = not tiny and not bad
    print(f"  结论: {'✅ 全部为有效 MP4' if ok else '⚠ 存在异常文件'}")
    return ok


def main():
    for p in (UP, DATA):
        if not p.exists():
            print(f"缺少 {p}", file=sys.stderr)
            return 1
    up = json.loads(UP.read_text(encoding="utf-8"))
    mine = json.loads(DATA.read_text(encoding="utf-8"))
    print(f"回测基准: UP主 {len(up)} 条 / 监控库 {len(mine)} 条\n")

    r = [test_lead_time(up, mine), test_coverage(up, mine), test_integrity()]

    print("\n" + "═" * 60)
    names = ["时间领先", "内容覆盖", "数据可用"]
    for n, x in zip(names, r):
        print(f"  {n}: {'✅ 通过' if x else ('⚠ 部分' if x is not None else '— 无样本')}")
    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
