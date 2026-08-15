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
MAX_PER_DAY = 2                          # 每天最多出几条（新号防限流）
DELAY_LADDER = [5, 8, 11]                # B站定时发布阶梯（必须 >4h）
SAME_VIDEO_COOLDOWN = 48 * 3600          # 同源冷却：同一场会切片不能连发
TID, COPYRIGHT = 207, 2                  # 财经商业 / 转载（转载必须带 source）

# 搜索噪音：「虎林园」「东北虎林园」是老虎公园，不是林园本人。标题命中即排除。
NOISE = re.compile(r"虎林园|东北虎|横道河子|二埋汰")

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


def save_state(st):
    import base64
    content = base64.b64encode(json.dumps(
        st, ensure_ascii=False, indent=1).encode()).decode()
    try:
        cur = gh("GET", f"/contents/{STATE_KEY}?ref=main")
        gh("PUT", f"/contents/{STATE_KEY}", {
            "message": "chore(fc): 更新流水线状态",
            "content": content, "sha": cur["sha"]})
    except Exception:
        gh("PUT", f"/contents/{STATE_KEY}", {
            "message": "chore(fc): 初始化流水线状态", "content": content})


# ---------- 选片 ----------

def video_id_of(page_url, video_url):
    m = re.search(r"(BV\w+)", page_url or "")
    if m:
        return m.group(1)
    return (video_url or "").split("?")[0] or page_url


def mp4_duration(path):
    """纯 Python 读 mvhd atom，FC 没有 ffmpeg。"""
    data = Path(path).open("rb").read(200_000)
    i = data.find(b"mvhd")
    if i < 0:
        return 0
    ver = data[i + 4]
    if ver == 1:
        ts, dur = int.from_bytes(data[i+20:i+24], "big"), int.from_bytes(data[i+24:i+32], "big")
    else:
        ts, dur = int.from_bytes(data[i+12:i+16], "big"), int.from_bytes(data[i+16:i+20], "big")
    return dur / ts if ts else 0


def pick(items, st, n):
    now = time.time()
    done = {e["key"] for e in st["dispatched"]} | {e["key"] for e in st["rejected"]}
    cooling = {e["video_id"] for e in st["dispatched"]
               if now - e.get("ts", 0) < SAME_VIDEO_COOLDOWN}
    cooling |= {e["video_id"] for e in st["rejected"]}
    cands = []
    for it in items:
        url, page = it.get("video_url") or "", it.get("url") or ""
        if not url and "bilibili.com/video/" not in page:
            continue
        key = it.get("id") or page or url
        vid = video_id_of(page, url)
        if not key or key in done or vid in cooling:
            continue
        if NOISE.search(it.get("title") or ""):
            continue                                     # 老虎公园不是林园
        cands.append({"key": key, "video_id": vid,
                      "title": (it.get("title") or "")[:60],
                      "video_url": url, "page_url": page,
                      "publish_time": it.get("publish_time") or ""})
    cands.sort(key=lambda c: c["publish_time"], reverse=True)
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


def download(cand, dest):
    if cand["video_url"]:                                # 微博等直链
        subprocess.run(["curl", "-sfL", "--max-time", "240", "-o",
                        str(dest), cand["video_url"]], check=True)
    else:                                                # B站：带指纹的会话走全程
        op = bili_opener()
        bvid = cand["video_id"]
        v = json.loads(op.open(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            timeout=30).read())
        cid = v["data"]["cid"]
        p = json.loads(op.open(
            f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
            "&qn=32&fnval=1&platform=html5&high_quality=1", timeout=30).read())
        durl = (p.get("data") or {}).get("durl") or []
        if not durl:
            raise RuntimeError("无可用流")
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
    cands = pick(items, st, MAX_PER_DAY)
    log.info(f"候选 {len(cands)} 条")

    rel = staging_release_id()
    tmp = Path(tempfile.mkdtemp())
    for i, c in enumerate(cands):
        import hashlib
        c["slug"] = "ly-" + time.strftime("%m%d") + "-" + \
                    hashlib.md5(c["key"].encode()).hexdigest()[:6]
        delay = DELAY_LADDER[i] if i < len(DELAY_LADDER) else DELAY_LADDER[-1]
        log.info(f"[{i+1}/{len(cands)}] {c['title'][:40]}")
        try:
            dest = tmp / f"{c['slug']}.mp4"
            dur = download(c, dest)
            asset_url = upload_asset(rel, dest)
            gh("POST", f"/actions/workflows/{WF_PRODUCE}/dispatches", {
                "ref": "main",
                "inputs": {"source": asset_url, "slug": c["slug"],
                           "speaker": "林园", "occasion": c["title"][:30],
                           "delay_hours": "0", "auto_publish": "false"}})
            st["dispatched"].append({"key": c["key"], "video_id": c["video_id"],
                                     "slug": c["slug"], "ts": int(time.time()),
                                     "source_url": c["page_url"] or c["video_url"],
                                     "title": c["title"], "delay_hours": delay})
            save_state(st)
            log.info(f"    ✓ 已调度 {c['slug']}（{dur:.0f}s，定时 +{delay}h）")
        except Exception as e:
            st["rejected"].append({"key": c["key"], "video_id": c["video_id"],
                                   "ts": int(time.time())})
            save_state(st)
            log.warning(f"    ✗ {e}")
    return {"dispatched": len([c for c in cands])}


# ---------- Handler 2：投稿 ----------

def publish_handler(event=None, context=None):
    st = load_state()
    pending = [e for e in st["dispatched"]
               if e.get("slug") and e["slug"] not in st["published"]
               and not e.get("failed")]
    if not pending:
        log.info("无待投稿件")
        return {"published": 0}

    runs = gh("GET", f"/actions/workflows/{WF_PRODUCE}/runs"
                     "?status=success&per_page=10").get("workflow_runs", [])
    arts = {}
    for run in runs:
        for a in gh("GET", f"/actions/runs/{run['id']}/artifacts").get("artifacts", []):
            if a["name"].startswith("deliver-") and not a.get("expired"):
                arts[a["name"][8:]] = a["archive_download_url"]

    tmp = Path(tempfile.mkdtemp())
    with open(tmp / "cookies.json", "w") as f:
        f.write(COOKIES_JSON)
    os.chmod(tmp / "cookies.json", 0o600)

    done = 0
    for e in pending:
        slug = e["slug"]
        if slug not in arts:
            log.info(f"{slug} 成片未就绪，下轮再看")
            continue
        zf = tmp / f"{slug}.zip"
        subprocess.run(["curl", "-sfL", "--max-time", "300", "-C", "-",
                        "-H", f"Authorization: Bearer {TOKEN}",
                        "-o", str(zf), arts[slug]], check=True)
        subprocess.run(["unzip", "-oq", str(zf), "-d", str(tmp / slug)], check=True)
        video = tmp / slug / "final.mp4"
        if not video.exists():
            continue
        # 优先用 artifact 里 meta.json 的 LLM 文案 + 封面
        title, desc, tags, cover = e.get("title") or slug, "", "林园,价值投资", None
        meta_f = tmp / slug / "meta.json"
        if meta_f.exists():
            try:
                mj = json.loads(meta_f.read_text(encoding="utf-8"))
                title = mj.get("title") or title
                desc = mj.get("desc", "")
                tags = ",".join(mj.get("tags", ["林园", "价值投资"]))
                if mj.get("cover") and (tmp / slug / mj["cover"]).exists():
                    cover = tmp / slug / mj["cover"]
            except Exception:
                pass
        if "｜" not in title:
            title = f"{title[:40]}｜林园"
        # FC 没有 PATH 里的 biliup，用 python -m 调包里带的（biliup/stream_gears 已打进代码包）
        cmd = [sys.executable, "-m", "biliup",
               "-u", str(tmp / "cookies.json"), "upload", str(video),
               "--title", title, "--tid", str(TID), "--copyright", str(COPYRIGHT),
               "--source", e.get("source_url") or "https://www.bilibili.com",
               "--desc", desc, "--tag", tags, "--limit", "1"]
        if cover:
            cmd += ["--cover", str(cover)]
        delay = int(e.get("delay_hours") or 0)
        if delay > 0:
            cmd += ["--dtime", str(int(time.time()) + delay * 3600)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        out = (r.stdout or "") + (r.stderr or "")
        m = re.search(r'BV\w{10}', out)
        if r.returncode == 0 and m:
            st["published"][slug] = {"bvid": m.group(0), "ts": int(time.time()),
                                     "title": title}
            save_state(st)
            log.info(f"✅ 已投 https://www.bilibili.com/video/{m.group(0)}")
            done += 1
        else:
            tail = [ln for ln in out.splitlines() if "code" in ln or "Error" in ln][-2:]
            log.error(f"✗ {slug} 投稿失败：{'; '.join(tail)[:200]}")
    return {"published": done}
