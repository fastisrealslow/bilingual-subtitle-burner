#!/usr/bin/env python3
"""B 站投稿的预留接口：把 ``queue.json`` 里的一集拼成 ``biliup upload`` 的参数。

    python scripts/publish_bilibili.py --queue deliver/<slug>/queue.json \
        --episode ep01 --dry-run

**默认关闭。** workflow 里那一步的条件是 ``secrets.BILIBILI_COOKIES != ''``，
这个 secret 目前不存在，所以永远跳过。用户明确的决定是先手动投稿，这里只把
接口位置和参数留好，将来一个开关接上。

cookie 缺失时**明确报错**而不是静默跳过：真到了开自动投稿那天，一次静默
「成功」会让人以为片子发出去了，其实一条都没发。要跳过是 workflow 那一层的
事，脚本被调起来了就必须真投或者真报错。

``--cookies`` / ``BILIBILI_COOKIES_FILE`` 期望的是**文件路径**，指向
``scripts/bilibili_cookies.py`` 落盘的 biliup cookies.json——不是 cookie 正文。
cookie 正文由那个脚本负责从 secret 落成文件，这里只管读路径，绝不把任何凭据
内容拼进异常文案（GitHub 的 secret masking 只遮蔽 secret 原文，报错里带出来的
片段不会被遮蔽）。

## 幂等：已投过的集直接跳过

投稿前会去读 ``site/data/index.json`` 里同一条记录的 ``publish.bilibili``——
非空就说明这一集已经投过，直接跳过（退 0，不算失败）。这是必须的：
``produce.py`` 每次重出都会把 ``queue.json`` 里的 ``publish`` 字段重新置空
（它是新生成的清单，不携带历史状态），历史状态的唯一持久载体是已经合并、
提交进 ``main`` 的 ``site/data/index.json``。没有这一层，同一批素材换代码重跑
几次，配了 cookie 之后每次都会把同一集再投一遍，B 站有重复投稿风控，删稿又要
一条条手动删。

投稿成功后把 biliup 返回的稿件标识写回 ``site/data/index.json`` 的
``publish.bilibili``，供下一次判断幂等用。

退出码：0 成功（含「已投过，跳过」）/ 1 配置错误（缺 cookie、缺产物、集号不
存在、超出投稿上限）/ 3 biliup 失败
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from cli_exit import EXIT_CONFIG, ConfigErrorArgumentParser

EXIT_OK = 0
EXIT_API = 3

# 财经商业。B 站分区 id，投稿时可在网页端改，这里给个不会被打回的默认值。
DEFAULT_TID = 207
# 自制稿件。1 = 自制，2 = 转载。我们做的是翻译剪辑，按转载报更稳妥，
# 但用户的频道是自己配字幕的成片，默认 1 并允许覆盖。
DEFAULT_COPYRIGHT = 1
DEFAULT_LINE = "bda2"
# 落盘后的 cookies.json 路径，由 scripts/bilibili_cookies.py 写进 $GITHUB_ENV。
# 不是 secret 原文，是文件路径——语义上专门跟旧的 BILIBILI_COOKIES（secret
# 原文）区分开，避免重新犯「以为环境变量里装的是路径，其实是正文」的错。
COOKIE_ENV = "BILIBILI_COOKIES_FILE"

# 用户明确要求：先只投一集试水，别一次投 7 集。默认值硬编码在这里，不依赖
# 调用方每次记得传参——workflow 侧的 input 留空时也落到这个值（见 produce.yml
# 「投稿 B 站」步骤的注释）。
DEFAULT_MAX_EPISODES = 1


class ConfigError(Exception):
    """投稿前就能发现的问题：缺 cookie、缺文件、集号不存在、上限非法。

    文案里绝不允许出现 cookie 内容或其任何片段——见模块顶部说明。
    """


def find_episode(queue: dict, episode_id: str) -> dict:
    for ep in queue.get("episodes", []):
        if ep.get("id") == episode_id:
            return ep
    known = [ep.get("id") for ep in queue.get("episodes", [])]
    raise ConfigError(f"queue.json 里没有 {episode_id}，现有 {known}")


def resolve_cookies(explicit: str | None = None) -> Path:
    """定位 cookie 文件路径。没有就报错 —— 静默跳过会让人以为片子发出去了。

    这里只处理路径，不处理 secret 正文：正文的落盘、格式校验由
    ``scripts/bilibili_cookies.py`` 负责，那一步失败会直接退 1，不会让这里
    读到一个装着正文的环境变量。
    """
    raw = (explicit or os.environ.get(COOKIE_ENV) or "").strip()
    if not raw:
        raise ConfigError(
            f"缺少 B 站 cookie 文件路径：设置 {COOKIE_ENV}（由 "
            "scripts/bilibili_cookies.py 落盘后写入）或传 --cookies 指向 "
            f"biliup 的 cookies.json。没有 cookie 就投不了稿，这里不静默跳过")
    path = Path(raw)
    if not path.is_file():
        # 注意：这里不能把 raw/path 原样拼进报错文案。如果有人把 cookie 正文
        # 误填进了这个环境变量（正是缺口一修复前发生过的事），raw 里装的就是
        # 真凭据，原样拼进异常会让它流入日志/CI 输出——GitHub 的 secret
        # masking 只遮蔽 secret 原文本身，报错里拼出来的片段不保证被遮蔽。只报
        # 长度，不报内容，够诊断且安全。
        raise ConfigError(
            f"cookie 文件不存在（收到的路径字符串长度为 {len(raw)}，若这个数字"
            "异常地大，很可能是把 cookie 正文而不是路径写进了 "
            f"{COOKIE_ENV}）")
    return path


def parse_max_episodes(raw: str | None) -> int:
    """校验投稿上限。留空 = 默认值；0、负数、非整数一律退 1，不静默纠正。

    「静默当成 1」和「静默当成无限」都是有害的降级：前者会让人以为改了参数
    却什么都没变，后者正是用户明确要求禁止的「一次投 7 集」。
    """
    text = (raw or "").strip()
    if not text:
        return DEFAULT_MAX_EPISODES
    if not re.fullmatch(r"-?\d+", text):
        raise ConfigError(
            f"投稿上限必须是整数，收到 {text!r}（不允许静默当成 "
            f"{DEFAULT_MAX_EPISODES} 或当成无限）")
    value = int(text)
    if value < 1:
        raise ConfigError(
            f"投稿上限必须 >= 1，收到 {value}（不允许静默当成 "
            f"{DEFAULT_MAX_EPISODES} 或当成无限）")
    return value


def already_published(index: dict, slug: str, episode_id: str) -> str | None:
    """查 ``site/data/index.json``，返回该集已记录的 bilibili 稿件标识；没有
    记录或未投过返回 ``None``。

    索引里同 ``slug`` + ``id`` 才算同一条——``update_site_index.py`` 用的就是
    这个复合键。查不到这一条记录（比如索引还没被这次的站点索引合并写入过）
    按「未投过」处理，不阻塞投稿；已投过的以 ``publish.bilibili`` 非空为准。
    """
    for ep in index.get("episodes", []):
        if ep.get("slug") == slug and ep.get("id") == episode_id:
            publish = ep.get("publish") or {}
            value = publish.get("bilibili")
            return value if value else None
    return None


BVID_RE = re.compile(r"\bBV[0-9A-Za-z]{10}\b")


def extract_bvid(text: str) -> str | None:
    """从 biliup 的 stdout 里摸一个 bvid 出来。摸不到返回 ``None``——绝不编造。

    biliup 提交成功后的响应里带 bvid（B 站稿件对外可见的标识），但它打印到
    stdout 的具体格式没有稳定的公开文档保证，所以这里只做「摸得到就用，摸不
    到就诚实说摸不到」，不去反推一个假的标识出来。
    """
    m = BVID_RE.search(text)
    return m.group(0) if m else None


def record_publish_result(index_path: Path, slug: str, episode_id: str,
                          marker: str) -> bool:
    """把投稿结果写回 ``site/data/index.json`` 的 ``publish.bilibili``。

    只更新匹配 ``slug`` + ``id`` 的那一条，其余字段原样保留——不重新跑一次
    ``update_site_index.py`` 的合并逻辑，避免和「合并进站点索引并提交」那步
    的下架/排序规则打架。索引里找不到这一条（还没被合并过）时不写、返回
    ``False``，调用方据此决定是否要提醒用户顺序不对。
    """
    if not index_path.is_file():
        return False
    data = json.loads(index_path.read_text(encoding="utf-8"))
    changed = False
    for ep in data.get("episodes", []):
        if ep.get("slug") == slug and ep.get("id") == episode_id:
            ep.setdefault("publish", {})["bilibili"] = marker
            ep["status"] = "published"
            changed = True
            break
    if not changed:
        return False
    data["updated_at"] = datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    index_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return True


def episode_files(queue_path: Path, episode: dict) -> tuple:
    """定位这一集的本地成片和封面。单集在 slug 根目录，多集在 ep01/ 子目录。"""
    base = queue_path.parent
    sub = base / str(episode.get("id", ""))
    src = sub if sub.is_dir() else base
    video, cover = src / "final.mp4", src / "cover_16x9.jpg"
    if not video.is_file():
        raise ConfigError(f"找不到成片 {video}")
    if not cover.is_file():
        raise ConfigError(f"找不到封面 {cover}")
    return video, cover


def build_upload_args(episode: dict, video: Path, cover: Path,
                      cookies: Path, tid: int = DEFAULT_TID,
                      copyright_: int = DEFAULT_COPYRIGHT,
                      line: str = DEFAULT_LINE) -> list:
    """拼 ``biliup upload`` 的命令行。标签逐个 ``--tag``，顺序与 queue 一致。"""
    cmd = ["biliup", "-u", str(cookies), "upload", str(video),
           "--title", episode.get("title", ""),
           "--tid", str(tid),
           "--desc", episode.get("desc", ""),
           "--cover", str(cover),
           "--copyright", str(copyright_),
           "--line", line]
    for tag in episode.get("tags", []):
        cmd += ["--tag", tag]
    return cmd


def main(argv=None) -> int:
    p = ConfigErrorArgumentParser(
        prog="publish_bilibili.py",
        description="从 queue.json 取一集投到 B 站（预留接口，默认关闭）")
    p.add_argument("--queue", required=True, help="queue.json 路径")
    p.add_argument("--episode", required=True, help="集号，如 ep01")
    p.add_argument("--cookies", default=None,
                   help=f"biliup cookies.json 路径，默认读环境变量 {COOKIE_ENV}")
    p.add_argument("--index", default=None,
                   help="site/data/index.json 路径，用于幂等判定与写回投稿"
                        "结果。不传则跳过幂等检查（仅供本地手工调试用）")
    p.add_argument("--tid", type=int, default=DEFAULT_TID,
                   help=f"B 站分区 id（默认 {DEFAULT_TID} 财经商业）")
    p.add_argument("--copyright", type=int, default=DEFAULT_COPYRIGHT,
                   choices=(1, 2), help="1 自制 / 2 转载")
    p.add_argument("--line", default=DEFAULT_LINE,
                   help=f"biliup 上传线路（默认 {DEFAULT_LINE}）")
    p.add_argument("--dry-run", action="store_true",
                   help="只打印将要执行的命令，不真的投稿")
    args = p.parse_args(argv)

    queue_path = Path(args.queue)
    index_path = Path(args.index) if args.index else None
    try:
        if not queue_path.is_file():
            raise ConfigError(f"找不到 {queue_path}")
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        episode = find_episode(queue, args.episode)
        slug = queue.get("slug", "")

        if index_path is not None and index_path.is_file():
            index = json.loads(index_path.read_text(encoding="utf-8"))
            marker = already_published(index, slug, args.episode)
            if marker:
                print(f"[bilibili] {args.episode} 已投过（bvid={marker}），"
                      "跳过，不重复投稿", flush=True)
                return EXIT_OK

        cookies = resolve_cookies(args.cookies)
        video, cover = episode_files(queue_path, episode)
        cmd = build_upload_args(episode, video, cover, cookies, args.tid,
                                args.copyright, args.line)
    except (ConfigError, json.JSONDecodeError) as e:
        print(f"[bilibili] {e}", file=sys.stderr)
        return EXIT_CONFIG

    printable = " ".join(cmd)
    if args.dry_run:
        print(f"[bilibili] dry-run：{printable}")
        return EXIT_OK

    if shutil.which("biliup") is None:
        print("[bilibili] 没装 biliup：pip install biliup", file=sys.stderr)
        return EXIT_CONFIG

    print(f"[bilibili] {printable}", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    # biliup 自己的输出照常回显——里面不会有 cookie 内容，只有提交结果——
    # 但我们要先摸一遍 bvid 再打印，摸的动作本身不能影响回显顺序。
    if r.stdout:
        print(r.stdout, end="" if r.stdout.endswith("\n") else "\n", flush=True)
    if r.stderr:
        print(r.stderr, end="" if r.stderr.endswith("\n") else "\n",
              file=sys.stderr, flush=True)
    if r.returncode != 0:
        print(f"[bilibili] biliup 退出码 {r.returncode}", file=sys.stderr)
        return EXIT_API

    bvid = extract_bvid(r.stdout or "")
    marker = bvid or f"published-no-bvid-{datetime.now(timezone.utc).isoformat()}"
    if bvid is None:
        print("[bilibili] ⚠️ biliup 退出码 0，但没能从输出里摸到 bvid——"
              "已按「已投稿」记录，但幂等标记里不含真实稿件号，人工核实一下",
              file=sys.stderr, flush=True)
    if index_path is not None:
        wrote = record_publish_result(index_path, slug, args.episode, marker)
        if not wrote:
            print(f"[bilibili] ⚠️ {index_path} 里没有 {slug}/{args.episode} "
                  "这一条记录，投稿结果没能写回索引——请确认「合并进站点索引"
                  "并提交」那步已经跑在这一步之前", file=sys.stderr, flush=True)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
