#!/usr/bin/env python3
"""CI 侧 B站取源：多策略降级。

实测的 WAF 封控表（2026-08-15）：
    view API    GitHub ❌  FC ❌  沙箱 ✅
    search API  GitHub ✅  FC ❌  沙箱 ✅
    CDN 上传    GitHub ❌  FC ✅  沙箱 ✅
view 被封不代表全死：pagelist 拿 cid、embed 页直接带 __playinfo__，
这些端点的风控级别不同，逐条路试。

用法：
    python3 ci_fetch_bilibili.py --url https://www.bilibili.com/video/BVxx --out video.mp4
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import parse_qs, urlparse
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _configured_cookie_entries():
    """读取 biliup cookies.json；只返回条目，任何异常都不打印凭据内容。"""
    raw = (os.environ.get("BILIBILI_COOKIES") or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw.lstrip("\ufeff"))
        entries = ((data.get("cookie_info") or {}).get("cookies")
                   or data.get("cookies") or [])
        if isinstance(entries, list):
            return [x for x in entries
                    if isinstance(x, dict) and x.get("name") and x.get("value")]
        if isinstance(data, dict) and data.get("SESSDATA"):
            return [{"name": key, "value": value}
                    for key, value in data.items() if isinstance(value, str)]
    except (AttributeError, TypeError, ValueError):
        pass
    print("[warn] BILIBILI_COOKIES 格式无效，改用匿名高清取流", file=sys.stderr)
    return []


def opener():
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    import http.cookiejar as cj
    for item in _configured_cookie_entries():
        jar.set_cookie(cj.Cookie(
            0, item["name"], item["value"], None, False,
            item.get("domain") or ".bilibili.com", True, False,
            item.get("path") or "/", True, False, None, False,
            None, None, {}))
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Accept-Language", "zh-CN,zh;q=0.9"),
                     ("Referer", "https://www.bilibili.com/")]
    try:
        op.open("https://www.bilibili.com/", timeout=20).read()
        spi = json.loads(op.open(
            "https://api.bilibili.com/x/frontend/finger/spi", timeout=20).read().decode())
        for n, val in (("buvid3", spi["data"]["b_3"]), ("buvid4", spi["data"]["b_4"])):
            jar.set_cookie(cj.Cookie(0, n, val, None, False, ".bilibili.com", True,
                                     False, "/", True, False, None, False, None, None, {}))
    except Exception as e:
        print(f"[warn] buvid 获取失败: {e}", file=sys.stderr)
    return op


def requested_page(url):
    value = parse_qs(urlparse(url).query).get('p', ['1'])
    if len(value) != 1 or not value[0].isdigit() or int(value[0]) < 1:
        raise ValueError('Invalid Bilibili page number')
    return int(value[0])


def page_cid(pages, page):
    matches = [p for p in pages if int(p.get('page', 0)) == page]
    if len(matches) != 1 or not matches[0].get('cid'):
        raise ValueError(f'Bilibili page {page} is missing or ambiguous; never substitute page 1')
    return matches[0]['cid']


def via_view(op, bvid, page=1):
    """策略 A：view API 拿 cid。"""
    v = json.loads(op.open(
        f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}", timeout=30).read())
    if v.get("code") != 0:
        raise RuntimeError(f"view code={v.get('code')}")
    data = v['data']
    if data.get('pages'):
        return page_cid(data['pages'], page)
    if page != 1:
        raise ValueError(f'No page {page} metadata')
    return data['cid']


def via_pagelist(op, bvid, page=1):
    """策略 B：pagelist 拿 cid（风控级别和 view 不同）。"""
    r = json.loads(op.open(
        f"https://api.bilibili.com/x/player/pagelist?bvid={bvid}", timeout=30).read())
    if r.get("code") != 0 or not r.get("data"):
        raise RuntimeError(f"pagelist code={r.get('code')}")
    return page_cid(r['data'], page)


def _urls(stream):
    if not stream:
        return []
    values = [stream.get("baseUrl") or stream.get("base_url") or stream.get("url")]
    backups = stream.get("backupUrl") or stream.get("backup_url") or []
    values.extend([backups] if isinstance(backups, str) else backups)
    return list(dict.fromkeys(x for x in values if x))


def select_streams(info):
    """优先选择不超过 1080P 的最高画质 DASH，并优先 H.264 兼容编码。"""
    data = (info or {}).get("data") or (info or {}).get("result") or {}
    dash = data.get("dash") or {}
    videos = [x for x in (dash.get("video") or [])
              if int(x.get("height") or 0) <= 1080]
    if videos:
        top_height = max(int(x.get("height") or 0) for x in videos)
        top = [x for x in videos if int(x.get("height") or 0) == top_height]
        avc = [x for x in top if int(x.get("codecid") or 0) == 7]
        video = max(avc or top, key=lambda x: int(x.get("bandwidth") or 0))
        audios = dash.get("audio") or []
        audio = max(audios, key=lambda x: int(x.get("bandwidth") or 0)) if audios else None
        return {"video": _urls(video), "audio": _urls(audio),
                "height": top_height, "quality": data.get("quality")}
    durl = data.get("durl") or []
    if durl:
        return {"video": _urls(durl[0]), "audio": [],
                "height": 0, "quality": data.get("quality")}
    raise RuntimeError("无可用 DASH/durl 流")


def via_embed(op, bvid, page=1):
    """策略 C：embed 播放页的 __playinfo__ 直接带流地址，连 playurl 都省了。"""
    html = op.open(
        f"https://player.bilibili.com/player.html?bvid={bvid}&page={page}&autoplay=0",
        timeout=30).read().decode("utf-8", "ignore")
    m = re.search(r"__playinfo__\s*=\s*(\{.*?\})\s*</script>", html, re.S) \
        or re.search(r"__playinfo__\s*=\s*(\{.*)", html)
    if not m:
        raise RuntimeError("embed 页没有 __playinfo__")
    return select_streams(json.loads(m.group(1)))


def playurl(op, bvid, cid):
    p = json.loads(op.open(
        f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
        "&qn=80&fnval=4048&fourk=0&high_quality=1", timeout=30).read())
    if p.get("code") != 0:
        raise RuntimeError(f"playurl code={p.get('code')}")
    return select_streams(p)


class FetchBudgetExceeded(TimeoutError):
    pass


def remaining_seconds(deadline):
    remaining=deadline-time.monotonic()
    if remaining<=0:
        raise FetchBudgetExceeded('取源达到总时间预算，保留为可重试下载故障')
    return remaining


def download_one(op, urls, referer, out, attempts=3, deadline=None):
    """镜像轮换 + 断点续传 + Content-Length 校验，避免长母片反复从零下载。"""
    out = Path(out)
    last = None
    tmp = out.with_suffix(out.suffix + ".part")
    manifest = out.with_suffix(out.suffix + '.download.json')
    identity = dict(source=referer, paths=sorted({urlparse(u).path for u in urls}))
    saved = {}
    if manifest.exists():
        try:
            saved = json.loads(manifest.read_text())
        except (ValueError, OSError):
            pass
    if saved.get('identity') != identity:
        # Refreshed CDN signatures are fine; a different source/representation
        # is not. Never append a new encoding to bytes cached by an earlier run.
        tmp.unlink(missing_ok=True)
        out.unlink(missing_ok=True)
        saved = dict(identity=identity)
        manifest.write_text(json.dumps(saved))
    if out.exists() and saved.get('sha256'):
        with out.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if digest == saved['sha256'] and out.stat().st_size == saved.get('total'):
            print(f'[取源复用] 已完成轨道 {out.name}', flush=True)
            return
        out.unlink()
    deadline=deadline if deadline is not None else time.monotonic()+1200
    stalled_rounds=0
    while stalled_rounds<attempts:
        remaining_seconds(deadline)
        before=tmp.stat().st_size if tmp.exists() else 0
        for url in urls:
            remaining_seconds(deadline)
            try:
                existing = tmp.stat().st_size if tmp.exists() else 0
                headers = {"User-Agent": UA, "Referer": referer}
                if existing:
                    headers["Range"] = f"bytes={existing}-"
                req = urllib.request.Request(
                    url, headers=headers)
                # A stalled mirror should yield to its backups. This is socket
                # inactivity, not a 60-second cap on a progressing large video.
                with op.open(req, timeout=min(60,remaining_seconds(deadline))) as response:
                    status = getattr(response, 'status', None) or response.getcode()
                    resumed = bool(existing and status == 206)
                    if not resumed:
                        existing = 0
                    content_range = response.headers.get("Content-Range") or ""
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", content_range)
                    if resumed and (not match or int(match.group(1)) != existing):
                        raise RuntimeError("CDN断点区间不连续")
                    expected = (int(match.group(3)) if match and match.group(3) != '*'
                                else existing + int(response.headers.get("Content-Length") or 0))
                    etag = response.headers.get('ETag')
                    if resumed and ((saved.get('total') and expected != saved['total'])
                                    or (etag and saved.get('etag') and etag != saved['etag'])):
                        tmp.unlink(missing_ok=True)
                        saved = dict(identity=identity)
                        manifest.write_text(json.dumps(saved))
                        raise RuntimeError('CDN内容版本改变，丢弃旧断点后重新下载')
                    saved.update(total=expected, etag=etag)
                    manifest.write_text(json.dumps(saved))
                    with tmp.open("ab" if resumed else "wb") as handle:
                        last_logged=existing
                        while True:
                            remaining_seconds(deadline)
                            chunk = response.read(1 << 20)
                            if not chunk:
                                break
                            handle.write(chunk)
                            current=handle.tell()
                            if current-last_logged>=16*(1<<20):
                                print(f'[下载进度] {current}/{expected or "?"} bytes',flush=True)
                                last_logged=current
                actual = tmp.stat().st_size
                if actual < 10240 or (expected and actual != expected):
                    raise RuntimeError(f"下载不完整：{actual}/{expected or '?'} bytes")
                tmp.replace(out)
                with out.open('rb') as handle:
                    saved['sha256'] = hashlib.file_digest(handle, 'sha256').hexdigest()
                saved['total'] = actual
                manifest.write_text(json.dumps(saved))
                return
            except FetchBudgetExceeded:
                raise
            except Exception as exc:
                last = exc
                # Tiny/error responses are not reusable. A substantial partial
                # file is kept and resumed on the next mirror or attempt.
                if tmp.exists() and tmp.stat().st_size < 10240:
                    tmp.unlink(missing_ok=True)
        after=tmp.stat().st_size if tmp.exists() else 0
        if after>before:
            # A partial response with forward progress is resumable work, not
            # a spent retry. #626 stopped at 111/258MB after just three rounds.
            stalled_rounds=0
            print(f'[断点续传] 已保留 {after} bytes，继续下载',flush=True)
        else:
            stalled_rounds+=1
            if stalled_rounds<attempts:
                time.sleep(min(2**(stalled_rounds-1),remaining_seconds(deadline)))
    raise RuntimeError(f"所有 CDN 镜像下载失败：{last}")


def validate_media(path, max_track_drift=2.0, max_tail_gap=5.0):
    """合流后硬校验：必须同时有可识别的音视频轨，且时长不明显漂移。

    ffmpeg 返回 0 只能说封装完成，不代表 CDN 给的两条 DASH 轨
    完整且可解码。再对头尾各 2 秒解码，能抓到截断的 m4s/NAL。
    """
    path = Path(path)
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,duration", "-of", "json", str(path),
    ], capture_output=True, text=True, timeout=60)
    if probe.returncode:
        raise RuntimeError(f"合流文件 ffprobe 失败：{probe.stderr.strip()[:160]}")
    try:
        data = json.loads(probe.stdout)
        streams = data.get("streams") or []
        video = [x for x in streams if x.get("codec_type") == "video"]
        audio = [x for x in streams if x.get("codec_type") == "audio"]
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("合流文件媒体信息无效") from exc
    if not video or not audio or duration <= 1:
        raise RuntimeError(
            f"合流文件轨道不完整：video={len(video)} audio={len(audio)} "
            f"duration={duration:.2f}s")
    durations = []
    for stream in (video[0], audio[0]):
        try:
            value = float(stream.get("duration") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            durations.append(value)
    if len(durations) == 2 and abs(durations[0] - durations[1]) > max_track_drift:
        raise RuntimeError(
            f"合流后音视频时长漂移 {abs(durations[0] - durations[1]):.2f}s")

    # 不能只相信 format/stream duration：截断 MP4 的 moov 元数据可能仍写着完整
    # 时长，ffmpeg 对“尾部根本没有音频包”也可能返回 0。直接检查两条轨最后一个
    # 真实 packet 的 PTS，确保数据确实延伸到容器结尾附近。
    tail_start = max(0.0, duration - max_tail_gap - 2.0)
    for selector, label in (("v:0", "视频"), ("a:0", "音频")):
        packet_probe = subprocess.run([
            "ffprobe", "-v", "error", "-read_intervals", f"{tail_start}%",
            "-select_streams", selector, "-show_packets", "-show_entries",
            "packet=pts_time", "-of", "csv=p=0", str(path),
        ], capture_output=True, text=True, timeout=120)
        pts = []
        for line in packet_probe.stdout.splitlines():
            try:
                pts.append(float(line.strip().split(",")[0]))
            except (TypeError, ValueError):
                continue
        if packet_probe.returncode or not pts:
            raise RuntimeError(f"合流文件尾部缺少{label}数据包")
        gap = duration - max(pts)
        if gap > max_tail_gap:
            raise RuntimeError(f"合流文件{label}轨提前 {gap:.2f}s 结束")

    checks = [
        ["ffmpeg", "-v", "error", "-xerror", "-t", "2", "-i", str(path),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
        ["ffmpeg", "-v", "error", "-xerror", "-sseof", "-2", "-i", str(path),
         "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
    ]
    for cmd in checks:
        decoded = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if decoded.returncode:
            raise RuntimeError(f"合流文件头尾解码失败：{decoded.stderr.strip()[:160]}")
    return {"duration": duration, "video_streams": len(video),
            "audio_streams": len(audio)}


def download(op, streams, referer, out, deadline=None):
    out = Path(out)
    if not streams.get("audio"):
        download_one(op, streams["video"], referer, out,deadline=deadline)
        validate_media(out)
        return
    video = out.with_suffix(".video.m4s")
    audio = out.with_suffix(".audio.m4s")
    completed = False
    try:
        download_one(op, streams["video"], referer, video,deadline=deadline)
        download_one(op, streams["audio"], referer, audio,deadline=deadline)
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
            "-i", str(audio), "-map", "0:v:0", "-map", "1:a:0",
            "-c", "copy", "-shortest", "-movflags", "+faststart", str(out),
        ], check=True)
        validate_media(out)
        completed = True
    finally:
        # A later audio/network failure must not discard an already downloaded
        # video track. Actions persists these verified, source-bound checkpoints.
        if completed:
            for track in (video, audio):
                track.unlink(missing_ok=True)
                track.with_suffix(track.suffix + '.download.json').unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--out")
    ap.add_argument("--validate-only", metavar="MEDIA")
    ap.add_argument('--budget-seconds',type=int,default=1200)
    ap.add_argument('--failure-report',type=Path)
    args = ap.parse_args()

    if args.validate_only:
        report = validate_media(args.validate_only)
        print(f"✓ 音视频轨与头尾解码正常，时长 {report['duration']:.2f}s")
        return
    if not args.url or not args.out:
        ap.error("下载模式必须同时提供 --url 和 --out")
    if args.budget_seconds<=0:
        ap.error('取源预算必须为正数')
    deadline=time.monotonic()+args.budget_seconds

    m = re.search(r"(BV\w+)", args.url)
    if not m:
        sys.exit("URL 里没有 BV 号")
    bvid = m.group(1)
    page = requested_page(args.url)

    strategies = [
        ("view→playurl", lambda op: playurl(op, bvid, via_view(op, bvid, page))),
        ("pagelist→playurl", lambda op: playurl(op, bvid, via_pagelist(op, bvid, page))),
    ]
    # Embedded pages do not prove which cid their playinfo belongs to. Keep
    # the legacy fallback only for page 1, never silently download the wrong P.
    if page == 1:
        strategies.append(("embed __playinfo__", lambda op: via_embed(op, bvid)))
    last = None
    for name, fn in strategies:
        try:
            remaining_seconds(deadline)
            op = opener()
            print(f"→ 策略 {name}",flush=True)
            streams = fn(op)
            height = streams.get("height") or "未知"
            print(f"  拿到最高可用流（{height}P），下载中...",flush=True)
            download(op, streams, args.url, args.out,deadline=deadline)
            print(f"✓ {name} 成功")
            return
        except FetchBudgetExceeded as e:
            last=e
            break
        except Exception as e:
            last = e
            print(f"  ✗ {e}", file=sys.stderr)
    if args.failure_report:
        args.failure_report.parent.mkdir(parents=True,exist_ok=True)
        args.failure_report.write_text(json.dumps(dict(passed=False,retryable=True,
            failure_stage='source-fetch',reason=f'取源未完成：{type(last).__name__}: {last}',
            source_url=args.url,budget_seconds=args.budget_seconds),ensure_ascii=False,indent=2))
    sys.exit(f"所有策略失败，最后错误：{last}")


if __name__ == "__main__":
    main()
