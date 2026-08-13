#!/usr/bin/env python3
"""把 B站登录态 secret 落成 biliup 能用的 cookies.json 文件。

    python3 linyuan/bili_cookies.py \
        --out "$RUNNER_TEMP/bili_cookies.json" --github-env "$GITHUB_ENV"

与 scripts/youtube_cookies.py 同一套约定，三条硬规则照搬：

1. **不静默降级**：secret 配了但 JSON 解不开、或缺 SESSDATA/bili_jct/DedeUserID
   三件套，一律退 1。退化成不带 cookies 去投稿的话，biliup 回的报错和
   「投稿被拒」长得一样，排查要绕一大圈。
2. **不落进工作区**：``--out`` 必须在仓库和 ``GITHUB_WORKSPACE`` 之外（用
   ``$RUNNER_TEMP``），否则可能被 ``git add`` 或 ``upload-artifact`` 带出去。
3. **不打内容**：GitHub 的 secret masking 只遮蔽 secret **原文**，落盘后的
   内容不在遮蔽范围。这里只打记录条数，报错文案里只允许出现键名和计数。

secret 没配也算成功（退 0）：此时不写文件、也不往 ``$GITHUB_ENV`` 写
``BILI_COOKIES_FILE``，投稿步骤靠这个变量判断「没配」还是「配好了」。

退出码：0 成功（含未配置）/ 1 配置或凭据格式错误
"""
import argparse
import json
import os
import sys
from pathlib import Path

PLAIN_ENV = "BILIBILI_COOKIES"        # secret 槽位：cookies.json 全文
COOKIES_ENV = "BILI_COOKIES_FILE"     # 约定出口：投稿步骤读这个变量拿文件路径
REQUIRED = {"SESSDATA", "bili_jct", "DedeUserID"}


class CookiesError(Exception):
    """凭据格式问题。文案只允许出现键名/条数，不允许出现任何值。"""


def load_cookie_names(text: str) -> set:
    """从 cookies.json 文本里取出 cookie 名集合。兼容两种格式。"""
    try:
        d = json.loads(text)
    except json.JSONDecodeError as e:
        raise CookiesError(f"不是合法 JSON（第 {e.lineno} 行）")
    entries = d.get("cookies") if isinstance(d, dict) else None
    if isinstance(entries, list):
        return {e.get("name") for e in entries if isinstance(e, dict)}
    if isinstance(d, dict) and "SESSDATA" in d:
        return set(d)                      # 裸 {name: value} 字典
    raise CookiesError("认不出格式：既不是 biliup 的 {cookies:[...]} 也不是 {name:value} 字典")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="落盘路径（须在仓库外）")
    ap.add_argument("--github-env", default=None, help="GITHUB_ENV 路径")
    args = ap.parse_args()

    out = Path(args.out)
    ws = os.environ.get("GITHUB_WORKSPACE", "")
    if ws and str(out.resolve()).startswith(str(Path(ws).resolve())):
        raise CookiesError(f"--out 不能落在工作区里：{out}")

    raw = (os.environ.get(PLAIN_ENV) or "").strip()
    if not raw:
        print(f"[bili-cookies] 未配置 {PLAIN_ENV}，投稿步骤将跳过")
        return 0

    # 粘贴伤：网页 secret 框会带进 BOM / CRLF / 首尾空行，统一抹掉
    text = raw.lstrip("﻿").replace("\r\n", "\n").strip()

    names = load_cookie_names(text)
    missing = REQUIRED - names
    if missing:
        raise CookiesError(f"缺关键 cookie：{sorted(missing)}（登录态三件套不全）")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    out.chmod(0o600)
    print(f"[bili-cookies] 校验通过：{len(names)} 条记录 → {out}")

    if args.github_env:
        with open(args.github_env, "a", encoding="utf-8") as f:
            f.write(f"{COOKIES_ENV}={out}\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CookiesError as e:
        print(f"::error::B站登录态无效：{e}", file=sys.stderr)
        sys.exit(1)
