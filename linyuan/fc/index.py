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

MIN_DUR, MAX_DUR = 120, 1200            # 太短挑不出 3 段，太长 CI 转写拖不起
MAX_PER_DAY = 3                          # 每天最多成功调度几条
MAX_ATTEMPTS = 6                         # 每天最多尝试调度几条（含下载失败的）
DELAY_LADDER = [5, 8, 11]                # B站定时发布阶梯（必须 >4h）
SAME_VIDEO_COOLDOWN = 48 * 3600          # 同源冷却：同一场会切片不能连发
TID, COPYRIGHT = 207, 2                  # 财经商业 / 转载（转载必须带 source）

# 搜索噪音：标题命中即排除
# - 「虎林园」「东北虎林园」是老虎公园，不是林园本人
# - 「林园群」是人名「林园群」，不是投资人林园
# - 「横道河子」是东北虎产地
# - 「二埋汰」是东北虎网红名
NOISE = re.compile(r"虎林园|东北虎|横道河子|二埋汰|林园群")

# 投稿好时段：12:00, 18:00, 21:00（避开凌晨和深夜）
PUBLISH_SLOTS = [(12, 0), (18, 0), (21, 0)]


def get_publish_delay(slot_index: int) -> int:
    """计算距离下一个好时段的延迟小时数。
    
    Args:
        slot_index: 使用第几个时段（0=第一个可用，1=第二个...）
    
    Returns:
        延迟小时数（至少 1 小时，最多 24 小时）
    """
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=8)))

    # 收集今天剩余 + 明天的时段（北京时间）
    available_slots = []
    for hour, minute in PUBLISH_SLOTS:
        slot_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot_time > now:
            available_slots.append(slot_time)

    # 添加明天的时段
    tomorrow = now + timedelta(days=1)
    for hour, minute in PUBLISH_SLOTS:
        slot_time = tomorrow.replace(hour=hour, minute=minute, second=0, microsecond=0)
        available_slots.append(slot_time)
    
    # 选择第 slot_index 个可用时段
    if slot_index >= len(available_slots):
        slot_index = len(available_slots) - 1
    
    target_time = available_slots[slot_index]
    delay_hours = (target_time - now).total_seconds() / 3600
    
    # 向上取整到下一个整小时，确保到达目标时段
    import math
    delay_hours = math.ceil(delay_hours)
    
    # 限制范围：至少 1 小时，最多 24 小时
    return max(1, min(24, delay_hours))



TOKEN = os.environ.get("GITHUB_TOKEN", "")
COOKIES_JSON = os.environ.get("BILIBILI_COOKIES", "")

# FC 实例可复用（热启动），状态文件放 /tmp 能在短时间内防重；
# 冷启动会丢，所以 dispatched 名单同时维护在 GitHub 侧（见 state 函数）
STATE_KEY = "linyuan/.automation/fc_state.json"


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
            return
        except Exception as e:
            log.warning(f"save_state 第 {attempt+1} 次失败：{e}")
            time.sleep(2 * (attempt + 1))
    log.error("save_state 多次失败，本轮状态未保存（下轮会基于仓库里的旧状态重试）")


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
    # 重试项：3 次失败后会进 rejected，这里从 retry_list 重新加回候选
    retry_ready = []
    for x in st.get("pending_retry", []):
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
        # 兼容旧 data.json：video_url 可能在 extra 里
        extra = it.get("extra") or {}
        if not url:
            url = extra.get("video_url", "")
        # 必须有视频：有直链、或B站链接、或 extra 标记 has_video=True
        has_video = bool(url) or "bilibili.com/video/" in page or extra.get("has_video", False)
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
        if NOISE.search(title):
            continue                                     # 老虎公园不是林园
        if NOISE_EXTRA.search(title):
            continue                                     # 微博噪音
        # 标题必须包含"林园"（排除只是提到林园的视频）
        if "林园" not in title:
            continue
        cands.append({"key": key, "video_id": vid,
                      "title": title[:60],
                      "video_url": url, "page_url": page,
                      "source": it.get("source", ""),
                      "publish_time": it.get("publish_time") or "",
                      "extra": extra})
    # 同内容去重：标题相似度 > 60% 只保留一条，保留质量更好的
    cands = dedup_by_title(cands)
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
        return 60 <= dur <= 1800 or dur == 0  # 0 表示未知，交给下载后检查
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
        # 其他直链 → 直接下载
        subprocess.run(["curl", "-sfL", "--max-time", "240", "-o",
                        str(dest), cand["video_url"]], check=True)
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
    if "dispatch" in name:
        return dispatch_handler(event, context)
    return publish_handler(event, context)


# ---------- Handler 1：每日调度 ----------

def dispatch_handler(event=None, context=None):
    st = load_state()
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
        delay = get_publish_delay(success)
        log.info(f"[{i+1}/{len(cands)}] {c['title'][:40]}")
        try:
            if not c["video_url"] and "bilibili.com/video/" in c["page_url"]:
                # B站 API 在 CI 侧反而通（FC 的阿里云 IP 被 WAF 412 整体拉黑），
                # 页面 URL 直接传给 CI，取源阶段由 ci_fetch_bilibili.py 拉流。
                asset_url = c["page_url"]
                dur = 0
                log.info("    B站源 → 透传页面 URL 给 CI 下载")
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
                           "delay_hours": "0", "auto_publish": "false"}})
            st["dispatched"].append({"key": c["key"], "video_id": c["video_id"],
                                     "slug": c["slug"], "ts": int(time.time()),
                                     "source_url": c["page_url"] or c["video_url"],
                                     "asset_url": asset_url,
                                     "title": c["title"], "delay_hours": delay,
                                     "source": c.get("source", ""),
                                     "publish_time": c.get("publish_time", "")})
            save_state(st)
            success += 1
            log.info(f"    ✓ 已调度 {c['slug']}（{dur:.0f}s，定时 +{delay}h）")
        except Exception as e:
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


# ---------- Handler 2：投稿 ----------

def publish_handler(event=None, context=None):
    st = load_state()
    now = time.time()
    pending = [e for e in st["dispatched"]
               if e.get("slug") and e["slug"] not in st["published"]
               and not e.get("failed")]
    if not pending:
        log.info("无待投稿件")
        return {"published": 0}

    runs = gh("GET", f"/actions/workflows/{WF_PRODUCE}/runs"
                     "?status=success&per_page=10").get("workflow_runs", [])
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
        if s in arts:
            e = candidate
            slug = s
            break
        age = now - candidate.get("ts", 0)
        retries = candidate.get("retries", 0)
        last_retry = candidate.get("last_retry", 0)
        if age > 12 * 3600:
            candidate["failed"] = True
            log.info(f"{s} 超过 12h 无 artifact，标记失败")
        elif retries < 2 and now - last_retry > 6 * 3600:
            # 自动重试出片：用之前保存的 asset_url 重新触发 workflow
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
                except Exception as re:
                    log.warning(f"{s} 重试触发失败: {re}")
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
    video = tmp / slug / "final.mp4"
    if not video.exists():
        log.error(f"✗ {slug} 成片不存在")
        return {"published": 0}
    
    # 优先用 artifact 里 meta.json 的 LLM 文案 + 封面
    title, desc, tags, cover = e.get("title") or slug, "", "林园,价值投资", None
    meta_info = {}
    meta_f = tmp / slug / "meta.json"
    if meta_f.exists():
        try:
            mj = json.loads(meta_f.read_text(encoding="utf-8"))
            meta_info = mj
            title = mj.get("title") or title
            desc = mj.get("desc", "")
            tags = ",".join(mj.get("tags", ["林园", "价值投资"]))
            if mj.get("cover") and (tmp / slug / mj["cover"]).exists():
                cover = tmp / slug / mj["cover"]
                log.info(f"✓ 封面: {cover}")
            else:
                log.info(f"✗ 封面不存在: meta.cover={mj.get('cover')}")
        except Exception:
            pass
    else:
        log.info(f"✗ meta.json 不存在")
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
    
    # 定时发布：重新计算延迟，基于当前北京时间而不是 dispatch 时的时间
    delay_hours = get_publish_delay(0)
    delay_seconds = delay_hours * 3600
    cmd += ["--dtime", str(int(time.time()) + delay_seconds)]
    log.info(f"重新计算延迟: 目标时段 12:00/18:00/21:00（北京），延迟 {delay_hours}h")
    
    # 设置 PYTHONPATH 环境变量
    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(sys.path)
    
    # 调用 biliup CLI
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=env)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r'BV\w{10}', out)
    if r.returncode == 0 and m:
        bvid = m.group(0)
        st["published"][slug] = {
            "bvid": bvid,
            "ts": int(time.time()),
            "title": title,
            "source_platform": meta_info.get("source_platform", e.get("source", "")),
            "source_url": e.get("source_url", ""),
            "watermark_cropped": meta_info.get("watermark_cropped", True),
            "subtitles_burned": meta_info.get("subtitles_burned", True),
            "has_existing_subtitles": meta_info.get("has_existing_subtitles", False),
            "vertical": meta_info.get("vertical", False),
            "duration_sec": meta_info.get("duration_sec", 0),
            "publish_time": e.get("publish_time", "") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        # 投稿成功后删除 GitHub Actions artifact，避免占用空间
        if slug in art_ids:
            try:
                gh("DELETE", f"/actions/artifacts/{art_ids[slug]}")
                log.info(f"✓ 已删除 artifact: deliver-{slug}")
            except Exception as ae:
                log.warning(f"删除 artifact 失败: {ae}")
        save_state(st)
        log.info(f"✅ 已投 https://www.bilibili.com/video/{bvid}")
        done += 1
    else:
        # 输出完整错误信息，方便调试
        log.error(f"✗ {slug} 投稿失败")
        log.error(f"  返回码: {r.returncode}")
        log.error(f"  stdout: {r.stdout[:500]}")
        log.error(f"  stderr: {r.stderr[:500]}")
        # 尝试提取错误代码
        tail = [ln for ln in out.splitlines() if "code" in ln or "Error" in ln or "error" in ln][-3:]
        if tail:
            log.error(f"  错误信息: {'; '.join(tail)[:300]}")

    return {"published": done}
