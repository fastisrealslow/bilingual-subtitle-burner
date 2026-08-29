# -*- coding: utf-8 -*-
"""林园流水线 · 境内执行端（阿里云函数计算 FC）

部署
----
1. 函数计算控制台 → 创建函数（Python 3.10+，运行时选「自定义」或标准 Python）
2. 本文件为入口 index.py，配两个定时触发器：
     dispatch_handler   每天 10:00（cron: 0 0 10 * * *）
     publish_handler    每小时（cron: 0 30 * * * *）
3. 依赖层（Layer）：pip install biliup requests -t python/
4. 环境变量：
     GITHUB_TOKEN        GitHub PAT（repo + actions 权限）
     BILIBILI_COOKIES    cookies.json 全文（biliup login 产出）

为什么需要这个函数
------------------
- 微博/B站 CDN 封海外 IP（GitHub runner 下不了源视频）→ 选片下载须在境内
- B站 upos 投稿对海外 IP 零吞吐（CI 实测 16 分钟零字节）→ 投稿须在境内
- 境内阿里云实测：下载正常、投稿 30MB/s 秒传（2026-08-15 验证）
"""
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

log = logging.getLogger()
log.setLevel(logging.INFO)

REPO = "fastisrealslow/bilingual-subtitle-burner"
API = f"https://api.github.com/repos/{REPO}"
WF_PRODUCE = "linyuan-produce-cn.yml"
DATA_JSON = "linyuan/dashboard/data.json"
RELEASE_TAG = "staging"

MIN_DUR, MAX_DUR = 90, 5400             # 90s 可选 2-3 段；上限 90 分钟：完整访谈/路演是最佳素材，
                                          # ASR 实时率 1.17x → 90min 视频约 110min 转写，CI 180min 超时放得下
MAX_PER_DAY = 7                          # 每天最多成功调度几条素材（2026-08-29 提至 7，保证供应 ≥6 条成片）
MAX_PUBLISH_PER_DAY = 6                  # 每天最多投几条成片（2026-08-29 改成 6 条，含中视频）
PENDING_LIMIT = 10                        # 待投成片积压阈值：超过就暂停调度（防积压爆炸，2026-08-27）
MAX_ATTEMPTS = 10                        # 每天最多尝试调度几条（含下载失败的）
DELAY_LADDER = [5, 8, 11]                # B站定时发布阶梯（必须 >4h）
SAME_VIDEO_COOLDOWN = 48 * 3600          # 同源冷却：同一场会切片不能连发
TID, COPYRIGHT = 207, 2                  # 财经商业 / 转载（转载必须带 source）

# 搜索噪音：标题命中即排除
# - 「虎林园」「东北虎林园」是老虎公园，不是林园本人
# - 「林园群」是人名「林园群」，不是投资人林园
# - 「横道河子」是东北虎产地
# - 「二埋汰」是东北虎网红名
NOISE = re.compile(r"虎林园|东北虎|横道河子|二埋汰|林园群|周瑜|雕像|泼漆|通报")

# B站源「机构白名单」（与 monitor_v2.py/stage_and_dispatch.py 保持一致）：
# 只保留明确的一手机构/官方号，其余二创个人号全排除。
# 「完整原片」识别（2026-08-27 重构）：不认作者、认内容形态。
# 真相：微博 764 条里官方媒体仅 12 条、B站 141 条里机构仅 8 条，纯一手撑不起每天 5 条。
# 改为按内容判断——「完整原片」（完整访谈/发言/直播/实录，哪怕自媒体转发）收，
# 「剪辑二创」（金句/观点/碎片/标题党）拒。
FULL_TITLE_PAT = re.compile(
    r"完整|全纪录|全记录|访谈|实录|直播|演讲|全程|发言|现场|对话|采访|股东会|路演|专访")
CLIP_TITLE_PAT = re.compile(
    r"金句|十大观点|秘诀|股神|曝光|惊人|精华|速看|语录|震撼|必看|揭秘|真相|名场面|划重点|一分钟|三分钟|解读|盘点|总结|五大|几条|个方法|条铁律")

# 投稿好时段（北京）：与 publish 触发器 cron 对齐（9/11/13/15/18/21 六次，每次只投 1 条）
PUBLISH_SLOTS = [(9, 0), (11, 0), (13, 0), (15, 0), (18, 0), (21, 0)]


def platform_of(source):
    """把监控源名归一化成平台名（供日志页显示）。"""
    s = (source or "").lower()
    if s.startswith("bilibili"):
        return "bilibili"
    if s.startswith("weibo"):
        return "weibo"
    if s.startswith("tencent"):
        return "tencent"
    if s.startswith("xueqiu"):
        return "xueqiu"
    if s.startswith("douyin"):
        return "douyin"
    if s.startswith("haokan"):
        return "haokan"
    if s.startswith("netease"):
        return "netease"
    return s or "unknown"


TOKEN = os.environ.get("GITHUB_TOKEN", "")
COOKIES_JSON = os.environ.get("BILIBILI_COOKIES", "")

# FC 实例可复用（热启动），状态文件放 /tmp 能在短时间内防重；
# 冷启动会丢，所以 dispatched 名单同时维护在 GitHub 侧（见 state 函数）
STATE_KEY = "linyuan/.automation/fc_state.json"
# 日志页（GitHub Pages）镜像副本：Pages 不服务 .automation 点目录，
# 所以另存一份到非点路径 site/ 供 log.html 同域读取。
SITE_STATE_KEY = "site/fc_state.json"
# 运行日志：FC 每次运行的关键事件追加到这，log.html 展示，便于追踪
LOGS_KEY = "linyuan/.automation/fc_logs.json"
MAX_LOG_ENTRIES = 200


# ---------- GitHub 基础 ----------

def gh(method, path, payload=None, raw=False, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "Content-Type": "application/json", "Accept-Encoding": "gzip"})
    import gzip
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        if raw:
            return body
        return json.loads(body.decode() or "{}")


def load_state():
    try:
        raw = gh("GET", f"/contents/{STATE_KEY}?ref=main", raw=True)
        return json.loads(raw.decode())
    except Exception:
        return {"dispatched": [], "rejected": [], "published": {}}


# ---------- 运行日志（log.html 展示，便于追踪）----------

_log_buffer = []


def log_event(kind, msg, detail=""):
    """记录一条运行事件到本地缓冲，运行结束时统一追加到 GitHub。
    kind: dispatch|publish|skip|fail|publish_ok|dispatch_ok|retry|dedup
    """
    _log_buffer.append({"ts": int(time.time()), "kind": kind, "msg": msg[:120],
                        "detail": (detail or "")[:200]})
    log.info(f"[{kind}] {msg}")


def flush_logs():
    """把本次运行的事件追加到 fc_logs.json（保留最近 MAX_LOG_ENTRIES 条）。
    best-effort：失败只告警，绝不影响主流程。"""
    if not _log_buffer:
        return
    import base64
    try:
        try:
            cur = gh("GET", f"/contents/{LOGS_KEY}?ref=main&_={time.time()}")
            entries = json.loads(base64.b64decode(cur["content"]).decode())
            sha = cur.get("sha")
        except Exception:
            entries, sha = [], None
        entries.extend(_log_buffer)
        entries = entries[-MAX_LOG_ENTRIES:]
        content = base64.b64encode(json.dumps(entries, ensure_ascii=False).encode()).decode()
        payload = {"message": "chore(fc): 追加运行日志", "content": content}
        if sha:
            payload["sha"] = sha
        gh("PUT", f"/contents/{LOGS_KEY}", payload)
        # 同步镜像到 site/ 供日志页同域读取
        try:
            site_payload = {"message": "chore(fc): 同步日志页运行日志", "content": content}
            try:
                scur = gh("GET", f"/contents/site/fc_logs.json?ref=main&_={time.time()}")
                if isinstance(scur, dict) and scur.get("sha"):
                    site_payload["sha"] = scur["sha"]
            except Exception:
                pass
            gh("PUT", "/contents/site/fc_logs.json", site_payload)
        except Exception:
            pass
        _log_buffer.clear()
    except Exception as e:
        log.warning(f"运行日志追加失败（不影响主流程）: {e}")


def save_state(st, retries=3):
    """写回 fc_state.json。防御性设计：
    - PUT 前重新 GET 拿最新 sha（并发/缓存会让旧 sha 失效）
    - 任何失败重试 3 次，仍失败也只报错不抛出 —— 状态丢失可恢复，
      但保存失败绝不能把主流程搞崩（实测：422 直接炸了整个 dispatch）。
    """
    import base64
    content = base64.b64encode(json.dumps(
        st, ensure_ascii=False, indent=1).encode()).decode()
    for attempt in range(retries):
        try:
            payload = {"message": "chore(fc): 更新流水线状态", "content": content}
            try:
                cur = gh("GET", f"/contents/{STATE_KEY}?ref=main&_={time.time()}")
                if isinstance(cur, dict) and cur.get("sha"):
                    payload["sha"] = cur["sha"]
            except Exception as e:
                log.warning(f"取 sha 失败（首次创建时正常）：{e}")
            gh("PUT", f"/contents/{STATE_KEY}", payload)
            break
        except Exception as e:
            log.warning(f"save_state 第 {attempt+1} 次失败：{e}")
            time.sleep(2 * (attempt + 1))
    else:
        log.error("save_state 多次失败，本轮状态未保存（下轮会基于仓库里的旧状态重试）")
        _log_buffer.append({"ts": int(time.time()), "kind": "state_lost",
                            "msg": "⚠️ 状态保存失败！本轮状态未落盘（可能造成重复投稿/日志页不准）",
                            "detail": ""})
        return
    # 镜像到 site/ 供 GitHub Pages 日志页读取（best-effort，失败不影响主流程）
    try:
        payload = {"message": "chore(fc): 同步日志页状态", "content": content}
        try:
            cur = gh("GET", f"/contents/{SITE_STATE_KEY}?ref=main&_={time.time()}")
            if isinstance(cur, dict) and cur.get("sha"):
                payload["sha"] = cur["sha"]
        except Exception:
            pass
        gh("PUT", f"/contents/{SITE_STATE_KEY}", payload)
    except Exception as e:
        log.warning(f"site 镜像同步失败（不影响主流程）：{e}")


# ---------- 选片 ----------

def video_id_of(page_url, video_url):
    m = re.search(r"(BV\w+)", page_url or "")
    if m:
        return m.group(1)
    return (video_url or "").split("?")[0] or page_url


def mp4_duration(path):
    """纯 Python 读 mvhd atom，FC 没有 ffmpeg。
    moov atom 可能 在文件头部或尾部，两头都找。
    """
    f = Path(path).open("rb")
    # 先读头部 500KB
    data = f.read(500_000)
    i = data.find(b"mvhd")
    if i < 0:
        # 头部没找到 → 读尾部 500KB
        f.seek(0, 2)
        size = f.tell()
        tail = min(500_000, size)
        f.seek(-tail, 2)
        data = f.read(tail)
        i = data.find(b"mvhd")
    f.close()
    if i < 0:
        return 0
    ver = data[i + 4]
    if ver == 1:
        # mvhd: type(4) + version(1) + flags(3) + creation(8) + modification(8) + timescale(4) + duration(8)
        ts = int.from_bytes(data[i+20:i+24], "big")
        dur = int.from_bytes(data[i+24:i+32], "big")
    else:
        # mvhd: type(4) + version(1) + flags(3) + creation(4) + modification(4) + timescale(4) + duration(4)
        ts = int.from_bytes(data[i+16:i+20], "big")
        dur = int.from_bytes(data[i+20:i+24], "big")
    return dur / ts if ts else 0


def title_similarity(a, b):
    """简单标题相似度：共同字符占比。"""
    if not a or not b:
        return 0
    # 取前 20 字符比较
    a, b = a[:20], b[:20]
    common = sum(1 for c in a if c in b)
    return common / max(len(a), len(b))


def dedup_by_title(cands, threshold=0.6):
    """同内容去重：标题相似度 > threshold 只保留一条。
    保留质量更好的：有直链 > 无直链，时长更长的优先。
    """
    result = []
    for c in cands:
        dup = False
        for i, r in enumerate(result):
            if title_similarity(c["title"], r["title"]) >= threshold:
                # 比较质量：有直链的优先，都没有直链的看 extra 中的时长
                c_score = (1 if c.get("video_url") else 0)
                r_score = (1 if r.get("video_url") else 0)
                c_extra = c.get("extra", {})
                r_extra = r.get("extra", {})
                # extra 可能是 JSON 字符串，需要解析
                if isinstance(c_extra, str):
                    try:
                        c_extra = json.loads(c_extra)
                    except Exception:
                        c_extra = {}
                if isinstance(r_extra, str):
                    try:
                        r_extra = json.loads(r_extra)
                    except Exception:
                        r_extra = {}
                # 有 duration 信息的优先
                c_dur = c_extra.get("duration", 0)
                r_dur = r_extra.get("duration", 0)
                if isinstance(c_dur, str):
                    c_dur = 0
                if isinstance(r_dur, str):
                    r_dur = 0
                c_score += c_dur / 10000  # 时长加权
                r_score += r_dur / 10000
                
                if c_score > r_score:
                    result[i] = c
                dup = True
                break
        if not dup:
            result.append(c)
    return result


def pick(items, st, n):
    now = time.time()
    done = {e["key"] for e in st["dispatched"]} | {e["key"] for e in st["rejected"]}
    # 已发布过的 key/source_url：绝不能因 pending_retry 残留被重新派发
    # （2026-08-25 事故：同一视频 BV1yM8x6ZEZy 连续 4 天被重复投稿）
    published_slugs = set(st.get("published", {}).keys())
    published_keys = {e["key"] for e in st["dispatched"] if e.get("slug") in published_slugs}
    published_srcs = {info.get("source_url", "") for info in st.get("published", {}).values() if info.get("source_url")}
    # 重试项：3 次失败后会进 rejected，这里从 retry_list 重新加回候选
    retry_ready = []
    for x in st.get("pending_retry", []):
        if x.get("key") in published_keys:
            continue  # 已发布过，不再重试
        if (x.get("page_url") or "").strip() in published_srcs:
            continue
        if now - x.get("ts", 0) > 30 * 60 and x.get("retries", 0) < 3:
            retry_ready.append(x)
    done -= {x["key"] for x in retry_ready}
    cooling = {e["video_id"] for e in st["dispatched"]
               if now - e.get("ts", 0) < SAME_VIDEO_COOLDOWN}
    cooling |= {e["video_id"] for e in st["rejected"]}
    cooling -= {x["video_id"] for x in retry_ready}
    # 排除已发布的视频（防止重复采集自己发的）
    published_bvs = {info.get("bvid", "") for info in st.get("published", {}).values()}
    # 微博噪音：不是林园本人视频的常见噪音关键词
    NOISE_EXTRA = re.compile(
        r"超话|小说|灯花笑|张子墨|好事多墨|木槿|"
        r"白居易|绿含滋|水风清|四韵|诗词|古诗|"
        r"陈育哲|张甜甜|胡以信|案例黑料|跟风后悔|姐妹|"
        r"两园共|打榜|应援|周边|同人|"
        r"假价值|假投机|假风险|假茅|卖书|会员|"
        r"远离林园|大势已去|时运已去|"
        r"墓园|公墓|墓地|陵园|骨灰|"
        r"灯花笑|宏成|版税|打赏|签约|出版"
    )
    cands = []
    for it in items:
        url, page = it.get("video_url") or "", it.get("url") or ""
        # 兼容旧 data.json：video_url 可能在 extra 的 video_url / mp4_url 字段
        # （2026-08-29 修复：抖音/网易/好看的直链在 extra.mp4_url，之前只查顶层 video_url 导致被当「无视频」过滤）
        extra = it.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        if not url:
            url = extra.get("video_url", "") or extra.get("mp4_url", "")
        # 必须有视频：有直链、或B站链接、或腾讯页面（dispatch 会解析）、或 extra 标记 has_video
        has_video = (bool(url) or "bilibili.com/video/" in page
                     or "news.qq.com" in page or extra.get("has_video", False))
        if not has_video:
            continue
        # 有直链或B站链接 → 可用；有页面链接 → 也可用
        if not url and "bilibili.com/video/" not in page and not page:
            continue
        key = it.get("id") or page or url
        vid = video_id_of(page, url)
        if not key or key in done or vid in cooling:
            continue
        # 排除已发布的视频（BV 号匹配）
        if vid in published_bvs:
            continue
        title = it.get("title") or ""
        # 剪辑二创（碎片/标题党）→ 拒；完整原片 → 收
        if CLIP_TITLE_PAT.search(title) and not FULL_TITLE_PAT.search(title):
            continue
        if NOISE.search(title):
            continue                                     # 老虎公园不是林园
        if NOISE_EXTRA.search(title):
            continue                                     # 微博噪音
        # 标题必须包含"林园"（硬性要求，防止无关内容混入）
        if "林园" not in title:
            continue
        cands.append({"key": key, "video_id": vid,
                      "title": title[:60],
                      "video_url": url, "page_url": page,
                      "source": it.get("source", ""),
                      "author": it.get("author", ""),
                      "publish_time": it.get("publish_time") or "",
                      "extra": extra})
    # 同内容去重：标题相似度 > 60% 只保留一条，保留质量更好的
    # 同内容提前去重：微博同条内容被大量转发/重发，重复候选会污染排序。
    # 进池子前就按标题相似度去重（阈值 0.6），只留质量最好的 1 条。
    cands = dedup_by_title(cands)
    # 二次去重：标题前 12 字完全相同也视为同内容（转发时只改尾部的场景）
    seen_prefix = set()
    deduped = []
    for c in cands:
        pfx = (c["title"] or "")[:12]
        if pfx in seen_prefix:
            continue
        seen_prefix.add(pfx)
        deduped.append(c)
    cands = deduped
    # 过滤时长过短的（< 60 秒）和过长的（> 30 分钟）
    def _dur_ok(c):
        extra = c.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        dur = extra.get("duration", 0) or 0
        if isinstance(dur, str):
            try:
                dur = int(dur)
            except Exception:
                dur = 0
        return 90 <= dur <= 1800 or dur == 0  # 0=未知交给下载后检查；下限对齐 MIN_DUR=90，避免 60-90s 被调度后下载浪费
    cands = [c for c in cands if _dur_ok(c)]

    def source_score(c):
        """来源权威性评分。注意：B站搜索很多是二创，不绝对优先。"""
        src = c.get("source", "")
        if src.startswith("xueqiu"):
            return 30   # 雪球：通常是一手访谈/股东大会
        if src.startswith("tencent"):
            return 28   # 腾讯新闻：官方媒体
        if src.startswith("bilibili_api") or src.startswith("bilibili_space"):
            return 25   # B站官方 API/空间：较可靠
        if src.startswith("weibo"):
            return 20   # 微博：可能一手，也可能片段
        if src.startswith("bilibili_search"):
            return 15   # B站搜索：二创可能性高
        if src.startswith("douyin"):
            return 12
        if src.startswith("netease"):
            return 10
        return 10

    def title_score(t):
        t = t.lower()
        score = 0
        # 高质量关键词：完整访谈/路演/直播回放/最新采访
        high = ["完整版", "完整", "访谈", "路演", "直播回放", "最新采访", "最新发声", "全集", "最新", "股东大会"]
        for k in high:
            if k in t:
                score += 10
                break
        # 有具体数字/观点
        if __import__('re').search(r"\d+", t):
            score += 8
        # 核心投资主题
        for k in ["投资", "医药", "消费", "茅台", "垄断", "AI", "风险", "回报"]:
            if k in t:
                score += 4
        # "林园"开头（本人视频）
        if t.startswith("林园"):
            score += 5
        # 二创/剪辑类减分
        low = ["精华版", "剪辑", "混剪", "恶搞", "鬼畜", "吐槽", "Reaction", " reaction"]
        for k in low:
            if k in t:
                score -= 15
        return score

    def freshness_score(c):
        """发布时间越新越好，但同一场内容不重复。"""
        try:
            from datetime import datetime
            pt = datetime.fromisoformat(c.get("publish_time", ""))
            age_hours = (__import__('time').time() - pt.timestamp()) / 3600
            if age_hours < 0:
                return 20
            if age_hours <= 24:
                return 15
            if age_hours <= 72:
                return 10
            if age_hours <= 168:
                return 5
            return 0
        except Exception:
            return 5

    def duration_score(c):
        """已知时长且合适的加分。"""
        extra = c.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        dur = extra.get("duration", 0) or 0
        if isinstance(dur, str):
            try:
                dur = int(dur)
            except Exception:
                dur = 0
        if 120 <= dur <= 600:
            return 10
        if 600 < dur <= 1200:
            return 8
        if 60 <= dur < 120:
            return 2
        return 0  # 未知或太短/太长

    def quality_score(c):
        return (source_score(c) + title_score(c["title"]) +
                freshness_score(c) + duration_score(c))

    # 按综合质量分降序
    cands.sort(key=lambda c: quality_score(c), reverse=True)
    return cands[:n]


# ---------- 下载 ----------

_BILI_OPENER = None


def bili_opener():
    """带 buvid 指纹的会话。B站 CDN 对 curl/裸 UA 直接 403（FC 实测），
    必须走 fetch_bilibili.py 那套：首页 → finger/spi 拿 buvid3/4 → 同会话下载。"""
    global _BILI_OPENER
    if _BILI_OPENER is not None:
        return _BILI_OPENER
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0.0.0 Safari/537.36"),
                     ("Accept-Language", "zh-CN,zh;q=0.9"),
                     ("Referer", "https://www.bilibili.com/")]
    try:
        op.open("https://www.bilibili.com/", timeout=20).read()
        spi = json.loads(op.open(
            "https://api.bilibili.com/x/frontend/finger/spi", timeout=20).read().decode())
        for n, val in (("buvid3", spi["data"]["b_3"]), ("buvid4", spi["data"]["b_4"])):
            jar.set_cookie(http.cookiejar.Cookie(
                0, n, val, None, False, ".bilibili.com", True, False,
                "/", True, False, None, False, None, None, {}))
    except Exception as e:
        log.warning(f"buvid cookie 获取失败（可能触发 403/412）: {e}")
    _BILI_OPENER = op
    return op


def download(cand, dest, _retried=False):
    try:
        return _download_inner(cand, dest)
    except urllib.error.HTTPError as e:
        if e.code in (412, 403) and not _retried and not cand["video_url"]:
            # B站 WAF 是按会话/IP 时间窗挑战的，换新会话重来一次常能过
            global _BILI_OPENER
            _BILI_OPENER = None
            log.info(f"    {e.code}，换新会话重试一次")
            time.sleep(3)
            return download(cand, dest, _retried=True)
        raise


def weibo_refresh_url(page_url):
    """从微博页面 URL 重新获取视频直链（直链会过期）。"""
    import http.cookiejar, urllib.parse, re as _re
    mid = page_url.rstrip("/").split("/")[-1]
    log.info(f"    微博刷新直链: mid={mid}")
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")]
    # 访客票据
    fp = json.dumps({"os": "1", "browser": "Chrome120,0,0,0", "fonts": "undefined",
                     "screenInfo": "1920*1080*24", "plugins": ""})
    body = urllib.parse.urlencode({"cb": "gen_callback", "fp": fp}).encode()
    txt = op.open(urllib.request.Request(
        "https://passport.weibo.com/visitor/genvisitor",
        data=body, headers={"Content-Type": "application/x-www-form-urlencoded",
                           "Referer": "https://passport.weibo.com/visitor/visitor"}),
        timeout=25).read().decode("utf-8", "ignore")
    m = _re.search(r"\((\{.*\})\)", txt, _re.S)
    if not m:
        raise RuntimeError("微博 genvisitor 失败")
    tid = json.loads(m.group(1))["data"]["tid"]
    op.open(urllib.request.Request(
        f"https://passport.weibo.com/visitor/visitor?a=incarnate&t={urllib.parse.quote(tid)}"
        "&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand=0"), timeout=25).read()
    try:
        op.open("https://weibo.com/", timeout=20).read()
    except Exception:
        pass
    xsrf = next((c.value for c in jar if c.name == "XSRF-TOKEN"), "")
    # 搜索 API 找到这条微博
    data = json.loads(op.open(urllib.request.Request(
        f"https://weibo.com/ajax/statuses/show?id={mid}",
        headers={"Accept": "application/json", "X-XSRF-TOKEN": xsrf,
                 "Referer": f"https://weibo.com/"}), timeout=25).read().decode())
    media = (data.get("page_info") or {}).get("media_info") or {}
    vurl = (media.get("stream_url_hd") or media.get("stream_url")
            or media.get("mp4_hd_url") or media.get("mp4_sd_url") or "")
    if not vurl:
        raise RuntimeError("微博无视频或直链提取失败")
    log.info(f"    ✓ 新直链: {vurl[:60]}")
    return vurl


def tencent_resolve_url(page_url):
    """腾讯新闻页面 URL → (视频真直链, 时长秒)。两段解析：
    getWebVideo 拿 vid → getinfo 换真直链。
    playurl 字段是假直链（跳转页），必须走 getinfo。
    时长用于下载前预检，短视频直接跳过。"""
    m = re.search(r"(\d{8}[A-Z]\w+)", page_url or "")
    if not m:
        raise RuntimeError(f"无法提取腾讯文章 ID: {page_url}")
    art_id = m.group(1)
    log.info(f"    腾讯解析: article={art_id}")
    req = urllib.request.Request(
        f"https://i.news.qq.com/getWebVideo?id={art_id}&appver=29_android_7.6.10",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore"))
    if data.get("ret") != 0:
        raise RuntimeError(f"getWebVideo 失败 ret={data.get('ret')}")
    video = ((data.get("video_channel") or {}).get("video") or {})
    vid = video.get("vid") or ""
    if not vid:
        raise RuntimeError("腾讯文章无视频")
    # 时长格式 "MM:SS" → 秒，用于下载前预检
    dur = 0
    dm = re.match(r"(\d+):(\d+)(?::(\d+))?", str(video.get("duration") or ""))
    if dm:
        parts = [int(x) for x in dm.groups() if x is not None]
        dur = (parts[0] * 60 + parts[1]) if len(parts) == 2 else (parts[0] * 3600 + parts[1] * 60 + parts[2])
    info_req = urllib.request.Request(
        f"http://vv.video.qq.com/getinfo?vids={vid}&defaultfmt=mp4",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"})
    raw = urllib.request.urlopen(info_req, timeout=25).read().decode("utf-8", "ignore")
    if raw.startswith("<?xml"):
        em = re.search(r"<em>(\w+)</em>", raw)
        raise RuntimeError(f"getinfo 拒绝 vid={vid} code={em.group(1) if em else '?'}（视频可能已被限制播放）")
    info = json.loads(raw)
    item = ((info.get("vl") or {}).get("vl") or [{}])[0]
    host = ((item.get("ul") or {}).get("ui") or [{}])[0].get("url", "")
    fn, vkey = item.get("fn", ""), item.get("fvkey", "")
    if not (host and fn and vkey):
        raise RuntimeError("getinfo 字段缺失")
    url = f"{host.rstrip('/')}/{fn}?vkey={vkey}"
    log.info(f"    ✓ 腾讯直链: {url[:70]}")
    return url, dur


def _download_inner(cand, dest):
    if cand["video_url"] and "weibocdn" in cand["video_url"]:
        # 微博直链 → 带 Referer 下载
        log.info("    微博直链，带 Referer 下载")
        try:
            subprocess.run(["curl", "-sfL", "--max-time", "240",
                            "-H", "Referer: https://weibo.com/",
                            "-o", str(dest), cand["video_url"]], check=True)
        except subprocess.CalledProcessError:
            # 直链过期/403 → 刷新直链重试
            log.info("    直链下载失败，刷新微博直链")
            new_url = weibo_refresh_url(cand["page_url"])
            cand["video_url"] = new_url
            subprocess.run(["curl", "-sfL", "--max-time", "240",
                            "-H", "Referer: https://weibo.com/",
                            "-o", str(dest), new_url], check=True)
        if not dest.exists() or dest.stat().st_size < 10240:
            # 文件太小 → 可能是错误页面，刷新直链重试
            if cand["video_url"] and "weibocdn" in cand.get("video_url", ""):
                log.info("    文件异常，刷新微博直链")
                new_url = weibo_refresh_url(cand["page_url"])
                cand["video_url"] = new_url
                subprocess.run(["curl", "-sfL", "--max-time", "240",
                                "-H", "Referer: https://weibo.com/",
                                "-o", str(dest), new_url], check=True)
            if not dest.exists() or dest.stat().st_size < 10240:
                raise RuntimeError("微博直链下载失败")
    elif cand["video_url"]:
        # 其他直链 → 直接下载（带浏览器 UA + 防盗链 Referer）
        # （2026-08-29 修复：网易/好看/抖音 CDN 有防盗链，裸 curl 无 UA/Referer 会被 403）
        _ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
        _ref = "https://www.baidu.com/"
        _v = cand["video_url"]
        if "163.com" in _v or "netease" in str(cand.get("source", "")):
            _ref = "https://www.163.com/"
        elif "bdstatic" in _v or "haokan" in str(cand.get("source", "")):
            _ref = "https://haokan.baidu.com/"
        elif "snssdk" in _v or "douyin" in str(cand.get("source", "")):
            _ref = "https://www.douyin.com/"
        subprocess.run(["curl", "-sfL", "--max-time", "240",
                        "-H", f"User-Agent: {_ua}",
                        "-H", f"Referer: {_ref}",
                        "-o", str(dest), cand["video_url"]], check=True)
    else:                                                # B站：带指纹的会话走全程
        op = bili_opener()
        bvid = cand["video_id"]
        log.info(f"    view {bvid}")
        v = json.loads(op.open(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            timeout=30).read())
        cid = v["data"]["cid"]
        log.info("    playurl")
        p = json.loads(op.open(
            f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
            "&qn=32&fnval=1&platform=html5&high_quality=1", timeout=30).read())
        durl = (p.get("data") or {}).get("durl") or []
        if not durl:
            raise RuntimeError("无可用流")
        log.info("    下载流")
        req = urllib.request.Request(
            durl[0]["url"],
            headers={"Referer": cand["page_url"] or "https://www.bilibili.com/"})
        with op.open(req, timeout=240) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    dur = mp4_duration(dest)
    log.info(f"    文件大小: {dest.stat().st_size/1024:.0f} KB, 时长: {dur:.0f}s")
    if not (MIN_DUR <= dur <= MAX_DUR):
        raise RuntimeError(f"时长 {dur:.0f}s 不在 [{MIN_DUR},{MAX_DUR}]")
    return dur


# ---------- staging release 中转 ----------

def staging_release_id():
    try:
        return gh("GET", f"/releases/tags/{RELEASE_TAG}")["id"]
    except Exception:
        return gh("POST", "/releases", {
            "tag_name": RELEASE_TAG, "target_commitish": "main",
            "name": "Staging 素材中转", "prerelease": True})["id"]


def upload_asset(rel_id, path):
    name = Path(path).name
    assets = gh("GET", f"/releases/{rel_id}/assets?per_page=100")
    for a in (assets if isinstance(assets, list) else []):
        if a["name"] == name:
            gh("DELETE", f"/releases/assets/{a['id']}")
    up = f"https://uploads.github.com/repos/{REPO}/releases/{rel_id}/assets?name={name}"
    req = urllib.request.Request(up, data=Path(path).read_bytes(), method="POST",
                                 headers={"Authorization": f"Bearer {TOKEN}",
                                          "Content-Type": "video/mp4"})
    with urllib.request.urlopen(req, timeout=600) as r:
        assert json.loads(r.read())["state"] == "uploaded"
    return f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}/{name}"


# ---------- FC 路由（一个函数挂两个定时触发器）----------

def handler(event, context):
    """FC 统一入口：按触发器名字路由。

    触发器名含 dispatch → 每日调度；其余 → 投稿。
    （定时触发器的 event 是 JSON：{"triggerName": "...", "triggerTime": "..."}）
    """
    try:
        evt = json.loads(event or "{}")
    except Exception:
        evt = {}
    name = str(evt.get("triggerName", ""))
    log.info(f"触发器: {name or '（手动测试）'}")
    log_event("run", f"触发器 {name or '手动'} 开始运行")
    try:
        if "dispatch" in name:
            return dispatch_handler(event, context)
        return publish_handler(event, context)
    finally:
        flush_logs()


# ---------- Handler 1：每日调度 ----------

def dispatch_handler(event=None, context=None):
    st = load_state()
    # 调度动态控制：待投队列积压超过阈值就暂停调度，先消化（防积压爆炸 2026-08-27）
    pending_cnt = _pending_final_count(st)
    if pending_cnt >= PENDING_LIMIT:
        log.info(f"待投队列 {pending_cnt} 条，超阈值 {PENDING_LIMIT}，暂停调度，先消化积压")
        return {"dispatched": 0}
    items_raw = gh("GET", f"/contents/{DATA_JSON}?ref=main", raw=True)
    j = json.loads(items_raw.decode())
    items = j if isinstance(j, list) else j.get("items", [])
    cands = pick(items, st, MAX_ATTEMPTS)
    log.info(f"候选 {len(cands)} 条，今日目标成功 {MAX_PER_DAY} 条")

    rel = staging_release_id()
    tmp = Path(tempfile.mkdtemp())
    success = 0
    for i, c in enumerate(cands):
        if success >= MAX_PER_DAY:
            log.info(f"已达到今日目标 {MAX_PER_DAY} 条，停止调度")
            break
        import hashlib
        c["slug"] = "ly-" + time.strftime("%m%d") + "-" + \
                    hashlib.md5(c["key"].encode()).hexdigest()[:6]
        log.info(f"[{i+1}/{len(cands)}] {c['title'][:40]}")
        try:
            if not c["video_url"] and "bilibili.com/video/" in c["page_url"]:
                # B站 API 在 CI 侧反而通（FC 的阿里云 IP 被 WAF 412 整体拉黑），
                # 页面 URL 直接传给 CI，取源阶段由 ci_fetch_bilibili.py 拉流。
                asset_url = c["page_url"]
                dur = 0
                log.info("    B站源 → 透传页面 URL 给 CI 下载")
            elif "news.qq.com" in (c["page_url"] or ""):
                # 腾讯新闻：yt-dlp 下不了 blob 页面，FC 直接解析真直链下载
                vurl, dur_hint = tencent_resolve_url(c["page_url"])
                if dur_hint and not (MIN_DUR <= dur_hint <= MAX_DUR):
                    raise RuntimeError(f"腾讯时长预检 {dur_hint:.0f}s 不在 [{MIN_DUR},{MAX_DUR}]")
                dest = tmp / f"{c['slug']}.mp4"
                c2 = dict(c)
                c2["video_url"] = vurl
                dur = download(c2, dest)
                asset_url = upload_asset(rel, dest)
            elif c["video_url"]:
                # 有直链（微博等）→ FC 下载后上传到 staging release
                dest = tmp / f"{c['slug']}.mp4"
                dur = download(c, dest)
                asset_url = upload_asset(rel, dest)
            elif c["page_url"]:
                # 非B站但无直链（腾讯新闻/抖音等）→ 透传页面 URL 给 CI
                # CI 用 yt-dlp 下载，可能失败
                asset_url = c["page_url"]
                dur = 0
                log.info(f"    非B站源 → 透传页面 URL 给 CI 下载: {c['page_url'][:60]}")
            else:
                raise RuntimeError("无可用 URL")
            gh("POST", f"/actions/workflows/{WF_PRODUCE}/dispatches", {
                "ref": "main",
                "inputs": {"source": asset_url, "slug": c["slug"],
                           "speaker": "林园", "occasion": c["title"][:30],
                           "delay_hours": "0", "auto_publish": "false",
                           "source_platform": platform_of(c.get("source", ""))}})
            st["dispatched"].append({"key": c["key"], "video_id": c["video_id"],
                                     "slug": c["slug"], "ts": int(time.time()),
                                     "source_url": c["page_url"] or c["video_url"],
                                     "asset_url": asset_url,
                                     "title": c["title"], "delay_hours": 0,
                                     "source": c.get("source", ""),
                                     "author": c.get("author", ""),
                                     "publish_time": c.get("publish_time", "")})
            save_state(st)
            success += 1
            log_event("dispatch_ok", f"已调度 {c['slug']}（{dur:.0f}s）", c["title"][:60])
            log.info(f"    ✓ 已调度 {c['slug']}（{dur:.0f}s）")
        except Exception as e:
            log_event("fail", f"调度失败 {c.get('slug', c['key'])}", str(e)[:150])
            _record_failure(st, c, e)

    _process_retries(st)
    return {"dispatched": success, "attempted": len(cands)}


def _record_failure(st, c, e):
    """记录调度失败，3 次后才真正 rejected。"""
    retry_list = st.setdefault("pending_retry", [])
    existing = next((x for x in retry_list if x.get("key") == c["key"]), None)
    if existing:
        existing["retries"] = existing.get("retries", 0) + 1
        existing["last_error"] = str(e)
        existing["ts"] = int(time.time())
        if existing["retries"] >= 3:
            st["rejected"].append({"key": c["key"], "video_id": c["video_id"],
                                   "ts": int(time.time()), "error": str(e)})
            st["pending_retry"] = [x for x in retry_list if x.get("key") != c["key"]]
            log.warning(f"    ✗ {c.get('slug', c['key'])} 失败 3 次，移入 rejected: {e}")
        else:
            log.warning(f"    ✗ {c.get('slug', c['key'])} 失败 {existing['retries']}/3: {e}")
    else:
        retry_list.append({
            "key": c["key"], "video_id": c["video_id"],
            "title": c["title"], "video_url": c.get("video_url"),
            "page_url": c.get("page_url"), "source": c.get("source"),
            "extra": c.get("extra"), "retries": 1,
            "last_error": str(e), "ts": int(time.time())
        })
        log.warning(f"    ✗ {c.get('slug', c['key'])} 失败 1/3: {e}，稍后重试")
    save_state(st)


def _process_retries(st):
    """把冷却完成的重试项重新加入候选队列（在下次 dispatch 时重试）。"""
    retry_list = st.get("pending_retry", [])
    if not retry_list:
        return
    now = time.time()
    ready = [x for x in retry_list if now - x.get("ts", 0) > 30 * 60]
    if not ready:
        return
    # 重试项不在这里直接调度，只是从 retry 移到候选可见状态
    # 实际重试会在下次 dispatch 时由 pick() 重新处理
    log.info(f"有 {len(ready)} 条失败项达到重试冷却时间")


OWNER_MID = os.environ.get("BILI_MID", "275211725")  # 园来滚雪球


def bili_find_duplicate(title):
    """查自己 B站空间是否已传过同标题视频。
    2026-08-19 事故复盘：publish 上传成功但 save_state 失败 → 每小时重复上传，
    单日出现 5 个相同视频。这是最后防线：投稿前查重，已存在直接补记状态。
    2026-08-21 加强：模糊匹配——标题前 15 字相同即视为同一视频
    （同一素材重出片时 LLM 会生成不同标题，精确匹配拦不住）。"""
    if not title:
        return None
    try:
        import urllib.parse as _up
        url = (f"https://api.bilibili.com/x/series/recArchivesByKeywords"
               f"?mid={OWNER_MID}&keywords={_up.quote(title[:12])}&ps=10&pn=1")
        # 裸请求会被 B站 WAF 412，必须走带 buvid 指纹的会话
        op = bili_opener()
        req = urllib.request.Request(url, headers={
            "Referer": f"https://space.bilibili.com/{OWNER_MID}/video",
            "Accept": "application/json"})
        data = json.loads(op.open(req, timeout=20).read().decode("utf-8", "ignore"))
        for v in (data.get("data") or {}).get("archives") or []:
            vt = (v.get("title") or "").strip()
            # 精确匹配 或 前 15 字相同（同一素材重出的不同 LLM 标题）
            if vt == title.strip() or (len(vt) >= 15 and len(title.strip()) >= 15
                                       and vt[:15] == title.strip()[:15]):
                return v.get("bvid")
    except Exception as e:
        log.warning(f"B站查重失败（不阻断，但无法防重复）: {e}")
    return None


# ---------- Handler 2：投稿 ----------

def _has_unpublished_part(e, st):
    """判断 dispatched 条目是否还有未投的 part（长视频多条分次投稿）。
    单条/旧记录（无 parts_total 或 parts_total<=1）已投完就不算 pending。"""
    pub = st.get("published", {}).get(e["slug"])
    if not pub:
        return True  # 完全没投过
    parts_total = pub.get("parts_total", 1)
    if parts_total <= 1:
        return False  # 单条/旧记录已投完，不再 pending
    published_parts = e.get("published_parts", 0)
    return published_parts < parts_total


def _pending_final_count(st):
    """待投成片总数（所有素材剩余未投 part 之和），调度端用它防积压。"""
    total = 0
    for e in st.get("dispatched", []):
        if not (e.get("slug") and not e.get("failed")):
            continue
        pub = st.get("published", {}).get(e["slug"])
        if pub:
            parts_total = pub.get("parts_total", 1)
            if parts_total <= 1:
                continue  # 单条已投完
            total += max(0, parts_total - e.get("published_parts", 0))
        else:
            total += 1  # 还没投过，按至少 1 条估
    return total


def publish_handler(event=None, context=None):
    st = load_state()
    now = time.time()
    # 每天投片上限：长视频拆多条排队分天发，每天最多投 MAX_PUBLISH_PER_DAY 条成片
    today = time.strftime("%Y-%m-%d", time.gmtime(now + 8 * 3600))  # 北京时间
    dp = st.get("daily_publish") or {}
    if dp.get("date") != today:
        dp = {"date": today, "count": 0}
        st["daily_publish"] = dp
    if dp.get("count", 0) >= MAX_PUBLISH_PER_DAY:
        log.info(f"今日已投 {dp['count']} 条，达每日上限 {MAX_PUBLISH_PER_DAY}，剩余排队到明天")
        save_state(st)
        return {"published": 0}
    pending = [e for e in st["dispatched"]
               if e.get("slug") and not e.get("failed")
               and _has_unpublished_part(e, st)]
    # 轮转：已投条数最少的素材优先（防长视频霸占额度、新素材饿死 2026-08-27）
    pending.sort(key=lambda e: e.get("published_parts", 0))
    if not pending:
        log.info("无待投稿件")
        return {"published": 0}

    runs = gh("GET", f"/actions/workflows/{WF_PRODUCE}/runs"
                     "?status=success&per_page=30").get("workflow_runs", [])
    arts = {}
    art_ids = {}
    for run in runs:
        for a in gh("GET", f"/actions/runs/{run['id']}/artifacts").get("artifacts", []):
            if a["name"].startswith("deliver-") and not a.get("expired"):
                slug_key = a["name"][8:]
                arts[slug_key] = a["archive_download_url"]
                art_ids[slug_key] = a["id"]

    # 遍历 pending，找到第一个有 artifact 的
    # 无 artifact 且未超重试次数 → 自动重试出片
    # 超过 12 小时无 artifact → 标记为 failed，避免永远 pending
    e = None
    slug = None
    retried = 0
    for candidate in pending:
        s = candidate["slug"]
        # 上轮上传中断的（uploading 标记仍在）：绝不能盲目重传——先查 B站
        if candidate.get("uploading"):
            dup = bili_find_duplicate(candidate.get("upload_title") or "")
            if dup:
                st["published"][s] = {"bvid": dup, "ts": int(time.time()),
                                       "title": candidate.get("upload_title") or candidate.get("title", "")}
                candidate.pop("uploading", None)
                log_event("dedup", f"{s} 上轮其实已传过（{dup}），补记状态，不再重传")
                log.info(f"{s} 上轮其实已传过（{dup}），补记状态，不再重传")
                save_state(st)
            else:
                candidate["failed"] = True
                candidate.pop("uploading", None)
                log_event("fail", f"{s} 上轮上传状态未知且 B站查无此片，标记失败待人工确认（绝不自动重传）")
                log.error(f"{s} 上轮上传状态未知且 B站查无此片，标记失败待人工确认（绝不自动重传）")
                save_state(st)
            continue
        if s in arts:
            e = candidate
            slug = s
            break
        age = now - candidate.get("ts", 0)
        retries = candidate.get("retries", 0)
        last_retry = candidate.get("last_retry", 0)
        if age > 12 * 3600:
            candidate["failed"] = True
            log_event("fail", f"{s} 超过 12h 无成片，标记失败", (candidate.get("title") or "")[:60])
            log.info(f"{s} 超过 12h 无 artifact，标记失败")
        elif retries < 2 and now - max(candidate.get("ts", 0), last_retry) > 6 * 3600:
            # 自动重试出片：用之前保存的 asset_url 重新触发 workflow
            # 注意用 max(ts, last_retry)：刚调度的条目（出片还在跑）不能重触发
            asset_url = candidate.get("asset_url") or candidate.get("source_url")
            if asset_url:
                try:
                    gh("POST", f"/actions/workflows/{WF_PRODUCE}/dispatches", {
                        "ref": "main",
                        "inputs": {"source": asset_url, "slug": s,
                                   "speaker": "林园", "occasion": candidate["title"][:30],
                                   "delay_hours": "0", "auto_publish": "false"}})
                    candidate["retries"] = retries + 1
                    candidate["last_retry"] = int(now)
                    retried += 1
                    log.info(f"{s} 无 artifact，自动重试出片 ({retries+1}/2)")
                except Exception as retry_err:
                    log.warning(f"{s} 重试触发失败: {retry_err}")
            else:
                log.warning(f"{s} 无可用 asset_url，无法重试")
        else:
            log.info(f"{s} 成片未就绪，跳过")

    # 保存 failed 标记和重试记录
    if any(c.get("failed") for c in pending) or retried > 0:
        save_state(st)

    if e is None:
        if retried > 0:
            log.info(f"已触发 {retried} 条重试，等待出片完成")
        else:
            log.info("无可投稿件（所有待发布视频都无 artifact 或已超时）")
        return {"published": 0}

    log.info(f"准备投稿: {slug} (共 {len(pending)} 条待发布，本次只投 1 条)")

    tmp = Path(tempfile.mkdtemp())
    with open(tmp / "cookies.json", "w") as f:
        f.write(COOKIES_JSON)
    os.chmod(tmp / "cookies.json", 0o600)

    done = 0
    zf = tmp / f"{slug}.zip"
    dl = subprocess.run(["curl", "-sfL", "--max-time", "300", "-C", "-",
                         "-H", f"Authorization: Bearer {TOKEN}",
                         "-o", str(zf), arts[slug]], capture_output=True)
    if dl.returncode != 0:
        log.error(f"✗ {slug} artifact 下载失败: {dl.stderr.decode()[:200]}")
        # artifact 可能已过期被删，标记 failed 避免一直卡在这
        e["failed"] = True
        save_state(st)
        log.info(f"{slug} artifact 不可用，标记为失败")
        return {"published": 0}
    subprocess.run(["unzip", "-oq", str(zf), "-d", str(tmp / slug)], check=True)
    # 长视频拆多条：检测所有 final*.mp4（final.mp4 / final_1.mp4 ...）
    final_videos = sorted((tmp / slug).glob("final*.mp4"), key=lambda p: p.name)
    if not final_videos:
        log.error(f"✗ {slug} 成片不存在")
        return {"published": 0}

    # meta.json：可能是 dict（单条）或 list（多条），统一成 parts 列表
    meta_info = {}
    meta_f = tmp / slug / "meta.json"
    parts = []
    if meta_f.exists():
        try:
            mj = json.loads(meta_f.read_text(encoding="utf-8"))
            parts = mj if isinstance(mj, list) else [mj]
        except Exception:
            parts = []
    if not parts:
        parts = [{}]
    parts_total = len(parts)

    # 决定投第几条：已投的 part 数（长视频多条时分次投稿，防扎堆）
    k = e.get("published_parts", 0)
    if k >= parts_total:
        log.info(f"{slug} 的 {parts_total} 条已全部投完")
        return {"published": 0}
    part = parts[k]
    video = tmp / slug / part.get("final", "final.mp4")
    if not video.exists():
        video = final_videos[k] if k < len(final_videos) else final_videos[0]

    # 优先用当前 part 的 meta 文案 + 封面
    meta_info = part
    title = (part.get("title") or e.get("title") or slug)
    desc = part.get("desc", "")
    tags = ",".join(part.get("tags", ["林园", "价值投资"]))
    cover = None
    if part.get("cover") and (tmp / slug / part["cover"]).exists():
        cover = tmp / slug / part["cover"]
    if "｜" not in title:
        title = f"{title[:40]}｜林园"
    
    # FC 依赖层路径：尝试多个可能的路径
    import sys
    possible_paths = ["/opt/python", "/opt/python/lib/python3.10/site-packages", "/code/python"]
    log.info(f"Python 路径: {sys.path}")
    for path in possible_paths:
        if os.path.exists(path):
            log.info(f"层路径存在: {path}")
            if path not in sys.path:
                sys.path.insert(0, path)
                log.info(f"添加层路径: {path}")
        else:
            log.info(f"层路径不存在: {path}")
    
    # 检查 biliup 是否可用
    try:
        import biliup
        log.info(f"✓ biliup 可用: {biliup.__file__}")
    except ImportError as e:
        log.error(f"✗ biliup 不可用: {e}")
        log.error(f"  sys.path: {sys.path}")
        return {"published": 0}
    
    # 使用 subprocess 调用 biliup CLI，设置 PYTHONPATH 环境变量
    log.info(f"准备投稿: {slug}")
    
    # 构造命令
    cmd = [sys.executable, "-m", "biliup",
           "-u", str(tmp / "cookies.json"), "upload", str(video),
           "--title", title, "--tid", str(TID), "--copyright", str(COPYRIGHT),
           "--source", e.get("source_url") or "https://www.bilibili.com",
           "--desc", desc, "--tag", tags, "--limit", "1"]
    if cover:
        cmd += ["--cover", str(cover)]
    
    # 立即发布：cron 已按 6 时段（9/11/13/15/18/21）唤醒 + 每次只投 1 条，
    # 天然分散不扎堆，无需再算延迟发布时间（2026-08-29 去掉 pick_publish_slot 双轨制）
    log.info("立即发布（cron 时段已分散，无需延迟）")

    # ── 投稿前双重防护（2026-08-19 五连发事故）──
    # 0) 素材源查重：同一源视频（source_url）已发布过 → 绝不再投
    #    （2026-08-21 事故：同一素材重出片后 LLM 生成不同标题，标题查重失效）
    src_url = (e.get("source_url") or "").strip()
    if src_url:
        for pslug, pinfo in st.get("published", {}).items():
            if (pinfo.get("source_url") or "").strip() == src_url and pslug != slug:
                st["published"][slug] = {"bvid": pinfo.get("bvid"), "ts": int(time.time()),
                                          "title": pinfo.get("title") or title,
                                          "source_platform": pinfo.get("source_platform") or platform_of(e.get("source", "")),
                                          "source_url": src_url, "note": "同源素材已发布过，防重入拦截"}
                candidate_failed_note = f"同源 {pslug} 已发布（{pinfo.get('bvid')}），拦截重复投稿"
                log_event("dedup", f"⛔ {slug} 与已发布 {pslug} 同源，拦截", (e.get("title") or "")[:60])
                log.info(f"⛔ {slug} 与已发布 {pslug} 同源，拦截重复投稿")
                save_state(st)
                return {"published": 0}
    # 1) 查 B站是否已有同标题视频（状态丢失时的自愈防线）
    dup = bili_find_duplicate(title)
    if dup:
        st["published"][slug] = {"bvid": dup, "ts": int(time.time()), "title": title,
                                 "source_platform": meta_info.get("source_platform") or platform_of(e.get("source", ""))}
        save_state(st)
        log.info(f"⛔ {slug} 已在 B站存在（{dup}），补记状态跳过上传")
        return {"published": 1}
    # 2) 落盘上传意图：万一上传后崩溃，下轮凭 uploading 标记走恢复逻辑而非重传
    e["uploading"] = True
    e["upload_title"] = title
    save_state(st)
    
    # 设置 PYTHONPATH 环境变量
    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(sys.path)
    
    # 调用 biliup CLI
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r'BV\w{10}', out)
    if r.returncode == 0 and m:
        bvid = m.group(0)
        e.pop("uploading", None)
        e.pop("upload_title", None)
        # 记录这次投到第几条了（长视频多条时分次投稿）
        e["published_parts"] = k + 1
        st["daily_publish"]["count"] = st["daily_publish"].get("count", 0) + 1
        prev_pub = st.get("published", {}).get(slug, {})
        prev_bvids = prev_pub.get("bvids", []) + [bvid]
        # parts 列表：每条 part 记 bvid+title+ts，修复「长视频拆多条标题丢全」的 bug（2026-08-27）
        parts_log = list(prev_pub.get("parts", []))
        parts_log.append({"bvid": bvid, "title": title, "ts": int(time.time())})
        st["published"][slug] = {
            "bvid": bvid,  # 最新一条的 bvid
            "bvids": prev_bvids,
            "parts": parts_log,
            "parts_total": parts_total,
            "ts": int(time.time()),
            "title": title,
            "source_platform": meta_info.get("source_platform") or platform_of(e.get("source", "")),
            "source_url": e.get("source_url", ""),
            "watermark_cropped": meta_info.get("watermark_cropped", True),
            "subtitles_burned": meta_info.get("subtitles_burned", True),
            "has_existing_subtitles": meta_info.get("has_existing_subtitles", False),
            "vertical": meta_info.get("vertical", False),
            "duration_sec": meta_info.get("duration_sec", 0),
            "publish_time": e.get("publish_time", "") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        # 投稿成功 → 从 pending_retry 清理对应 key/source_url，防止重复派发
        st["pending_retry"] = [x for x in st.get("pending_retry", [])
                               if x.get("key") != e.get("key")
                               and (x.get("page_url") or "").strip() != (e.get("source_url") or "").strip()]
        # 只在所有 part 都投完时才删 artifact（否则下次还要投下一条）
        if slug in art_ids and k + 1 >= parts_total:
            try:
                gh("DELETE", f"/actions/artifacts/{art_ids[slug]}")
                log.info(f"✓ 已删除 artifact: deliver-{slug}")
            except Exception as ae:
                log.warning(f"删除 artifact 失败: {ae}")
        save_state(st)
        log_event("publish_ok", f"✅ 已投[{k+1}/{parts_total}] https://www.bilibili.com/video/{bvid}", title[:50])
        log.info(f"✅ 已投[{k+1}/{parts_total}] https://www.bilibili.com/video/{bvid}")
        done += 1
    else:
        # 输出完整错误信息，方便调试
        log_event("fail", f"✗ {slug} 投稿失败", f"rc={r.returncode} {((r.stdout or '') + (r.stderr or ''))[:150]}")
        log.error(f"✗ {slug} 投稿失败")
        log.error(f"  返回码: {r.returncode}")
        log.error(f"  stdout: {r.stdout[:500]}")
        log.error(f"  stderr: {r.stderr[:500]}")
        # 尝试提取错误代码
        tail = [ln for ln in out.splitlines() if "code" in ln or "Error" in ln or "error" in ln][-3:]
        if tail:
            log.error(f"  错误信息: {'; '.join(tail)[:300]}")

    return {"published": done}
