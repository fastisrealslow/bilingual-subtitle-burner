#!/usr/bin/env python3
"""
多源监控框架 v2
支持：B站官方API、B站空间页、雪球个股讨论、微博搜索、腾讯新闻搜索
- 统一 item 格式
- SQLite 去重/diff
- 失败重试、风控退避
- 通过 OpenClaw Chromium CDP 获取登录态
"""
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).with_name("monitor_v2.db")
STATE_PATH = Path(__file__).with_name("monitor_v2_state.json")
# CDP 浏览器地址：本地开发时连已登录的浏览器拿登录态；
# 无浏览器环境（如 CI）探测不到会自动降级，跳过 BROWSER_REQUIRED 中的源。
CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:18800")

# 黑名单作者（2026-08-29）：这些账号的内容从源头排除，不进 data.json、不出片。
# - 园来滚雪球：自己的成片号（避免自己搬运自己）
# 注：竞品「园园滚雪球」不在此列——要保留监控（进 data.json 供分析），
#     只在 FC 选片层排除出片（见 fc/index.py COMPETITOR_AUTHORS）。
# 加号直接往集合里加名字即可。
BLACKLIST_AUTHORS = {"园来滚雪球"}

# 需要 CDP 浏览器（登录态 / JS 渲染）的源。
# 无浏览器环境下自动跳过，其余源仍可正常运行。
BROWSER_REQUIRED = {
    "bilibili_space",   # 空间页需 JS 渲染
    "xueqiu_search",    # WAF 拦截，尚未攻克
}


def cdp_available(url=CDP_URL, timeout=3):
    """探测 CDP 浏览器是否可用，用于决定运行模式。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{url}/json/version", timeout=timeout):
            return True
    except Exception:
        return False


def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            title TEXT,
            url TEXT,
            publish_time TEXT,
            author TEXT,
            extra TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON items(source)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_time ON items(publish_time)")
    conn.commit()
    conn.close()


def http_get(url, referer=None, ua=None, timeout=25):
    """统一的纯 HTTP GET，不依赖浏览器。用于无登录态即可访问的接口。"""
    import urllib.request
    headers = {
        "User-Agent": ua or ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


class Source(ABC):
    name = ""
    min_interval = 600

    def __init__(self, config, state):
        self.config = config
        self.state = state.setdefault(self.name, {})

    def can_fetch(self):
        last = self.state.get("last_fetch", 0)
        return time.time() - last >= self.min_interval

    def mark_fetched(self):
        self.state["last_fetch"] = time.time()

    def backoff(self, attempt):
        sleep = min(60 * (2 ** attempt) + random.uniform(1, 10), 300)
        time.sleep(sleep)

    @abstractmethod
    def fetch(self, page):
        pass


class BilibiliApiSource(Source):
    """B站官方 API: x/space/arc/search"""
    name = "bilibili_api"
    min_interval = 1800

    def fetch(self, page):
        uid = self.config["uid"]
        url = f"https://api.bilibili.com/x/space/arc/search?mid={uid}&ps=30&pn=1"
        page.goto(url, wait_until="networkidle", timeout=60000)
        time.sleep(2)
        raw = page.evaluate("""() => {
            try { return JSON.parse(document.body.innerText); }
            catch (e) { return {error: e.message, body: document.body.innerText.slice(0, 300), code: -999}; }
        }""")
        if raw.get("code") != 0:
            raise Exception(f"B站API错误: {raw.get('message')} (code={raw.get('code')})")

        items = []
        for v in raw["data"]["list"]["vlist"]:
            items.append({
                "id": f"bilibili_api:{v['bvid']}",
                "source": self.name,
                "title": v.get("title", ""),
                "url": f"https://www.bilibili.com/video/{v['bvid']}",
                "publish_time": datetime.fromtimestamp(v.get("created", 0)).isoformat(),
                "author": v.get("author", ""),
                "extra": json.dumps({
                    "bvid": v.get("bvid"),
                    "view_count": v.get("play", 0),
                    "duration": v.get("length", ""),
                    "is_charging": bool(v.get("is_charging_arc")),
                    "cover_url": v.get("pic", ""),
                    "comment_count": v.get("comment", 0),
                }, ensure_ascii=False),
            })
        return items


class BilibiliSearchSource(Source):
    """B站搜索关键词（空间页被风控时更稳定）"""
    name = "bilibili_search"
    min_interval = 1800

    # B站源「机构白名单」：只保留明确的一手机构/官方号，其余二创个人号全排除。
    # 2026-08-27 实证：B站源 141 条里机构号 <10 条，关键词黑名单打地鼠盖不住
    # （二创号名字千奇百怪），改成白名单只吃机构，一手素材靠微博/股东大会等源。
    # 「完整原片」识别：不认作者、认内容形态（选片层会再判断一次，这里先粗筛减少噪音）
    FULL_TITLE_PAT = re.compile(
        r"完整|全纪录|全记录|访谈|实录|直播|演讲|全程|发言|现场|对话|采访|股东会|路演|专访")
    CLIP_TITLE_PAT = re.compile(
        r"金句|十大观点|秘诀|股神|曝光|惊人|精华|速看|语录|震撼|必看|揭秘|真相|名场面|划重点|一分钟|三分钟|解读|盘点|总结|五大|几条|个方法|条铁律")

    EXTRACT_JS = """
    () => {
        const items = [];
        const seen = new Set();
        document.querySelectorAll('a[href*="/video/BV"]').forEach(a => {
            if (a.parentElement?.className !== 'bili-video-card__info--right') return;
            const href = a.getAttribute('href');
            const match = href.match(/\\/video\\/(BV[\\w]+)/);
            if (!match) return;
            const bvid = match[1];
            if (seen.has(bvid)) return;
            seen.add(bvid);
            const title = a.innerText.trim();
            let card = a.closest('.bili-video-card');
            const allText = card ? card.innerText.split('\\n').map(t => t.trim()).filter(t => t) : [title];
            let viewText = '';
            for (const t of allText) {
                if (/^[\\d.]+万?$/.test(t) && t !== title) { viewText = t; break; }
            }
            let up = '';
            const upLink = card ? card.querySelector('a[href*="space.bilibili.com"]') : null;
            if (upLink) up = upLink.innerText.trim();
            items.push({bvid, title, viewText, up});
        });
        return items;
    }
    """

    def _fetch_via_api(self, keyword):
        """纯 HTTP 路径：B站搜索 API 无需登录态，实测 code=0 可直接返回结果。"""
        import urllib.parse
        kw = urllib.parse.quote(keyword)
        url = ("https://api.bilibili.com/x/web-interface/wbi/search/type"
               f"?search_type=video&keyword={kw}&page=1")
        data = json.loads(http_get(url, referer="https://www.bilibili.com/"))
        if data.get("code") != 0:
            raise RuntimeError(f"B站 API code={data.get('code')} {data.get('message')}")
        out = []
        for v in (data.get("data", {}).get("result") or []):
            bvid = v.get("bvid")
            if not bvid:
                continue
            out.append({
                "bvid": bvid,
                "title": re.sub(r"<[^>]+>", "", v.get("title", "")),
                "viewText": "",
                "view_count": v.get("play") or 0,
                "up": v.get("author", ""),
            })
        return out

    def fetch(self, page):
        keyword = self.config.get("keyword", "林园")
        raw_items = None
        try:
            raw_items = self._fetch_via_api(keyword)
        except Exception as e:
            if page is None:
                raise
            print(f"[{self.name}] API 路径失败，回退浏览器: {e}", file=sys.stderr)

        if raw_items is None:
            url = f"https://search.bilibili.com/all?keyword={keyword}"
            page.goto(url, wait_until="networkidle", timeout=60000)
            time.sleep(3)
            raw_items = page.evaluate(self.EXTRACT_JS)

        items = []
        for v in raw_items:
            up = v.get("up", "")
            # 剪辑二创标题 → 拒；完整原片 → 收（按内容形态，不按作者）
            if self.CLIP_TITLE_PAT.search(v.get("title", "")) and not self.FULL_TITLE_PAT.search(v.get("title", "")):
                continue
            view_count = v.get("view_count") or 0
            vt = v.get("viewText", "")
            if not view_count and vt:
                try:
                    view_count = int(float(vt.replace("万", "")) * (10000 if "万" in vt else 1))
                except Exception:
                    pass
            items.append({
                "id": f"bilibili_search:{v['bvid']}",
                "source": self.name,
                "title": v["title"],
                "url": f"https://www.bilibili.com/video/{v['bvid']}",
                "publish_time": datetime.now().strftime("%Y-%m-%d 00:00:00"),
                "author": up,
                "extra": json.dumps({"bvid": v["bvid"], "view_count": view_count}, ensure_ascii=False),
            })
        return items


class BilibiliSpaceSource(Source):
    """B站空间页抓取（备用）"""
    name = "bilibili_space"
    min_interval = 1800

    EXTRACT_JS = """
    () => {
        const items = [];
        const seen = new Set();
        document.querySelectorAll('a[href*="/video/BV"]').forEach(a => {
            if (a.parentElement?.className !== 'bili-video-card__title') return;
            const href = a.getAttribute('href');
            const match = href.match(/\\/video\\/(BV[\\w]+)/);
            if (!match) return;
            const bvid = match[1];
            if (seen.has(bvid)) return;
            seen.add(bvid);
            const title = a.innerText.trim();
            let card = a.closest('.bili-video-card');
            const allText = card ? card.innerText.split('\\n').map(t => t.trim()).filter(t => t) : [title];
            let dateText = '';
            for (const t of allText) {
                if (/^(昨天|今天|\\d{2}-\\d{2}|\\d{4}-\\d{2}-\\d{2})$/.test(t)) { dateText = t; break; }
            }
            items.push({bvid, title, dateText});
        });
        return items;
    }
    """

    def fetch(self, page):
        uid = self.config["uid"]
        page.goto(f"https://space.bilibili.com/{uid}/video", wait_until="networkidle", timeout=60000)
        time.sleep(3)
        raw_items = page.evaluate(self.EXTRACT_JS)

        items = []
        today = datetime.now()
        for v in raw_items:
            date_text = v["dateText"]
            if date_text == "今天":
                pt = today
            elif date_text == "昨天":
                pt = today - timedelta(days=1)
            elif date_text and len(date_text) == 5:
                mm, dd = date_text.split("-")
                pt = today.replace(month=int(mm), day=int(dd))
            else:
                pt = today
            items.append({
                "id": f"bilibili_space:{v['bvid']}",
                "source": self.name,
                "title": v["title"],
                "url": f"https://www.bilibili.com/video/{v['bvid']}",
                "publish_time": pt.strftime("%Y-%m-%d 00:00:00"),
                "author": self.config.get("name", ""),
                "extra": json.dumps({"bvid": v["bvid"]}, ensure_ascii=False),
            })
        return items


class XueqiuSearchSource(Source):
    """雪球个股讨论监控（雪球搜索页被风控，改抓个股讨论区并过滤林园）"""
    name = "xueqiu_search"
    min_interval = 1800

    EXTRACT_JS = """
    () => {
        const items = [];
        document.querySelectorAll('article').forEach(art => {
            const links = Array.from(art.querySelectorAll('a[href*="/"]'));
            const authorLink = links.find(a => {
                const t = a.innerText.trim();
                return t.length > 0 && t.length < 20 && !t.includes('分钟') && !t.includes('小时') && !t.includes('来自') && !t.includes('前·');
            });
            const timeLink = links.find(a => {
                const t = a.innerText.trim();
                return t.includes('分钟前') || t.includes('小时前') || t.includes('秒前') || t.includes('修改于') || t.includes('天前');
            });
            const titleEl = art.querySelector('h3');
            const textEl = Array.from(art.querySelectorAll('p, div')).find(el => el.innerText.trim().length > 20);
            const text = (titleEl ? titleEl.innerText : textEl ? textEl.innerText : '').trim();
            if (text.length < 10) return;

            let href = timeLink ? timeLink.getAttribute('href') : '';
            if (href && !href.startsWith('http')) href = 'https://xueqiu.com' + href;

            items.push({
                text: text.slice(0, 200),
                href: href,
                author: authorLink ? authorLink.innerText.trim() : ''
            });
        });
        return items;
    }
    """

    def fetch(self, page):
        # 雪球 WAF 需要浏览器执行 JS 才能通过，无浏览器环境（如 CI）直接跳过
        if page is None:
            raise RuntimeError("雪球需浏览器环境（WAF 挑战需执行 JS），当前无浏览器，跳过")
        symbols = self.config.get("symbols", ["SH600519", "SH600436", "SH600329"])
        keyword = self.config.get("keyword", "林园")
        all_items = []
        seen = set()

        for symbol in symbols:
            url = f"https://xueqiu.com/S/{symbol}"
            page.goto(url, wait_until="networkidle", timeout=60000)
            time.sleep(3)
            # 向下滚动触发讨论区加载
            for _ in range(3):
                page.evaluate("() => window.scrollBy(0, 800)")
                time.sleep(1.5)
            results = page.evaluate(self.EXTRACT_JS)

            for r in results:
                if keyword not in r["text"]:
                    continue
                key = r["href"] or r["text"][:50]
                if key in seen:
                    continue
                seen.add(key)
                # href 是帖子永久链接，用它做稳定 ID（不用 hash()，原因同腾讯源）
                stable_id = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
                # 雪球正文开头带「作者+N小时前」等相对时间，会随抓取时刻变化，
                # 剥离后才能避免同一帖子被当成新内容
                clean = re.sub(r"^.{0,20}?(?:\d+分钟前|\d+小时前|\d+天前|\d{2}-\d{2}\s*\d{2}:\d{2})"
                               r"[^\n]{0,12}\n?", "", r["text"]).strip() or r["text"]
                all_items.append({
                    "id": f"xueqiu_search:{stable_id}",
                    "source": self.name,
                    "title": clean[:200],
                    "url": r["href"] or url,
                    "publish_time": datetime.now().isoformat(),
                    "author": r["author"],
                    "extra": json.dumps({"symbol": symbol}, ensure_ascii=False),
                })
        return all_items


class WeiboSearchSource(Source):
    """微博关键词搜索。

    纯 HTTP 实现：通过 passport 访客系统换取临时票据（无需登录），
    再请求 weibo.com/ajax/statuses/search 拿完整正文。
    注：m.weibo.cn 的接口拒绝访客票据（返回 ok=-100），PC 端接口可以。
    """
    name = "weibo_search"
    min_interval = 1800
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

    # 同名干扰过滤（东北虎林园、华林园景区、互评群等）
    NOISE = re.compile(r"东北虎林园|虎林园|华林园|林园酒店|林园饭店|林园景区|"
                       r"橘子林园|林园小区|动物园|互评群|壁垒")
    SIGNAL = re.compile(r"投资|股|市值|仓|私募|基金|财经|估值|分红|茅台|片仔癉|"
                        r"价值|巴菲特|毛利|企业|消费|医药|中药|A股|牛市|买入|持有")

    def _visitor_session(self):
        """换取微博访客票据，返回（opener, xsrf_token）。"""
        import http.cookiejar
        import urllib.parse
        import urllib.request

        jar = http.cookiejar.CookieJar()
        op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        def rq(url, headers=None, data=None):
            h = {"User-Agent": self.UA, "Accept-Language": "zh-CN,zh;q=0.9"}
            h.update(headers or {})
            return op.open(urllib.request.Request(url, data=data, headers=h),
                           timeout=25).read().decode("utf-8", "ignore")

        fp = json.dumps({"os": "1", "browser": "Chrome120,0,0,0", "fonts": "undefined",
                         "screenInfo": "1920*1080*24", "plugins": ""})
        body = urllib.parse.urlencode({"cb": "gen_callback", "fp": fp}).encode()
        txt = rq("https://passport.weibo.com/visitor/genvisitor",
                 {"Content-Type": "application/x-www-form-urlencoded",
                  "Referer": "https://passport.weibo.com/visitor/visitor"}, body)
        m = re.search(r"\((\{.*\})\)", txt, re.S)
        if not m:
            raise RuntimeError("genvisitor 返回异常")
        tid = json.loads(m.group(1))["data"]["tid"]

        rq("https://passport.weibo.com/visitor/visitor?a=incarnate&t="
           + urllib.parse.quote(tid)
           + "&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand=" + str(time.time()))
        try:
            rq("https://weibo.com/")
        except Exception:
            pass

        xsrf = next((c.value for c in jar if c.name == "XSRF-TOKEN"), "")
        return rq, xsrf

    def _relevant(self, text):
        """过滤同名干扰：命中噪声词且无投资信号则丢弃。"""
        if self.NOISE.search(text) and not self.SIGNAL.search(text):
            return False
        return True

    def fetch(self, page):
        import urllib.parse
        keyword = self.config["keyword"]
        rq, xsrf = self._visitor_session()
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://s.weibo.com/weibo?q={urllib.parse.quote(keyword)}",
            "X-Requested-With": "XMLHttpRequest",
            "X-XSRF-TOKEN": xsrf,
        }

        statuses = []
        for pg in (1, 2):
            url = ("https://weibo.com/ajax/statuses/search"
                   f"?q={urllib.parse.quote(keyword)}&page={pg}")
            try:
                data = json.loads(rq(url, headers))
            except Exception as e:
                if pg == 1:
                    raise
                print(f"[{self.name}] 第{pg}页失败: {e}", file=sys.stderr)
                break
            batch = data.get("statuses") or []
            if not batch:
                break
            statuses.extend(batch)

        items, seen, dropped = [], set(), 0
        for s in statuses:
            mid = s.get("mid")
            text = re.sub(r"<[^>]+>", "", s.get("text_raw") or s.get("text") or "").strip()
            if not mid or not text or len(text) < 5 or mid in seen:
                continue
            if not self._relevant(text):
                dropped += 1
                continue
            seen.add(mid)

            media = (s.get("page_info") or {}).get("media_info") or {}
            video_url = (media.get("stream_url_hd") or media.get("stream_url")
                         or media.get("mp4_hd_url") or media.get("mp4_sd_url") or "")
            cover = (s.get("page_info") or {}).get("page_pic") or ""
            if isinstance(cover, dict):
                cover = cover.get("url", "")

            items.append({
                "id": f"weibo_search:{mid}",
                "source": self.name,
                "title": text[:300],
                "url": f"https://m.weibo.cn/detail/{mid}",
                "publish_time": datetime.now().isoformat(),
                "author": (s.get("user") or {}).get("screen_name", ""),
                "extra": json.dumps({
                    "mid": mid,
                    "has_video": bool(video_url),
                    "video_url": video_url,
                    # 微博 CDN 直链带 Expires，实测仅 1 小时有效。
                    # 存库只作快照，过期后需重新抓取；下载时要带 Referer。
                    "video_expires_at": (int(re.search(r"Expires=(\d+)", video_url).group(1))
                                         if video_url and re.search(r"Expires=(\d+)", video_url) else 0),
                    "video_refreshed_at": int(time.time()) if video_url else 0,
                    "need_referer": "https://weibo.com/" if video_url else "",
                    "cover": cover,
                    "raw_time": s.get("created_at", ""),
                    "reposts": s.get("reposts_count", 0),
                    "comments": s.get("comments_count", 0),
                }, ensure_ascii=False),
            })
        if dropped:
            print(f"[{self.name}] 过滤同名干扰 {dropped} 条", file=sys.stderr)
        return items


class TencentLiveSource(Source):
    """腾讯新闻 林园关键词搜索（含视频直链提取）"""
    name = "tencent_live"
    min_interval = 1800
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    TENCENT_ARTICLE_RE = re.compile(r"/rain/a/([A-Za-z0-9]+)")

    def _fetch_via_api(self, keyword):
        """纯 HTTP 路径：腾讯作者流 JSON 接口，无需登录态。

        这些 suid 是林园内容的原始发布方（从已入库文章反查 card.suid 得到）。
        相比搜索页，直接盯发布方能拿到更接近源头的素材；
        其中私募排排网自己的站点是前端渲染抓不到，但通过腾讯这个接口可以。

        ⚠ suid 失效时接口仍返回 ret=0 但 newslist 为空（静默失败），
           所以这里统计每个 suid 的产出，全部为空时告警。
        """
        feeds = self.config.get("feeds") or [
            "8QMY3Hxd7YIevjs=",      # 腾讯财经
            "8QMf2nxf6IMduTfa",      # 私募排排网
            "8QMc239d5YAcvDnZ",      # 时代财经
            "8QIf3n5f6YceuTrY7wM=",  # 财闻
        ]
        import urllib.parse
        seen, out = set(), []
        feed_yield = {}
        for suid in feeds:
            raw_total = 0
            for tab in ("om_video", "om_article"):
                url = ("https://i.news.qq.com/getSubNewsMixedList?offset_info=0"
                       f"&guestSuid={urllib.parse.quote(suid)}&tabId={tab}&caller=1&from_scene=105")
                try:
                    data = json.loads(http_get(url, referer="https://news.qq.com/"))
                except Exception:
                    continue
                lst = data.get("newslist") or (data.get("data") or {}).get("newslist") or []
                raw_total += len(lst)
                for n in lst:
                    title = n.get("title", "")
                    aid = n.get("id", "")
                    if not aid or keyword not in title or aid in seen:
                        continue
                    seen.add(aid)
                    out.append({"id": aid, "title": title,
                                "url": f"https://news.qq.com/rain/a/{aid}"})
                time.sleep(0.2)
            feed_yield[suid] = raw_total

        dead = [s for s, n in feed_yield.items() if n == 0]
        if dead:
            print(f"[{self.name}] ⚠ 以下发布方 suid 零产出，可能已失效: {dead}",
                  file=sys.stderr)
        if feed_yield and all(n == 0 for n in feed_yield.values()):
            raise RuntimeError("所有腾讯发布方 suid 均零产出，接口可能已变更")
        return out

    def _resolve_tencent_mp4(self, vid):
        """用 vid 换真实 MP4 直链。

        注意：getWebVideo 返回的 playurl 是个跳转页（实测返回 application/json），
        不是可下载的视频。真直链要走腾讯视频的 getinfo 接口：
        文件名(fn) + CDN host(ul.ui[].url) + vkey 拼接。
        """
        url = (f"https://vv.video.qq.com/getinfo?vids={vid}"
               "&platform=101001&charge=0&otype=json&defn=shd")
        raw = http_get(url, referer="https://v.qq.com/", ua=self.UA, timeout=25)
        data = json.loads(re.sub(r"^QZOutputJson=|;$", "", raw.strip()))
        vi = (data.get("vl") or {}).get("vi") or []
        if not vi:
            return {}
        v = vi[0]
        fn = v.get("fn", "")
        fvkey = v.get("fvkey", "")
        hosts = [ui.get("url", "") for ui in ((v.get("ul") or {}).get("ui") or []) if ui.get("url")]
        if not fn or not hosts:
            return {}
        links = [f"{h}{fn}?vkey={fvkey}" if fvkey else f"{h}{fn}" for h in hosts]
        return {"mp4_url": links[0], "mp4_mirrors": links[1:3],
                "duration_sec": v.get("td", "")}

    def _enrich_video(self, article_id):
        """用 getWebVideo 补齐视频信息（纯 HTTP，无需浏览器）。"""
        url = f"https://i.news.qq.com/getWebVideo?id={article_id}&appver=29_android_7.6.10"
        data = json.loads(http_get(url))
        if data.get("ret") != 0:
            return {}
        vc = data.get("video_channel") or {}
        video = (vc.get("video") or {}) if isinstance(vc, dict) else {}
        if not video.get("vid"):
            return {}
        vid = video.get("vid")
        out = {
            "article_id": article_id,
            "has_video": True,
            "vid": vid,
            "video_page": video.get("playurl", ""),
            "video_cover": video.get("img", ""),
            "video_duration": video.get("duration", ""),
            "tencent_video_page": f"https://v.qq.com/x/page/{vid}.html",
            # 真实发布时间（顶层 time 字段），用于与二创比对早晚
            "published_at": data.get("time", ""),
        }
        try:
            out.update(self._resolve_tencent_mp4(vid))
        except Exception as e:
            out["mp4_error"] = str(e)[:60]
        return out

    def fetch(self, page):
        keyword = self.config.get("keyword", "林园")

        # 优先纯 HTTP（无浏览器也能跑）
        api_items = []
        api_failed = None
        try:
            api_items = self._fetch_via_api(keyword)
        except Exception as e:
            api_failed = e
            print(f"[{self.name}] 作者流接口失败: {e}", file=sys.stderr)

        results = [{"title": x["title"], "href": x["url"]} for x in api_items]

        # 有浏览器时额外跑搜索页，扩大覆盖
        if page is not None:
            try:
                url = f"https://news.qq.com/search?query={keyword}"
                page.goto(url, wait_until="networkidle", timeout=60000)
                time.sleep(3)
                web = page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        title: a.innerText.trim(),
                        href: a.getAttribute('href')
                    })).filter(x => x.title.length > 10 && x.title.includes('%s'));
                }""" % keyword)
                results.extend(web)
            except Exception as e:
                print(f"[{self.name}] 搜索页失败，仅用 API 结果: {e}", file=sys.stderr)

        items = []
        seen = set()
        # 无浏览器时作者流是唯一数据来源，它挂了就必须报错，
        # 否则会静默返回空列表，看上去“抓取成功 0 条”
        if api_failed is not None and page is None:
            raise api_failed
        for r in results[:30]:
            href = r["href"]
            if not href or href.startswith('javascript'):
                continue
            if not href.startswith("http"):
                href = "https://news.qq.com" + href
            if href in seen:
                continue
            seen.add(href)

            extra = {"article_url": href}
            m = self.TENCENT_ARTICLE_RE.search(href)
            if m:
                try:
                    extra.update(self._enrich_video(m.group(1)))
                except Exception as e:
                    extra["video_error"] = str(e)

            # 用文章 ID 作为稳定标识，不用 hash()——
            # Python 的 hash() 受 PYTHONHASHSEED 影响，每次进程启动结果不同，
            # 按天运行会导致同一文章反复入库。
            m_id = self.TENCENT_ARTICLE_RE.search(href)
            stable_id = m_id.group(1) if m_id else hashlib.md5(href.encode()).hexdigest()[:16]

            items.append({
                "id": f"tencent_live:{stable_id}",
                "source": self.name,
                "title": r["title"][:200],
                "url": href,
                "publish_time": (extra.get("published_at") or datetime.now().isoformat()),
                "author": "腾讯新闻",
                "extra": json.dumps(extra, ensure_ascii=False),
            })
        return items


class DouyinVideoSource(Source):
    """抖音单视频解析。

    纯 HTTP 实现：走 iesdouyin 分享页，从 _ROUTER_DATA 中取 play_addr。
    相比 yt-dlp 路径的优势：不需 __ac_nonce/cookie、不需浏览器、返回无水印地址，
    并能拿到真实发布时间与点赞数。主站 www.douyin.com 有反爬，但分享页没有。
    """
    name = "douyin_video"
    min_interval = 1800
    UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
    VID_RE = re.compile(r"(\d{15,25})")

    def _parse_share(self, vid):
        """从分享页提取视频信息（无需登录态）。"""
        html = http_get(f"https://www.iesdouyin.com/share/video/{vid}/",
                        referer="https://www.douyin.com/", ua=self.UA, timeout=30)
        m = re.search(r"_ROUTER_DATA\s*=\s*(\{.*?\})\s*</script>", html, re.S)
        if not m:
            raise RuntimeError("未找到 _ROUTER_DATA")
        data = json.loads(m.group(1))
        item = None
        for _, v in (data.get("loaderData") or {}).items():
            if not isinstance(v, dict):
                continue
            lst = ((v.get("videoInfoRes") or {}).get("item_list")) or []
            if lst:
                item = lst[0]
                break
        if not item:
            raise RuntimeError("item_list 为空（视频可能已删除或私密）")

        video = item.get("video") or {}
        urls = (video.get("play_addr") or {}).get("url_list") or []
        if not urls:
            raise RuntimeError("无 play_addr")
        # playwm = 带水印，换成 play 即无水印
        play = urls[0].replace("/playwm/", "/play/")
        cover = (video.get("cover") or {}).get("url_list") or [""]
        stats = item.get("statistics") or {}
        ctime = item.get("create_time") or 0
        return {
            "title": item.get("desc", "") or f"抖音视频 {vid}",
            "mp4_url": play,
            "thumbnail": cover[0],
            "author": (item.get("author") or {}).get("nickname", "") or "抖音",
            "digg": stats.get("digg_count", 0),
            "comment": stats.get("comment_count", 0),
            "upload_date": (datetime.fromtimestamp(ctime).strftime("%Y%m%d") if ctime else ""),
            "create_time": ctime,
            "duration": round(((item.get("video") or {}).get("duration")
                              or item.get("duration") or 0) / 1000),
        }

    def fetch(self, page):
        urls = list(self.config.get("urls", []))
        seeds_file = self.config.get("seeds_file")
        if seeds_file:
            seeds_path = Path(__file__).with_name(seeds_file)
            if seeds_path.exists():
                try:
                    seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
                    urls.extend(seeds.get("urls", []))
                except Exception as e:
                    print(f"[{self.name}] 种子文件读取失败: {e}", file=sys.stderr)
        if not urls:
            return []

        # 已入库的视频不重复请求，避免触发抖音 IP 限流。
        # 抖音直链会过期，按 refresh_days 周期性刷新。
        refresh_days = int(self.config.get("refresh_days", 7))
        known = {}
        try:
            conn = sqlite3.connect(DB_PATH)
            for rid, extra, upd in conn.execute(
                    "SELECT id, extra, updated_at FROM items WHERE source = ?", (self.name,)):
                known[rid.split(":", 1)[-1]] = (extra, upd)
            conn.close()
        except Exception as e:
            # 读不到已入库直链只是失去缓存优化，不影响抓取，但要可见
            print(f"[{self.name}] 读取已入库直链失败（将全量重拓）: {type(e).__name__}",
                  file=sys.stderr)

        def is_fresh(vid):
            rec = known.get(vid)
            if not rec:
                return False
            extra, upd = rec
            try:
                if not json.loads(extra or "{}").get("mp4_url"):
                    return False
                age = (datetime.now() - datetime.fromisoformat(upd)).days
                return age < refresh_days
            except Exception:
                return False

        items, seen, fresh_skip, fails = [], set(), 0, 0
        for raw in urls[:60]:
            m = self.VID_RE.search(str(raw))
            if not m:
                continue
            vid = m.group(1)
            if vid in seen:
                continue
            seen.add(vid)

            if is_fresh(vid):
                fresh_skip += 1
                continue

            # 连续失败过多说明已被限流，提前停手留待下轮
            if fails >= 5:
                print(f"[{self.name}] 连续失败过多，疑似限流，本轮提前停止", file=sys.stderr)
                break

            try:
                info = None
                for attempt in range(3):
                    try:
                        info = self._parse_share(vid)
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        time.sleep(2.5 * (attempt + 1))
                fails = 0
                pub = (datetime.fromtimestamp(info["create_time"]).isoformat()
                       if info["create_time"] else datetime.now().isoformat())
                items.append({
                    "id": f"douyin_video:{vid}",
                    "source": self.name,
                    "title": info["title"][:300],
                    "url": f"https://www.douyin.com/video/{vid}",
                    "publish_time": pub,
                    "author": info["author"],
                    "extra": json.dumps({
                        "video_id": vid,
                        "mp4_url": info["mp4_url"],
                        "thumbnail": info["thumbnail"],
                        "upload_date": info["upload_date"],
                        "digg_count": info["digg"],
                        "comment_count": info["comment"],
                        "duration": info.get("duration", 0),
                        "no_watermark": True,
                    }, ensure_ascii=False),
                })
            except Exception as e:
                fails += 1
                print(f"[{self.name}] 解析失败 {vid}: {e}", file=sys.stderr)
            time.sleep(2.0)

        if fresh_skip:
            print(f"[{self.name}] 复用已入库直链 {fresh_skip} 条（{refresh_days} 天内）")
        return items


class DouyinSearchSource(Source):
    """抖音搜索 林园关键词（需登录cookie，默认尝试stealth）"""
    name = "douyin_search"
    min_interval = 3600

    def fetch(self, page):
        keyword = self.config.get("keyword", "林园")
        encoded = keyword
        url = f"https://www.douyin.com/search/{encoded}"

        # 尝试基础反检测
        try:
            page.set_viewport_size({"width": 1920, "height": 1080})
        except Exception:
            pass
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(5)

        # 判断是否被验证码拦截
        title = page.title()
        body = page.evaluate("() => document.body.innerText.slice(0, 200)")
        if "验证码" in title or "验证" in body or "captcha" in body.lower():
            raise RuntimeError("抖音触发验证码/风控，需要登录cookie或更换网络环境")

        # 尝试提取搜索结果
        results = page.evaluate("""() => {
            return Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/user/"]')).map(a => ({
                title: (a.innerText || a.getAttribute('title') || '').trim(),
                href: a.getAttribute('href')
            })).filter(x => x.href && x.href.includes('/video/') && x.title.length > 3);
        }""")

        items = []
        seen = set()
        for r in results[:20]:
            href = r["href"]
            if not href.startswith("http"):
                href = "https://www.douyin.com" + href
            if href in seen:
                continue
            seen.add(href)
            video_id = href.split('/video/')[-1].split('?')[0] if '/video/' in href else ''
            items.append({
                "id": f"douyin_search:{video_id or hash(href) & 0xFFFFFFFF}",
                "source": self.name,
                "title": r["title"][:200] or f"抖音视频 {video_id}",
                "url": href,
                "publish_time": datetime.now().isoformat(),
                "author": "抖音",
                "extra": json.dumps({"video_id": video_id, "keyword": keyword}, ensure_ascii=False),
            })
        return items


class HaokanVideoSource(Source):
    """好看视频（百度系）解析：无需登录/无验证码，直接拿 MP4 直链（含高清）"""
    name = "haokan_video"
    min_interval = 1800
    UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
          "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1")

    def _pick_best(self, urls):
        """优先 hd > cae_h264 > 其他；同时返回一个备选普清"""
        hd = [u for u in urls if "/hd/" in u]
        sd = [u for u in urls if "/hd/" not in u]
        best = (hd or sd or [""])[0]
        return best, (sd[0] if sd else "")

    def _extract(self, vid):
        import urllib.request
        url = f"https://haokan.baidu.com/v?pd=wisenatural&vid={vid}"
        req = urllib.request.Request(url, headers={"User-Agent": self.UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        title = ""
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        if m:
            title = m.group(1).strip()
            for suffix in (",好看视频", "_好看视频"):
                if title.endswith(suffix):
                    title = title[: -len(suffix)]
            title = title.split(",")[0].strip()

        raw = re.findall(r'https?:\\?/\\?/[^\s"\'<>]+?\.mp4[^\s"\'<>]*', html)
        mp4s = list(dict.fromkeys(u.replace("\\/", "/") for u in raw))
        if not mp4s:
            raise RuntimeError("未找到 mp4 直链")
        best, fallback = self._pick_best(mp4s)

        cover = ""
        mc = re.search(r'"poster"\s*:\s*"([^"]+)"', html) or \
             re.search(r'https?:[^\s"\'<>]+?\.(?:jpg|jpeg|png)[^\s"\'<>]*', html)
        if mc:
            cover = mc.group(1) if mc.lastindex else mc.group(0)
            cover = cover.replace("\\/", "/")

        # 发布时间（十位时间戳），用于与二创比对早晚
        published = ""
        pm = re.search(r'"publish_time"\s*:\s*"?(\d{10})"?', html)
        if pm:
            published = datetime.fromtimestamp(int(pm.group(1))).isoformat()

        # 时长（秒），用于同源判定
        dur = 0
        dm = re.search(r'"duration"\s*:\s*"?(\d{2,5})"?', html)
        if dm:
            dur = int(dm.group(1))

        return {"title": title, "mp4_url": best, "mp4_sd": fallback,
                "cover": cover, "page_url": url, "count": len(mp4s),
                "published_at": published, "duration": dur}

    def fetch(self, page):
        vids = list(self.config.get("vids", []))
        seeds_file = self.config.get("seeds_file")
        if seeds_file:
            seeds_path = Path(__file__).with_name(seeds_file)
            if seeds_path.exists():
                try:
                    seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
                    vids.extend(seeds.get("vids", []))
                except Exception as e:
                    print(f"[haokan_video] 种子文件读取失败: {e}", file=sys.stderr)
        if not vids:
            return []

        items, seen = [], set()
        for vid in vids[:40]:
            vid = str(vid).strip()
            if not vid or vid in seen:
                continue
            seen.add(vid)
            try:
                info = self._extract(vid)
                items.append({
                    "id": f"haokan_video:{vid}",
                    "source": self.name,
                    "title": (info["title"] or f"好看视频 {vid}")[:300],
                    "url": info["page_url"],
                    "publish_time": (info.get("published_at") or datetime.now().isoformat()),
                    "author": "好看视频",
                    "extra": json.dumps({
                        "vid": vid,
                        "mp4_url": info["mp4_url"],
                        "mp4_sd": info["mp4_sd"],
                        "cover": info["cover"],
                        "stream_count": info["count"],
                        "published_at": info.get("published_at", ""),
                        "duration": info.get("duration", 0),
                    }, ensure_ascii=False),
                })
            except Exception as e:
                print(f"[haokan_video] 解析失败 {vid}: {e}", file=sys.stderr)
        return items


class ShareholderMeetingSource(Source):
    """股东大会公告监控（巨潮资讯，证监会指定信披网站）。

    溯源发现：B站 UP主「园园滚雪球」的二创素材，大量来自林园参加
    自己持仓公司的股东大会现场发言。实测日期完全对应：
      片仔癉股东大会 2026-06-27 → 二创 BV1Sc7s68EuC 同日
      茅台股东大会   2026-06-12 → 二创 BV1c6EC66EWf 同日
    监控公告可在二创出现前拿到素材线索，是最上游的入口。
    纯 HTTP，无需登录态。
    """
    name = "shareholder_meeting"
    min_interval = 21600  # 6 小时，公告不频繁
    API = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
    STOCK_JSON = "http://www.cninfo.com.cn/new/data/szse_stock.json"
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

    # 林园公开持仓 / 常参与的公司
    DEFAULT_STOCKS = ["600519", "600436", "600329", "000538", "600085"]

    _org_cache = None

    # 从公告 PDF 中提取会议实地信息的规则
    RE_DATE = re.compile(r"召开的日期时间[：:\s]*([0-9]{4}\s*年\s*[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*日"
                         r"(?:\s*[0-9]{1,2}\s*点\s*[0-9]{1,2}\s*分)?)")
    RE_PLACE = re.compile(r"召开地点[：:\s]*([^\n]{4,60})")
    RE_LIVE = re.compile(r"(https?://[^\s）)。，,]{6,80})")
    RE_PLATFORM = re.compile(r"(全景路演|上证路演中心|价值在线|网络文字互动|视频直播|电话会议)")

    def _extract_pdf(self, adjunct_url):
        """下载公告 PDF 并提取会议时间/地点/直播平台。
        股东大会通知会提前 3 周左右发布，写明现场会议时间地点，
        这是比二创更早拿到一手素材的关键。"""
        import subprocess
        import tempfile
        import urllib.request
        url = f"http://static.cninfo.com.cn/{adjunct_url}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                raw = r.read()
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as f:
                f.write(raw)
                f.flush()
                out = subprocess.run(["pdftotext", f.name, "-"],
                                     capture_output=True, text=True, timeout=60)
            txt = out.stdout
        except Exception as e:
            return {"pdf_error": str(e)[:80]}

        info = {}
        m = self.RE_DATE.search(txt)
        if m:
            info["meeting_time"] = re.sub(r"\s+", "", m.group(1))
        m = self.RE_PLACE.search(txt)
        if m:
            info["meeting_place"] = re.sub(r"\s+", "", m.group(1))[:60]
        p = self.RE_PLATFORM.findall(txt)
        if p:
            info["platform"] = list(dict.fromkeys(p))
        links = [u for u in self.RE_LIVE.findall(txt)
                 if not u.startswith("http://www.sse") and "cninfo" not in u]
        if links:
            info["live_urls"] = list(dict.fromkeys(links))[:3]
        return info

    def _org_map(self):
        if ShareholderMeetingSource._org_cache is None:
            try:
                data = json.loads(http_get(self.STOCK_JSON, ua=self.UA))
                ShareholderMeetingSource._org_cache = {
                    s["code"]: (s["orgId"], s.get("zwjc", ""))
                    for s in data.get("stockList", [])
                }
            except Exception as e:
                print(f"[{self.name}] 股票列表获取失败: {e}", file=sys.stderr)
                ShareholderMeetingSource._org_cache = {}
        return ShareholderMeetingSource._org_cache

    def _query(self, code, org, keyword):
        import urllib.parse
        import urllib.request
        # 深市（000/002/300 开头）用 szse，沪市（600/601/603/688）用 sse
        column = "szse" if code[0] in ("0", "3") else "sse"
        body = urllib.parse.urlencode({
            "pageNum": 1, "pageSize": 15, "column": column, "tabName": "fulltext",
            "stock": f"{code},{org}", "searchkey": keyword, "seDate": "",
            "category": "", "isHLtitle": "true", "sortName": "", "sortType": "", "trade": "",
        }).encode()
        req = urllib.request.Request(
            self.API, data=body,
            headers={"User-Agent": self.UA,
                     "Referer": "http://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
                     "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                     "X-Requested-With": "XMLHttpRequest"})
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))

    def fetch(self, page):
        stocks = self.config.get("stocks") or self.DEFAULT_STOCKS
        keyword = self.config.get("keyword", "股东大会")
        omap = self._org_map()

        items = []
        for code in stocks:
            org, name = omap.get(code, ("", ""))
            if not org:
                continue
            try:
                data = self._query(code, org, keyword)
            except Exception as e:
                print(f"[{self.name}] {code} 查询失败: {e}", file=sys.stderr)
                continue

            for a in (data.get("announcements") or []):
                title = (a.get("announcementTitle") or "").replace("<em>", "").replace("</em>", "")
                aid = a.get("announcementId")
                adjunct = a.get("adjunctUrl", "")
                if not aid or not title:
                    continue
                # 只留真正属于该公司的公告
                sec = a.get("secName", "") or ""
                if name and sec and name not in sec and sec not in name:
                    continue
                ts = a.get("announcementTime")
                pub = (datetime.fromtimestamp(ts / 1000).isoformat()
                       if ts else datetime.now().isoformat())

                is_notice = ("通知" in title or "提示性" in title)
                extra = {
                    "stock_code": code,
                    "company": sec or name,
                    "announcement_id": aid,
                    "is_meeting_notice": is_notice,
                    "is_resolution": ("决议" in title),
                }
                # 只对「召开通知」类公告解析 PDF（含会议时间地点），
                # 避免浪费带宽去下载法律意见书/会议资料
                if is_notice and adjunct and self.config.get("parse_pdf", True):
                    extra.update(self._extract_pdf(adjunct))
                    # 计算距会议还有多久，供 Dashboard 做临近提醒
                    mt = extra.get("meeting_time", "")
                    md = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", mt or "")
                    if md:
                        try:
                            mdate = datetime(int(md.group(1)), int(md.group(2)), int(md.group(3)))
                            days = (mdate - datetime.now()).days
                            extra["meeting_date"] = mdate.strftime("%Y-%m-%d")
                            extra["days_until"] = days
                            extra["upcoming"] = 0 <= days <= 30
                        except Exception:
                            pass

                items.append({
                    "id": f"shareholder_meeting:{aid}",
                    "source": self.name,
                    "title": f"[{sec or name}] {title}"[:300],
                    "url": f"http://static.cninfo.com.cn/{adjunct}" if adjunct else
                           f"http://www.cninfo.com.cn/new/disclosure/detail?announcementId={aid}",
                    "publish_time": pub,
                    "author": sec or name,
                    "extra": json.dumps(extra, ensure_ascii=False),
                })
            time.sleep(0.4)
        return items


class NeteaseVideoSource(Source):
    """网易视频（money.163.com 关键词标签页）。

    优势：自带发现能力——标签页会持续聚合新视频，不需要维护种子池。
    纯 HTTP，无登录、无验证码。

    MP4 直链推导：播放页 → m3u8 地址 → 读 m3u8 内容拿 ts 路径
    → 拼接为 {host}{videolib路径}/{vid}-mobile.mp4
    """
    name = "netease_video"
    min_interval = 3600
    UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    # 林园的网易关键词标签页
    DEFAULT_TAGS = ["https://money.163.com/keywords/6/9/679756ed/{page}.html"]
    NOISE = re.compile(r"华林园|虎林园|林园酒店|林园景区|园林|动物园")

    def _page(self, url):
        return http_get(url, referer="https://money.163.com/", ua=self.UA, timeout=30)

    def _resolve_mp4(self, m3u8_url):
        """从 m3u8 推导出 MP4 直链。"""
        try:
            content = http_get(m3u8_url, ua=self.UA, timeout=25)
        except Exception:
            return ""
        seg = re.search(r"(/videolib\d+/[^\s]+?)/([A-Za-z0-9]+)-mobile-\d+\.ts", content)
        if not seg:
            return ""
        host = re.match(r"(https?://[^/]+)", m3u8_url)
        if not host:
            return ""
        return f"{host.group(1)}{seg.group(1)}/{seg.group(2)}-mobile.mp4"

    def _parse_video(self, vcode):
        html = self._page(f"https://www.163.com/v/video/{vcode}.html")
        ti = re.search(r"<title>(.*?)</title>", html, re.S)
        title = re.sub(r"[_|].*$", "", ti.group(1).strip()) if ti else ""
        m3 = re.findall(r'https?://[^\s"\'<>\\]+?\.m3u8[^\s"\'<>\\]*', html)
        if not m3:
            raise RuntimeError("未找到 m3u8")
        m3u8 = m3[0]
        cover = ""
        mc = re.search(r'"(https?://[^"]+?\.(?:jpg|jpeg|png))"', html)
        if mc:
            cover = mc.group(1)

        # 发布时间（页面上的 YYYY-MM-DD HH:MM）
        published = ""
        pm = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", html)
        if pm:
            published = pm.group(1).replace(" ", "T")

        # 时长（秒），用于与二创做时长匹配
        dur = 0
        dm = re.search(r'"duration"\s*:\s*"?(\d+)', html)
        if dm:
            dur = int(dm.group(1))

        return {"title": title, "m3u8": m3u8,
                "mp4": self._resolve_mp4(m3u8), "cover": cover,
                "published_at": published, "duration": dur}

    def fetch(self, page):
        keyword = self.config.get("keyword", "林园")
        tags = self.config.get("tag_urls") or self.DEFAULT_TAGS
        pages = int(self.config.get("pages", 2))

        # 1) 从标签页发现视频
        found = {}
        # 1a) 种子文件（标签页以外的，由 web_search 发现后补充）
        seeds_file = self.config.get("seeds_file")
        if seeds_file:
            sp = Path(__file__).with_name(seeds_file)
            if sp.exists():
                try:
                    for v in json.loads(sp.read_text(encoding="utf-8")).get("vcodes", []):
                        found.setdefault(str(v).strip(), "")
                except Exception as e:
                    print(f"[{self.name}] 种子文件读取失败: {e}", file=sys.stderr)
        for tpl in tags:
            for p in range(1, pages + 1):
                try:
                    html = self._page(tpl.format(page=p))
                except Exception as e:
                    print(f"[{self.name}] 标签页 p{p} 失败: {e}", file=sys.stderr)
                    continue
                for m in re.finditer(
                        r'href="(https?://[^"]*163\.com/v/video/([A-Za-z0-9]+)\.html)"[^>]*>([^<]{4,80})<', html):
                    url, vcode, text = m.group(1), m.group(2), m.group(3).strip()
                    if keyword not in text:
                        continue
                    if self.NOISE.search(text):
                        continue
                    found.setdefault(vcode, text)
                time.sleep(0.3)

        if not found:
            # 标签页 ID 是硬编码的（money.163.com/keywords/6/9/<id>），
            # 网易改版后会静默返回空页而非报错，这里显式告警
            if tags and not self.config.get("seeds_file"):
                raise RuntimeError(
                    "网易标签页零产出，标签 ID 可能已变更（当前: "
                    + ", ".join(t.split("/")[-2] for t in tags) + "）")
            return []

        # 2) 逐个解析拿 MP4
        items = []
        for vcode, text in list(found.items())[:25]:
            try:
                info = self._parse_video(vcode)
                items.append({
                    "id": f"netease_video:{vcode}",
                    "source": self.name,
                    "title": (info["title"] or text)[:300],
                    "url": f"https://www.163.com/v/video/{vcode}.html",
                    "publish_time": (info.get("published_at") or datetime.now().isoformat()),
                    "author": "网易视频",
                    "extra": json.dumps({
                        "vcode": vcode,
                        "mp4_url": info["mp4"],
                        "m3u8_url": info["m3u8"],
                        "cover": info["cover"],
                        "published_at": info.get("published_at", ""),
                        "duration": info.get("duration", 0),
                    }, ensure_ascii=False),
                })
            except Exception as e:
                msg = str(e)
                # 404 说明视频已下线，标记出来便于清理种子
                tag = "（已下线）" if ("404" in msg or "未找到 m3u8" in msg) else ""
                print(f"[{self.name}] 解析失败 {vcode}: {msg[:50]}{tag}", file=sys.stderr)
            time.sleep(0.4)
        return items


SOURCES = {
    "bilibili_api": BilibiliApiSource,
    "bilibili_search": BilibiliSearchSource,
    "bilibili_space": BilibiliSpaceSource,
    "xueqiu_search": XueqiuSearchSource,
    "weibo_search": WeiboSearchSource,
    "tencent_live": TencentLiveSource,
    "douyin_video": DouyinVideoSource,
    "douyin_search": DouyinSearchSource,
    "haokan_video": HaokanVideoSource,
    "shareholder_meeting": ShareholderMeetingSource,
    "netease_video": NeteaseVideoSource,
}


def upsert_items(items):
    conn = sqlite3.connect(DB_PATH)
    now = datetime.now().isoformat()
    new_items = []

    for item in items:
        cur = conn.execute("SELECT 1 FROM items WHERE id = ?", (item["id"],))
        exists = cur.fetchone() is not None

        if exists:
            conn.execute("""
                UPDATE items SET
                    title = ?, url = ?, publish_time = ?, author = ?, extra = ?, updated_at = ?
                WHERE id = ?
            """, (
                item["title"], item["url"], item["publish_time"], item["author"],
                item["extra"], now, item["id"]
            ))
        else:
            conn.execute("""
                INSERT INTO items
                (id, source, title, url, publish_time, author, extra, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["id"], item["source"], item["title"], item["url"],
                item["publish_time"], item["author"], item["extra"], now, now
            ))
            new_items.append(item)

    conn.commit()
    conn.close()
    return new_items


def run_source(source_cls, config, state, page):
    src = source_cls(config, state)
    if not src.can_fetch():
        print(f"[{src.name}] 未到最小间隔，跳过")
        return []

    for attempt in range(3):
        try:
            print(f"[{src.name}] 开始抓取...")
            items = src.fetch(page)
            src.mark_fetched()
            print(f"[{src.name}] 抓取成功: {len(items)} 条")
            return items
        except Exception as e:
            print(f"[{src.name}] 抓取失败 (attempt {attempt+1}/3): {e}", file=sys.stderr)
            if attempt < 2:
                src.backoff(attempt)
    return []


def update_item_extra(item_id, extra):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE items SET extra = ?, updated_at = ? WHERE id = ?",
                 (json.dumps(extra, ensure_ascii=False), datetime.now().isoformat(), item_id))
    conn.commit()
    conn.close()


def export_dashboard_data():
    """将 monitor_v2.db 导出为 dashboard/data.json"""
    out_path = Path(__file__).parent / "dashboard" / "data.json"
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM items ORDER BY publish_time DESC, created_at DESC"
    ).fetchall()
    conn.close()

    items = []
    for row in rows:
        extra = {}
        try:
            extra = json.loads(row["extra"] or "{}")
        except Exception:
            pass
        if row["author"] in BLACKLIST_AUTHORS:
            continue  # 排除黑名单账号（存量数据在导出时一并滤掉）
        item = {
            "id": row["id"],
            "source": row["source"],
            "title": row["title"],
            "url": row["url"],
            "video_url": extra.get("video_url", ""),
            "publish_time": row["publish_time"],
            "author": row["author"],
            "extra": extra,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        items.append(item)

    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[dashboard] 已导出 {len(items)} 条到 {out_path}")


def main():
    init_db()
    print(f"监控开始: {datetime.now().isoformat()}")

    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        sync_playwright = None

    state = load_state()

    config_path = Path(__file__).with_name("monitor_v2_config.json")
    if config_path.exists():
        configs = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        configs = [
            {"type": "bilibili_api", "uid": "1700344493", "name": "园园滚雪球"},
            {"type": "bilibili_space", "uid": "1700344493", "name": "园园滚雪球"},
            {"type": "xueqiu_search", "keyword": "林园", "symbols": ["SH600519", "SH600436", "SH600329"]},
            {"type": "weibo_search", "keyword": "林园"},
            {"type": "tencent_live", "keyword": "林园"},
        ]

    all_new = []
    has_browser = cdp_available()
    if has_browser:
        print("[模式] 检测到 CDP 浏览器 → 全量模式")
    else:
        skipped = [c["type"] for c in configs if c["type"] in BROWSER_REQUIRED]
        print(f"[模式] 未检测到浏览器 → 降级模式（跳过: {', '.join(skipped) or '无'}）")
        configs = [c for c in configs if c["type"] not in BROWSER_REQUIRED]

    def run_all(page):
        for cfg in configs:
            src_type = cfg["type"]
            src_cls = SOURCES.get(src_type)
            if not src_cls:
                print(f"未知 source type: {src_type}", file=sys.stderr)
                continue
            items = run_source(src_cls, cfg, state, page)
            items = [it for it in items if it.get("author", "") not in BLACKLIST_AUTHORS]
            new_items = upsert_items(items)
            print(f"[{src_type}] 新增: {len(new_items)} 条")
            for item in new_items[:5]:
                print(f"  - {item['publish_time']} | {item['title'][:50]} | {item['url'][:60]}")
            all_new.extend(new_items)

    if has_browser:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = context.new_page()
            try:
                run_all(page)
            finally:
                page.close()
    else:
        run_all(None)

    save_state(state)
    export_dashboard_data()
    print(f"\n总计新增: {len(all_new)} 条")
    return all_new


if __name__ == "__main__":
    main()
