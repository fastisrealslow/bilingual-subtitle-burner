#!/usr/bin/env python3
"""
step9_douyin.py — 抖音自动上传（Step 9，Playwright 网页自动化）

抖音无官方开放上传 API，本脚本通过 Playwright 驱动创作者平台网页版
（https://creator.douyin.com/creator-micro/content/upload）完成上传。

⚠️ 现实约束（务必知晓）：
  1. 数据中心 IP（GitHub Actions）极易触发抖音风控 / 验证码 / 登录态失效；
     业界稳妥做法是在家用/本地 IP 的机器上跑本步骤。
  2. cookie 时效短，失效后需重新在浏览器登录导出，CI 内无法扫码。
  3. 抖音前端 DOM 会变动，选择器可能需要不定期维护。

cookie 来源（二选一，优先 --douyin-cookies）：
  - JSON 文件：Playwright storage_state 格式，或 [{name,value,domain,path},...] 列表
  - 环境变量 DOUYIN_COOKIES 指向上述文件路径

用法：
  python steps/step9_douyin.py \
      --job-dir output/jobs/xxx \
      --douyin-cookies secrets/douyin.json \
      [--do-upload] [--upload-interval 60] [--headful]

不加 --do-upload 时只做“干跑”（校验 cookie 文件与成片是否齐全，不真正上传）。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import platform_rules as PR  # noqa: E402

UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"


def load_upload_list(job_dir: Path) -> list:
    """读取 step8 生成的 upload_list.json；抖音优先用竖屏成片。"""
    f = job_dir / "upload_list.json"
    if not f.exists():
        print(f"[douyin] ❌ 找不到 {f}，请先跑完 step8。", flush=True)
        sys.exit(2)
    with open(f, encoding="utf-8") as fp:
        return json.load(fp)


def resolve_video(entry: dict) -> Path | None:
    """定位抖音上传用视频。

    step8 打包时复制的 video.mp4 就是 clip.py 的成片；
    若 pipeline 开了 --vertical，它已经是 1080×1920 竖屏成片（同名覆盖）。
    因此直接用素材包内的 video.mp4 即可。
    """
    sub_dir = Path(entry.get("package_dir", ""))
    p = sub_dir / "video.mp4"
    return p if p.exists() else None


def load_cookies(cookies_path: str):
    """把 cookie 文件解析成 Playwright storage_state 可用结构。
    支持两种格式：storage_state({cookies:[...]}) 或纯 cookie 列表。"""
    with open(cookies_path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "cookies" in data:
        return data  # 已是 storage_state
    if isinstance(data, list):
        # 补全 Playwright 需要的字段
        for c in data:
            c.setdefault("domain", ".douyin.com")
            c.setdefault("path", "/")
        return {"cookies": data, "origins": []}
    raise ValueError("无法识别的 cookie 文件格式")


def upload_one(page, video: Path, title: str, tags: list, timeout_ms: int = 180000) -> bool:
    """在已打开的创作者页面上传单条视频。返回是否成功进入发布流程。"""
    from playwright.sync_api import TimeoutError as PWTimeout
    try:
        page.goto(UPLOAD_URL, wait_until="networkidle", timeout=60000)
    except PWTimeout:
        print("[douyin]   ❌ 打开上传页超时（可能登录态失效）", flush=True)
        return False

    # 检测是否被踢回登录页
    if "login" in page.url or "passport" in page.url:
        print("[douyin]   ❌ 登录态失效，被重定向到登录页。请重新导出 cookie。", flush=True)
        return False

    # 选择文件（input[type=file]）
    try:
        file_input = page.locator('input[type="file"]').first
        file_input.set_input_files(str(video), timeout=30000)
    except PWTimeout:
        print("[douyin]   ❌ 找不到文件上传控件（DOM 可能已变动）", flush=True)
        return False

    print(f"[douyin]   ▶ 已选择文件，等待转码上传：{video.name}", flush=True)

    # 等待标题输入框出现（上传完成的信号之一）
    title_box = None
    for sel in ('input[placeholder*="标题"]',
                'div[contenteditable="true"]',
                'textarea[placeholder*="标题"]'):
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            title_box = loc
            break
        except PWTimeout:
            continue
    if title_box is None:
        print("[douyin]   ❌ 等待标题框超时（上传/转码未完成或 DOM 变动）", flush=True)
        return False

    # 填标题
    try:
        title_box.click()
        title_box.fill("")
        title_box.type(title[:PR.DOUYIN_TITLE_MAX], delay=20)
    except Exception as e:
        print(f"[douyin]   ⚠ 标题填写异常: {e}", flush=True)

    # 填话题标签（抖音用 # 触发）
    try:
        for tag in tags[:5]:
            title_box.type(f" #{tag}", delay=20)
            time.sleep(0.5)
    except Exception:
        pass

    # 找“发布”按钮
    published = False
    for sel in ('button:has-text("发布")',
                'button:has-text("发 布")',
                'text=发布'):
        try:
            btn = page.locator(sel).first
            btn.wait_for(state="visible", timeout=8000)
            btn.click(timeout=8000)
            published = True
            break
        except PWTimeout:
            continue
        except Exception:
            continue

    if not published:
        print("[douyin]   ❌ 找不到发布按钮（DOM 可能已变动）", flush=True)
        return False

    # 等待跳转到内容管理页 = 发布成功
    try:
        page.wait_for_url("**/content/manage**", timeout=30000)
        print("[douyin]   ✅ 抖音发布成功", flush=True)
        return True
    except PWTimeout:
        print("[douyin]   ⚠ 已点发布但未确认跳转（可能仍在处理，请人工复核）", flush=True)
        return True


def main():
    parser = argparse.ArgumentParser(description="Step 9: 抖音自动上传（Playwright）")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--douyin-cookies", default="",
                        help="抖音 cookie 文件路径；也可由 DOUYIN_COOKIES 环境变量指定")
    parser.add_argument("--do-upload", action="store_true",
                        help="实际上传；不加则只做干跑校验")
    parser.add_argument("--headful", action="store_true",
                        help="有头模式（本地调试用；CI 里省略走无头）")
    parser.add_argument("--upload-interval", type=int, default=60,
                        help="多条上传间隔秒数（防风控）")
    parser.add_argument("--top-n", type=int, default=0,
                        help="只上传前 N 条（0=全部）")
    args = parser.parse_args()

    job_dir = Path(args.job_dir)
    cookies_path = args.douyin_cookies or (os.environ.get("DOUYIN_COOKIES") or "").strip()

    upload_list = load_upload_list(job_dir)
    if args.top_n > 0:
        upload_list = upload_list[:args.top_n]

    # 校验成片
    ready = []
    for entry in upload_list:
        v = resolve_video(entry)
        if v:
            ready.append((entry, v))
        else:
            print(f"[douyin] ⚠ [{entry.get('rank')}] 找不到成片，跳过", flush=True)
    print(f"[douyin] 待上传成片：{len(ready)} 条", flush=True)

    # 发布前体检。拖到真上传才发现字段超限/时长超标，代价太大，
    # 所以干跑和真跑都先跑一遍。抖音网页发布与开放平台 API 的审核逻辑一致，
    # 所以直接拿官方 API 的字段限制当基准。
    print("[douyin] ── 发布前体检 ──", flush=True)
    blocked = 0
    for entry, v in ready:
        rank = entry.get("rank")
        t, tw = PR.clean_title(entry.get("title", ""), "douyin")
        tg, gw = PR.clean_tags(entry.get("tags", []), "douyin")
        vw = PR.validate_video(str(v), "douyin")
        entry["title"] = t
        entry["tags"] = tg
        PR.report(tw + gw + vw, f"[{rank:02d}] 抖音")
        if vw:
            blocked += 1
    if blocked:
        print(f"[douyin] ⚠ {blocked} 条成片存在规格问题，上传可能被退回", flush=True)
    else:
        print("[douyin] ✅ 字段与规格体检通过", flush=True)

    if not args.do_upload:
        print("[douyin] 干跑模式（未加 --do-upload）：仅校验，不上传。", flush=True)
        if not cookies_path or not Path(cookies_path).exists():
            print(f"[douyin] ⚠ 未提供有效 cookie 文件：{cookies_path!r}（上传时必需）", flush=True)
        return

    # 真实上传：校验 cookie
    if not cookies_path or not Path(cookies_path).exists():
        print(f"[douyin] ❌ 未找到抖音 cookie 文件：{cookies_path!r}", flush=True)
        print("[douyin]    请在浏览器登录抖音创作者后导出 cookie（storage_state 或列表），存入 Secrets。", flush=True)
        sys.exit(2)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[douyin] ❌ 未安装 playwright。请 `pip install playwright && playwright install chromium`。", flush=True)
        sys.exit(3)

    storage_state = load_cookies(cookies_path)

    ok, fail = 0, 0
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not args.headful,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            storage_state=storage_state,
            viewport={"width": 1440, "height": 900},
            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )
        page = context.new_page()
        for i, (entry, video) in enumerate(ready):
            title = entry.get("title", f"clip_{entry.get('rank')}")
            tags = entry.get("tags", ["投资", "价值投资"])
            print(f"\n[douyin] [{i+1}/{len(ready)}] 上传：{title[:40]}", flush=True)
            success = upload_one(page, video, title, tags)
            results.append({"rank": entry.get("rank"), "title": title, "uploaded": success})
            ok += int(success)
            fail += int(not success)
            if i < len(ready) - 1 and args.upload_interval > 0:
                time.sleep(args.upload_interval)
        context.close()
        browser.close()

    with open(job_dir / "douyin_result.json", "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "fail": fail, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n[douyin] ========== 抖音上传完成：成功 {ok} / 失败 {fail} ==========", flush=True)
    if fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
