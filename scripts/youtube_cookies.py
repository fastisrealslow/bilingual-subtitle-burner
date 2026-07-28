#!/usr/bin/env python3
"""把 YouTube 登录态 secret 落成 yt-dlp 能用的 Netscape cookies 文件。

    python scripts/youtube_cookies.py \
        --out "$RUNNER_TEMP/youtube_cookies.txt" --github-env "$GITHUB_ENV"

数据中心 IP 上的 yt-dlp 一律被 YouTube 回「Sign in to confirm you're not a
bot」，GitHub runner 也是数据中心 IP，所以取源必须带登录态。secret 从环境变量读
（``YOUTUBE_COOKIES_B64`` 优先，其次明文 ``YOUTUBE_COOKIES``），不走命令行 ——
命令行参数会进 ``ps`` 和日志。

明文那个槽位是给「手上没有命令行」的人留的：``cookies.txt`` 全选复制，直接粘进
GitHub 网页的 secret 输入框即可，不用先在本地跑 ``base64``。代价是粘贴会带进一些
伤（BOM、CRLF、首尾空行），这些在落盘前统一抹掉 —— 见 ``normalize_cookie_text``。

三条硬规则：

1. **不静默降级**：secret 配了但 base64 解不开、或解出来不是合法 Netscape 格式，
   一律退 1。退化成不带 cookies 去下载的话，YouTube 回的是登录墙那句话，和
   「片源不存在」长得一样，排查要绕一大圈。
2. **不落进工作区**：``--out`` 必须在仓库和 ``GITHUB_WORKSPACE`` 之外（用
   ``$RUNNER_TEMP``），否则可能被 ``git add`` 或 ``upload-artifact`` 带出去。
3. **不打内容**：GitHub 的 secret masking 只遮蔽 secret **原文**，base64 解码后
   的内容不在遮蔽范围内。所以这里只打记录条数和域名个数，任何报错都不回显文件
   内容 —— ``CookiesError`` 的文案里只允许出现行号和计数。

secret 没配也算成功（退 0）：archive.org、本地文件这些源不需要登录态。此时不写
文件、也不往 ``$GITHUB_ENV`` 写 ``COOKIES_FILE``，取源那边看到没这个变量就知道是
「没配」而不是「配坏了」，两种情况的处置完全不同。

退出码：0 成功（含未配置）/ 1 配置或凭据格式错误
"""

import base64
import binascii
import os
import re
import sys
from pathlib import Path

from cli_exit import EXIT_CONFIG, ConfigErrorArgumentParser

REPO_ROOT = Path(__file__).resolve().parent.parent

B64_ENV = "YOUTUBE_COOKIES_B64"
PLAIN_ENV = "YOUTUBE_COOKIES"
# 取源那边约定的入口：steps/step1_fetch.py 早就在读这个变量，沿用同一个名字。
COOKIES_ENV = "COOKIES_FILE"

# http.cookiejar.MozillaCookieJar 认的文件头（它的 magic_re 是
# ``#( Netscape)? HTTP Cookie File``）。浏览器扩展导出的 cookies.txt 自带这一行。
NETSCAPE_HEADER_RE = re.compile(
    r"^#\s*(?:Netscape\s+)?HTTP\s+Cookie\s+File", re.IGNORECASE)
NETSCAPE_FIELDS = 7
# 这个前缀开头的行是数据行而不是注释，curl / yt-dlp 用它标 HttpOnly。
HTTPONLY_PREFIX = "#HttpOnly_"
EXPIRES_RE = re.compile(r"^\d*(?:\.\d+)?$")

# UTF-8 BOM。记事本另存、部分浏览器扩展导出都会在开头带上它，带着它第一行就配不上
# NETSCAPE_HEADER_RE，报错会变成「第一行不是文件头」，看不出真实原因是一个隐形字符。
BOM = "\ufeff"

HOWTO = ("浏览器装 “Get cookies.txt LOCALLY” 扩展 → 登录 YouTube → 导出 "
         f"youtube.com 的 Netscape 格式 cookies → 全文原样粘进 {PLAIN_ENV}"
         f"（不需要命令行），或 `base64 -w0 cookies.txt` 的结果存进 {B64_ENV}。")


class CookiesError(Exception):
    """凭据不可用。

    文案里只允许出现行号、字段数这类计数信息 —— 这个异常会被打进 CI 日志，
    带上文件内容就等于把会话令牌公开了。
    """


def decode_secret(raw: str) -> str:
    """base64 → 文本。解不开就抛，绝不退化成「当明文用」。

    退化是有害的：``YOUTUBE_COOKIES_B64`` 里塞了明文时，当明文用能跑通，下次
    换个人来看就再也说不清这个 secret 到底该存哪种格式了。
    """
    compact = "".join(raw.split())
    if not compact:
        raise CookiesError(f"{B64_ENV} 只有空白字符")
    try:
        blob = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as e:
        raise CookiesError(
            f"{B64_ENV} 不是合法的 base64（{e}）。要存明文 Netscape 全文请改用 "
            f"{PLAIN_ENV} 这个 secret") from e
    if not blob:
        raise CookiesError(f"{B64_ENV} 解出来是空的")
    try:
        return blob.decode("utf-8")
    except UnicodeDecodeError as e:
        # 只带 reason，不带 str(e) —— 后者会把出错的那几个字节原样打出来
        raise CookiesError(
            f"{B64_ENV} 解出来的不是 UTF-8 文本（{e.reason}），"
            "多半是把二进制文件或别的 secret 编进去了") from e


def normalize_cookie_text(text: str) -> str:
    """抹掉复制粘贴带进来的伤，只抹不可能掩盖「真的不合法」的那几种。

    只做三件事：去 BOM、换行统一成 LF、去掉首尾的空行。这三种伤都是**整行之外**
    的东西，抹掉它们不会让一个非法的 jar 变合法 —— 字段个数、分隔符、时间戳该错
    的照样错。

    反过来，畸形的 cookie 行一概不修：补字段、把空格猜成制表符之类的「修复」会
    把一份内容存疑的凭据放行，而它到了 yt-dlp 那里才炸，报错里带的是会话令牌。
    """
    if text.startswith(BOM):
        text = text[len(BOM):]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def validate_netscape(text: str) -> tuple[int, int]:
    """校验 Netscape cookies 格式，返回 ``(记录条数, 域名个数)``。

    宁严不宽：格式不对时 yt-dlp 自己也会拒，而它的报错会把出错那一行**原样**
    打出来 —— 那一行里就是会话令牌。自己先拦下来，日志里就只剩行号。
    """
    lines = text.splitlines()
    first = next((ln for ln in lines if ln.strip()), None)
    if first is None:
        raise CookiesError("cookies 内容是空的")
    if not NETSCAPE_HEADER_RE.match(first):
        raise CookiesError(
            "第一行不是 Netscape cookies 文件头（应为 "
            "`# Netscape HTTP Cookie File`）。浏览器扩展导出的 cookies.txt "
            "自带这一行，别手工删掉；JSON 格式的 cookies 也不能直接用")

    records, domains = 0, set()
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if line.startswith("#") and not line.startswith(HTTPONLY_PREFIX):
            continue
        fields = line.split("\t")
        if len(fields) != NETSCAPE_FIELDS:
            raise CookiesError(
                f"第 {n} 行有 {len(fields)} 个字段，Netscape 格式要求 "
                f"{NETSCAPE_FIELDS} 个、且分隔符必须是制表符（空格对齐的导出、"
                "被编辑器把 Tab 换成空格的文件都不合法）")
        domain, _flag, _path, _secure, expires, _name, _value = fields
        if not domain.strip():
            raise CookiesError(f"第 {n} 行的 domain 字段是空的")
        if not EXPIRES_RE.match(expires.strip()):
            raise CookiesError(
                f"第 {n} 行的 expires 字段不是 Unix 时间戳（第 5 个字段）")
        records += 1
        domains.add(domain.strip())

    if not records:
        raise CookiesError("整个文件只有注释，没有一条 cookie 记录")
    return records, len(domains)


def guard_outside_workspace(out: Path) -> None:
    """cookies 文件不许落在仓库工作区里。

    落在里面就有两条泄露路径：``git add`` 连带提交、``upload-artifact`` 打包带
    出去（出片失败时会上传整个 ``_tmp/``）。老的 pipeline.yml 写的是仓库里的
    ``secrets/``，正是这个问题。
    """
    resolved = out.expanduser().resolve()
    bases = [("仓库工作区", REPO_ROOT),
             ("GITHUB_WORKSPACE", os.environ.get("GITHUB_WORKSPACE") or "")]
    for label, base in bases:
        if not base:
            continue
        base = Path(base).expanduser().resolve()
        if resolved == base or base in resolved.parents:
            raise CookiesError(
                f"--out 指到{label}（{base}）里面了。cookies 文件必须放在工作区"
                "之外（CI 里用 $RUNNER_TEMP），否则会被 git add 或 "
                "upload-artifact 带出去")


def write_cookies_file(out: Path, text: str) -> None:
    """0600 落盘。先 open 后 chmod：文件已存在时也要把权限收回来。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(out), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    out.chmod(0o600)


def append_github_env(github_env: Path, out: Path) -> None:
    """只写路径。路径不是秘密，文件内容才是。"""
    with open(github_env, "a", encoding="utf-8") as f:
        f.write(f"{COOKIES_ENV}={out}\n")


def is_valid_netscape(text: str) -> bool:
    """``validate_netscape`` 的判断式版本，用在需要「先试试」的地方。"""
    try:
        validate_netscape(text)
    except CookiesError:
        return False
    return True


def rescue_base64_in_plain_slot(text: str) -> str | None:
    """明文槽位里塞了 base64 时救一把；不像 base64 或救不出来返回 ``None``。

    仲裁标准故意定得很硬：**解出来必须本身就是一份合法的 Netscape jar**，才认定
    「他是把 base64 粘错槽位了」。所以这个分支不可能误伤合法明文 —— 合法明文在
    调用点已经先校验通过、根本走不到这里；退一步说，合法 jar 必然以 ``#`` 打头的
    文件头起步，而 ``#`` 不在 base64 字母表里，``validate=True`` 那一步就先拒了。

    救出来照样要在日志里点明用错了 secret：这次能跑通，下次换个人来看就说不清这
    两个 secret 各该存什么了。
    """
    try:
        blob = base64.b64decode("".join(text.split()), validate=True)
        decoded = normalize_cookie_text(blob.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return decoded if is_valid_netscape(decoded) else None


def load_plain_secret(raw: str) -> str:
    """明文槽位：归一化后原样用；只有在它不合法、且确实是 base64 时才解码。

    救不出来时返回归一化后的原文，让调用点的 ``validate_netscape`` 去报那条带行号
    的准确错误 —— 这里自己造一句「不是 Netscape 格式」的话，行号就丢了。
    """
    text = normalize_cookie_text(raw)
    if is_valid_netscape(text):
        return text
    rescued = rescue_base64_in_plain_slot(text)
    if rescued is None:
        return text
    print(f"[cookies] ⚠️ {PLAIN_ENV} 里装的是 base64，不是 Netscape 全文 —— "
          f"已按 base64 解开继续，但 secret 用错了：base64 该存进 {B64_ENV}，"
          f"{PLAIN_ENV} 存的是 cookies.txt 全文原样。",
          file=sys.stderr, flush=True)
    return rescued


def load_secret() -> tuple[str, str] | None:
    """读 secret，返回 ``(文本, 来源变量名)``；两个都没配返回 ``None``。"""
    raw_b64 = os.environ.get(B64_ENV) or ""
    if raw_b64.strip():
        return normalize_cookie_text(decode_secret(raw_b64)), B64_ENV
    raw_plain = os.environ.get(PLAIN_ENV) or ""
    if raw_plain.strip():
        return load_plain_secret(raw_plain), PLAIN_ENV
    return None


def main(argv=None) -> int:
    p = ConfigErrorArgumentParser(
        description="把 YouTube 登录态 secret 落成 Netscape cookies 文件")
    p.add_argument("--out", required=True,
                   help="cookies 文件写到哪（必须在仓库工作区之外，"
                        "CI 里用 $RUNNER_TEMP）")
    p.add_argument("--github-env", default="",
                   help=f"往这个文件追加 {COOKIES_ENV}=<路径>（CI 里传 "
                        f"$GITHUB_ENV）。只在真的写了 cookies 时才追加")
    args = p.parse_args(argv)

    out = Path(args.out).expanduser()
    try:
        guard_outside_workspace(out)
        loaded = load_secret()
        if loaded is None:
            print(f"[cookies] 未配置 YouTube 凭据（{B64_ENV} / {PLAIN_ENV} 都是"
                  "空的）：本次取源不带 cookies。archive.org、本地文件等非 "
                  "YouTube 源不受影响；YouTube 源会被登录墙挡回并明确报错。",
                  flush=True)
            return 0
        text, origin = loaded
        records, domains = validate_netscape(text)
    except CookiesError as e:
        print(f"[cookies] ❌ {e}", file=sys.stderr, flush=True)
        print(f"[cookies]    {HOWTO}", file=sys.stderr, flush=True)
        return EXIT_CONFIG

    write_cookies_file(out, text)
    if args.github_env:
        append_github_env(Path(args.github_env).expanduser(), out)
    # 只打计数，不打域名和内容
    print(f"[cookies] 已写入 {out}（来自 {origin}，{records} 条记录、"
          f"{domains} 个域名，权限 600）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
