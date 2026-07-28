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

退出码：0 成功 / 1 配置错误（缺 cookie、缺产物、集号不存在）/ 3 biliup 失败
"""

import json
import os
import shutil
import subprocess
import sys
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
COOKIE_ENV = "BILIBILI_COOKIES"


class ConfigError(Exception):
    """投稿前就能发现的问题：缺 cookie、缺文件、集号不存在。"""


def find_episode(queue: dict, episode_id: str) -> dict:
    for ep in queue.get("episodes", []):
        if ep.get("id") == episode_id:
            return ep
    known = [ep.get("id") for ep in queue.get("episodes", [])]
    raise ConfigError(f"queue.json 里没有 {episode_id}，现有 {known}")


def resolve_cookies(explicit: str | None = None) -> Path:
    """定位 cookie 文件。没有就报错 —— 静默跳过会让人以为片子发出去了。"""
    raw = (explicit or os.environ.get(COOKIE_ENV) or "").strip()
    if not raw:
        raise ConfigError(
            f"缺少 B 站 cookie：设置 {COOKIE_ENV} 或传 --cookies 指向 "
            f"biliup 的 cookies.json。没有 cookie 就投不了稿，这里不静默跳过")
    path = Path(raw)
    if not path.is_file():
        raise ConfigError(f"cookie 文件不存在：{path}")
    return path


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
    try:
        if not queue_path.is_file():
            raise ConfigError(f"找不到 {queue_path}")
        queue = json.loads(queue_path.read_text(encoding="utf-8"))
        episode = find_episode(queue, args.episode)
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
    r = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"[bilibili] biliup 退出码 {r.returncode}", file=sys.stderr)
        return EXIT_API
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
