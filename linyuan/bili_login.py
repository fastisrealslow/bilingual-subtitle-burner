#!/usr/bin/env python3
"""B站登录：程序化生成二维码 → 自动轮询 → 落 biliup 凭据。

为什么需要这个（而不是 `biliup login`）：
  biliup 的登录是交互式 TUI，要 TTY，没法进定时任务。这里全程无交互：
  自己调 passport 接口拿二维码、渲染成 PNG、轮询扫码状态、写 cookies.json。

一次性操作：B站 cookie 有效期数月。之后每天的抓取→出片→投稿都不用再登录，
只需定期跑 `biliup renew` 续期（check_login.py 会在快过期时提醒）。

用法：
    python3 bili_login.py              # 生成二维码并等待扫码
    python3 bili_login.py --check      # 只检查当前登录状态
"""
import argparse
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).parent
QR_PNG = BASE / "bili_login_qr.png"
COOKIES = BASE / "cookies.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
GEN = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key="
NAV = "https://api.bilibili.com/x/web-interface/nav"

POLL_INTERVAL = 3
POLL_TIMEOUT = 180          # 二维码约 3 分钟过期

# 供控制台服务复用的会话态：qrcode_key → {opener, jar, created}
# 扫码登录是两步异步流程（生成二维码 → 轮询），cookie 必须落在同一个
# opener 上，所以跨请求保存。只留最近几个，避免无限堆积。
_SESSIONS = {}
_SESSION_MAX = 5


def start_qr_session():
    """生成一个扫码会话，返回 (qrcode_key, 二维码PNG路径)。"""
    op, jar = make_opener()
    d = json.loads(op.open(GEN, timeout=20).read().decode())
    if d.get("code") != 0:
        raise RuntimeError(f"取二维码失败：{d}")
    url, key = d["data"]["url"], d["data"]["qrcode_key"]
    png = BASE / f"bili_qr_{key[:10]}.png"
    render_qr(url, png)

    _SESSIONS[key] = {"op": op, "jar": jar, "png": png, "created": time.time()}
    # 清理陈旧会话及其图片
    for k in sorted(_SESSIONS, key=lambda k: _SESSIONS[k]["created"])[:-_SESSION_MAX]:
        old = _SESSIONS.pop(k)
        Path(old["png"]).unlink(missing_ok=True)
    return key, png


def poll_qr_session(key):
    """轮询一次。返回 {code, message, done, user}。

    code: 0=成功 86101=未扫码 86090=已扫待确认 86038=已过期
    """
    s = _SESSIONS.get(key)
    if not s:
        return {"code": -1, "message": "会话不存在或已过期，请重新生成", "done": False}
    try:
        body = json.loads(s["op"].open(POLL + key, timeout=20).read().decode())
    except Exception as e:
        return {"code": -2, "message": f"轮询异常 {type(e).__name__}", "done": False}

    data = body.get("data") or {}
    code = data.get("code")
    out = {"code": code, "message": data.get("message", ""), "done": False}
    if code == 0:
        try:
            save_cookies(s["jar"])
        except Exception as e:
            out["message"] = f"凭据保存失败：{e}"
            return out
        ok, who = check_login()
        out.update({"done": True, "ok": ok, "user": who})
        Path(s["png"]).unlink(missing_ok=True)
        _SESSIONS.pop(key, None)
    elif code == 86038:
        Path(s["png"]).unlink(missing_ok=True)
        _SESSIONS.pop(key, None)
    return out


def make_opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", UA), ("Referer", "https://www.bilibili.com/")]
    return op, jar


def render_qr(url, out_png):
    """用 node 的 qrcode 包出图。纯 Python 侧没有可用的 QR 库，
    而 npm 在本环境可用，所以走 node 生成。"""
    script = (
        "const QR=require('qrcode');"
        f"QR.toFile({json.dumps(str(out_png))},{json.dumps(url)},"
        "{width:480,margin:2,errorCorrectionLevel:'M'},"
        "e=>{if(e){console.error(e.message);process.exit(1)}});"
    )
    r = subprocess.run(["node", "-e", script], cwd=BASE,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"二维码生成失败：{r.stderr.strip()[:120]}")


def save_cookies(jar):
    """写成 biliup 认的 cookies.json（含 SESSDATA / bili_jct / DedeUserID）。"""
    ck = {c.name: c.value for c in jar}
    required = ["SESSDATA", "bili_jct", "DedeUserID"]
    missing = [k for k in required if k not in ck]
    if missing:
        raise RuntimeError(f"缺关键 cookie：{missing}")

    payload = {
        "cookie_info": {
            "cookies": [
                {"name": n, "value": v, "http_only": 0, "expires": 0, "secure": 0}
                for n, v in ck.items()
            ]
        },
        "sso": ["https://passport.bilibili.com/api/v2/sso"],
        "token_info": {
            "mid": int(ck.get("DedeUserID", 0)),
            "access_token": "",
            "refresh_token": "",
            "expires_in": 0,
        },
        "platform": "BiliTV",
    }
    COOKIES.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                       encoding="utf-8")
    COOKIES.chmod(0o600)          # 凭据文件，别给别的用户读
    return ck


def check_login():
    """当前凭据是否还有效。返回 (是否登录, 用户名)。"""
    if not COOKIES.exists():
        return False, "未登录（无 cookies.json）"
    try:
        data = json.loads(COOKIES.read_text(encoding="utf-8"))
        cookies = data["cookie_info"]["cookies"]
        hdr = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
    except (OSError, ValueError, KeyError) as e:
        return False, f"凭据文件损坏：{type(e).__name__}"

    req = urllib.request.Request(NAV, headers={"User-Agent": UA, "Cookie": hdr})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=20)
                       .read().decode("utf-8", "ignore"))
    except Exception as e:
        return False, f"校验请求失败：{type(e).__name__}"
    if d.get("code") == 0 and (d.get("data") or {}).get("isLogin"):
        u = d["data"]
        return True, f"{u.get('uname')} (mid={u.get('mid')})"
    return False, f"凭据已失效（code={d.get('code')}）"


def login():
    op, jar = make_opener()
    d = json.loads(op.open(GEN, timeout=20).read().decode())
    if d.get("code") != 0:
        sys.exit(f"取二维码失败：{d}")
    url, key = d["data"]["url"], d["data"]["qrcode_key"]

    render_qr(url, QR_PNG)
    print(f"二维码已生成：{QR_PNG}")
    print("请用手机 B站 App 扫码（约 3 分钟内有效）\n")

    deadline = time.time() + POLL_TIMEOUT
    last = None
    while time.time() < deadline:
        time.sleep(POLL_INTERVAL)
        try:
            r = op.open(POLL + key, timeout=20)
            body = json.loads(r.read().decode())
        except Exception as e:
            print(f"  轮询异常 {type(e).__name__}，继续", file=sys.stderr)
            continue
        code = (body.get("data") or {}).get("code")
        msg = (body.get("data") or {}).get("message", "")
        if code != last:
            print(f"  [{int(deadline - time.time()):3d}s] {msg}")
            last = code
        if code == 0:
            ck = save_cookies(jar)
            ok, who = check_login()
            print(f"\n✅ 登录成功：{who}")
            print(f"   凭据 → {COOKIES}（权限 600）")
            QR_PNG.unlink(missing_ok=True)      # 用完即删，别留在磁盘上
            return 0
        if code == 86038:
            print("\n❌ 二维码已过期，重新运行本脚本")
            return 1
    print("\n❌ 等待超时")
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只查登录状态")
    args = ap.parse_args()
    ok, who = check_login()
    if args.check:
        print(("✅ " if ok else "❌ ") + who)
        return 0 if ok else 1
    if ok:
        print(f"✅ 已登录：{who}（如需换号，先删 {COOKIES.name}）")
        return 0
    return login()


if __name__ == "__main__":
    sys.exit(main())
