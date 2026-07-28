#!/usr/bin/env python3
"""命令行用法错误的统一归类：退 ``EXIT_CONFIG``，不沿用 argparse 默认的退 2。

argparse 把用法错误退 2，而 2 在本仓库是有专门含义的码 —— 出片链路里是
「内容质量不达标，拒绝硬出」，``prune_releases.py`` 里是「Release 都删了但有
tag 没清干净，需要人工收尾」。两种情况照着各自的退出码表读出来都是错的结论，
而真实原因只是少打了一个字母。

这里放的是**纯文本**那一版：打 argparse 原本的 usage + ``prog: error: ...``。
``scripts/`` 下按退出码约定办事、但不打结构化 JSON 的入口共用它。

``produce.py`` / ``scripts/highlight.py`` / ``steps/step7_cover.py`` 各自留着
一份同名类，因为它们的 ``error()`` 要把失败写成各自既有的结构化 JSON
（``{"stage": "config", "reason": "invalid_arguments", ...}``）再退出，方便 CI
grep。把那三份也折进来就得给输出方式加参数，不再是同一个东西，所以不折。
"""

import argparse
import sys

EXIT_CONFIG = 1


class ConfigErrorArgumentParser(argparse.ArgumentParser):
    """把 argparse 的用法错误归到 ``EXIT_CONFIG``，不沿用它默认的退 2。

    ``-h/--help`` 走 argparse 自己的 ``exit()``，不经过这里，仍退 0。
    """

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr, flush=True)
        sys.exit(EXIT_CONFIG)
