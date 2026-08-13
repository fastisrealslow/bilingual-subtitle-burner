#!/usr/bin/env python3
"""林园监控流水线 · 本地可视化控制台。

设计取舍
--------
* 纯标准库 http.server，不引第三方框架 —— 这套项目一直是零依赖，
  控制台不该破坏这一点。
* **只允许执行白名单里的预定义步骤**，不接受前端传任意命令。
  这是个能触发 shell 的 HTTP 服务，不做白名单等于开后门。
* 任务在后台线程跑，输出实时写日志文件，前端轮询增量拉取。
  长任务（转写、下载）动辄几分钟，不能阻塞请求。

用法：
    python3 server.py                 # 监听 0.0.0.0:8420
    python3 server.py --port 9000
"""
import argparse
import json
import mimetypes
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).parent
WEB = BASE / "console"
LOGS = BASE / ".jobs"
LOGS.mkdir(exist_ok=True)

PY = sys.executable

# ── 白名单：前端只能触发这些，且参数固定 ──────────────────────────────
STEPS = {
    "healthcheck": {
        "name": "接口巡检",
        "desc": "探测 5 个关键接口，防静默失败",
        "cmd": [PY, "healthcheck.py"],
        "est": "10s",
    },
    "monitor": {
        "name": "抓取监控源",
        "desc": "7 个源：B站/微博/腾讯/抖音/好看/网易/股东大会公告",
        "cmd": [PY, "-u", "monitor_v2.py"],
        "est": "1-3min",
        "pre": ["rm -f monitor_v2_state.json"],
    },
    "download": {
        "name": "下载视频",
        "desc": "趁直链未过期立即落盘（微博直链仅 1 小时有效）",
        "cmd": [PY, "fetch_videos.py", "--all"],
        "est": "5-20min",
    },
    "organize": {
        "name": "归档分类",
        "desc": "按股东大会/演讲/专访/切片分目录",
        "cmd": [PY, "organize_videos.py", "--apply"],
        "est": "5s",
    },
    "select": {
        "name": "选片",
        "desc": "按价值排序，挑出可出片的候选",
        "cmd": [PY, "bridge_produce.py"],
        "est": "30s",
    },
    "backtest": {
        "name": "能力回测",
        "desc": "验证时间领先/内容覆盖/数据可用三项未退化",
        "cmd": [PY, "backtest.py"],
        "est": "30s",
    },
    "checklinks": {
        "name": "直链体检",
        "desc": "检查已入库直链是否还能下载",
        "cmd": [PY, "check_links.py"],
        "est": "1min",
    },
    "pipeline": {
        "name": "全流程",
        "desc": "巡检 → 抓取 → 下载 → 归档 → 选片（不含投稿）",
        "cmd": ["bash", "pipeline.sh", "--no-upload"],
        "est": "10-30min",
    },
}

# 运行中的任务：job_id → {step, started, proc, rc}
JOBS = {}
JOBS_LOCK = threading.Lock()


def log_path(job_id):
    return LOGS / f"{job_id}.log"


def run_job(job_id, step_key):
    step = STEPS[step_key]
    lp = log_path(job_id)
    with open(lp, "w", encoding="utf-8") as f:
        f.write(f"$ {' '.join(step['cmd'])}\n\n")
        f.flush()
        for pre in step.get("pre", []):
            subprocess.run(pre, shell=True, cwd=BASE)
        try:
            p = subprocess.Popen(step["cmd"], cwd=BASE, stdout=f,
                                 stderr=subprocess.STDOUT, text=True)
            with JOBS_LOCK:
                JOBS[job_id]["proc"] = p
            rc = p.wait()
        except Exception as e:
            f.write(f"\n[启动失败] {type(e).__name__}: {e}\n")
            rc = -1
        f.write(f"\n[退出码 {rc}]\n")
    with JOBS_LOCK:
        JOBS[job_id]["rc"] = rc
        JOBS[job_id]["finished"] = time.time()


def collect_status():
    """给前端的全局状态。所有读取都容错，任一环节缺失不影响其余显示。"""
    def safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    data = safe(lambda: json.loads((BASE / "dashboard" / "data.json")
                                   .read_text(encoding="utf-8")), []) or []
    by_src = {}
    for x in data:
        by_src[x.get("source", "?")] = by_src.get(x.get("source", "?"), 0) + 1

    vids = safe(lambda: list((BASE / "videos").rglob("*.mp4")), []) or []
    vid_by_cat = {}
    for p in vids:
        vid_by_cat[p.parent.name] = vid_by_cat.get(p.parent.name, 0) + 1

    delivers = []
    dd = BASE / "deliver"
    if dd.exists():
        for sub in sorted(dd.iterdir()):
            f = sub / "final.mp4"
            if f.is_file():
                meta = safe(lambda: json.loads((sub / "meta.json")
                            .read_text(encoding="utf-8")), {}) or {}
                delivers.append({
                    "slug": sub.name,
                    "size_mb": round(f.stat().st_size / 1048576, 1),
                    "occasion": meta.get("occasion", ""),
                    "segments": len(meta.get("segments", [])),
                })

    uploaded = safe(lambda: json.loads((BASE / "uploaded.json")
                    .read_text(encoding="utf-8")).get("uploaded", []), []) or []

    seeds = {}
    for f, key in (("douyin_seeds.json", "urls"), ("haokan_seeds.json", "vids"),
                   ("netease_seeds.json", "vcodes")):
        d = safe(lambda f=f: json.loads((BASE / f).read_text(encoding="utf-8")), {}) or {}
        seeds[f.split("_")[0]] = len(d.get(key, []))

    logged_in = safe(lambda: subprocess.run(
        [PY, "bili_login.py", "--check"], cwd=BASE,
        capture_output=True, text=True, timeout=25).returncode == 0, False)

    with JOBS_LOCK:
        jobs = [{"id": k, "step": v["step"], "name": STEPS[v["step"]]["name"],
                 "started": v["started"],
                 "running": v.get("rc") is None,
                 "rc": v.get("rc")}
                for k, v in sorted(JOBS.items(), key=lambda kv: -kv[1]["started"])][:12]

    return {
        "now": datetime.now().isoformat(timespec="seconds"),
        "steps": [{"key": k, **{kk: vv for kk, vv in v.items() if kk != "cmd"}}
                  for k, v in STEPS.items()],
        "monitor": {"total": len(data), "by_source": by_src},
        "videos": {"total": len(vids), "by_category": vid_by_cat},
        "delivers": delivers,
        "uploaded": uploaded,
        "seeds": seeds,
        "bili_logged_in": logged_in,
        "jobs": jobs,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                    # 静音默认访问日志

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # 允许跨域：控制台页面可能被嵌在别的源（如聊天界面）里，
        # 那时 fetch 是跨源请求。服务只听内网，且只能触发白名单步骤。
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)

        if u.path in ("/", "/index.html"):
            f = WEB / "index.html"
            if not f.exists():
                return self._send(500, "console/index.html 缺失", "text/plain; charset=utf-8")
            return self._send(200, f.read_text(encoding="utf-8"),
                              "text/html; charset=utf-8")

        if u.path == "/api/status":
            return self._send(200, collect_status())

        if u.path == "/api/log":
            job = (q.get("job") or [""])[0]
            off = int((q.get("offset") or ["0"])[0])
            lp = log_path(job)
            if not job or not lp.exists():
                return self._send(404, {"error": "no such job"})
            raw = lp.read_bytes()
            chunk = raw[off:]
            with JOBS_LOCK:
                info = JOBS.get(job, {})
            return self._send(200, {
                "offset": len(raw),
                "text": chunk.decode("utf-8", "replace"),
                "running": info.get("rc") is None,
                "rc": info.get("rc"),
            })

        if u.path == "/api/artifact":
            name = (q.get("name") or [""])[0]
            # 只允许取白名单里声明过的产物，避免被当成任意文件读取
            allowed = {s.get("artifact") for s in STEPS.values() if s.get("artifact")}
            # 扫码登录的二维码是动态文件名，另行校验前缀
            if name not in allowed and not (
                    name.startswith("bili_qr_") and name.endswith(".png")
                    and "/" not in name and "\\" not in name):
                return self._send(403, {"error": "not allowed"})
            f = BASE / name
            if not f.exists():
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            return self._send(200, f.read_bytes(), ctype)

        if u.path == "/api/bili/poll":
            key = (q.get("key") or [""])[0]
            try:
                import bili_login
                return self._send(200, bili_login.poll_qr_session(key))
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)

        if u.path == "/api/bili/qr":
            try:
                import bili_login
                key, png = bili_login.start_qr_session()
                return self._send(200, {"key": key, "png": Path(png).name})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})

        if u.path != "/api/run":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        step = body.get("step")
        if step not in STEPS:
            return self._send(400, {"error": f"未知步骤：{step}"})

        with JOBS_LOCK:
            busy = [k for k, v in JOBS.items()
                    if v.get("rc") is None and v["step"] == step]
        if busy:
            return self._send(409, {"error": "该步骤正在运行", "job": busy[0]})

        job_id = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            JOBS[job_id] = {"step": step, "started": time.time(), "rc": None}
        threading.Thread(target=run_job, args=(job_id, step), daemon=True).start()
        return self._send(200, {"job": job_id, "step": step})


def _lan_ip():
    """探测本机局域网 IP。绝不硬编码 —— 硬编码等于把内部网络拓扑泄进公开仓库。"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # 不真的发包，只用来确定出口网卡
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8420)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"控制台 → http://{_lan_ip()}:{args.port}/")
    print(f"        （本机 http://127.0.0.1:{args.port}/）")
    srv.serve_forever()


if __name__ == "__main__":
    main()
