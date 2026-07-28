"""命令行参数错误必须退 1，不许和「内容质量不达标」的退 2 撞号。

argparse 默认把用法错误退 2。本仓库的 2 是 ``EXIT_QUALITY`` 的专用码，
``docs/pipeline.md`` 的退出码表写得很硬：退 2「不是 bug，是拒绝硬出」
「重试没有意义」，该换片源。于是敲错一个 flag 也退 2 —— 照文档读出来的结论
是「这条片源不行，换一个」，而真实原因只是少打了一个字母。

更自相矛盾的是同一个参数的两种错法给两个码：``--episodes 2.5`` 由 argparse
拦下退 2，``--episodes 0`` 由 ``produce.main`` 自己拦下退 1。

带退出码约定的入口都用 ``ConfigErrorArgumentParser`` 覆盖了 argparse 的
``error()``：``produce.py`` / ``scripts/highlight.py`` /
``scripts/publish_bilibili.py`` / ``steps/step7_cover.py`` /
``scripts/prune_releases.py``。

``prune_releases.py`` 撞的是另一个 2 —— 它的退出码表里 2 是「Release 都删了但有
tag 没清干净，需要人工收尾」。敲错 flag 退 2 会被读成「去仓库里收拾残留的空
ref」，而其实一个删除请求都没发出去。它自己那三档语义由
``test_prune_releases.py`` 守着，这里只核跨入口的一致性。

本文件锁三件事：

1. 用法错误退 ``EXIT_CONFIG``，且 stderr 里看得出**是哪个参数**错了 ——
   只核退出码的话，一个把错误信息吞掉的实现照样能跑绿。
2. ``-h/--help`` 这类正常退出仍退 0，别被一起改掉。
3. 内容质量拒绝仍退 ``EXIT_QUALITY``，没被这次改动误伤。

阈值常量一概不在这里核（``test_episode_gate.py::test_thresholds_are_unchanged``
负责），本文件只管退出码的归类。
"""

import json
import sys

import pytest

import highlight as HL
import produce
import prune_releases as PR
import publish_bilibili as PB
import step7_cover as COVER


# ── 退出码常量：撞号的前提是这俩不相等 ──────────────────────────────────────

def test_config_and_quality_codes_are_distinct():
    """整个改动的立足点：1 和 2 是两件事。相等的话下面所有断言都失去意义。"""
    assert produce.EXIT_CONFIG == 1
    assert produce.EXIT_QUALITY == 2
    assert produce.EXIT_CONFIG != produce.EXIT_QUALITY


@pytest.mark.parametrize("mod", [produce, HL, PB, COVER, PR])
def test_every_entry_module_agrees_on_config_code(mod):
    """带退出码约定的入口对「配置错 = 1」的理解必须一致，不能各写各的。"""
    assert mod.EXIT_CONFIG == 1


# ── 1. produce.py：用法错误退 1 ────────────────────────────────────────────
# 每个用例带上「stderr 里必须出现的字样」，把「哪个参数错了」一起钉死。

USAGE_ERRORS = [
    pytest.param(["--source", "v.mp4", "--slug", "s", "--epsiodes", "2"],
                 "--epsiodes", id="拼错-flag"),
    pytest.param(["--source", "v.mp4", "--slug", "s", "--episodes", "2.5"],
                 "--episodes", id="episodes-非整数"),
    pytest.param(["--source", "v.mp4", "--slug", "s",
                  "--cover-time-sec", "abc"],
                 "--cover-time-sec", id="cover-time-sec-非数字"),
    pytest.param(["--slug", "s"], "--source", id="缺必需参数"),
    pytest.param(["--source", "v.mp4", "--slug", "s",
                  "--translator", "gpt-5"],
                 "--translator", id="choices-取值不在表内"),
    pytest.param(["--source", "v.mp4", "--slug", "s",
                  "--sub-margin-v", "上面一点"],
                 "--sub-margin-v", id="自定义-type-抛-ArgumentTypeError"),
]


@pytest.mark.parametrize("argv,needle", USAGE_ERRORS)
def test_usage_error_exits_config(argv, needle, capsys):
    with pytest.raises(SystemExit) as e:
        produce.parse_args(argv)
    assert e.value.code == produce.EXIT_CONFIG
    # 退出码对了还不够：得看得出是哪个参数惹的祸，否则等于静默失败
    assert needle in capsys.readouterr().err


@pytest.mark.parametrize("argv,needle", USAGE_ERRORS)
def test_usage_error_is_not_mistaken_for_quality_rejection(argv, needle,
                                                           capsys):
    """反向断言：退出码绝不能是 2。

    上面那条只说「必须等于 1」，这条单独把 2 点名 —— 撞号是这次要修的病，
    要有一条测试的失败信息直接指向它。
    """
    with pytest.raises(SystemExit) as e:
        produce.parse_args(argv)
    capsys.readouterr()
    assert e.value.code != produce.EXIT_QUALITY


def test_usage_error_emits_structured_reason(capsys):
    """走的是既有的 ``die`` 路径，所以 stderr 末行是可 grep 的结构化 JSON。"""
    with pytest.raises(SystemExit) as e:
        produce.parse_args(["--source", "v.mp4", "--slug", "s",
                            "--episodes", "2.5"])
    assert e.value.code == produce.EXIT_CONFIG

    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["stage"] == "config"
    assert payload["reason"] == "invalid_arguments"
    assert "--episodes" in payload["detail"]


def test_both_ways_of_getting_episodes_wrong_give_the_same_code(capsys,
                                                                monkeypatch,
                                                                tmp_path):
    """同一个参数的两种错法必须给同一个码 —— 这条就是「自相矛盾」的守卫。

    ``2.5`` 走 argparse，``0`` 走 ``produce.main`` 自己的校验。修好之前一个退
    2 一个退 1。
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr(produce, "resolve_source",
                        lambda *a, **k: pytest.fail("闸门没拦住：不该走到取源"))

    codes = []
    for raw in ("2.5", "0"):
        with pytest.raises(SystemExit) as e:
            produce.main(["--source", "v.mp4", "--slug", "s",
                          "--episodes", raw])
        codes.append(e.value.code)
    capsys.readouterr()

    assert codes == [produce.EXIT_CONFIG, produce.EXIT_CONFIG]


# ── 2. 正常退出仍退 0 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_help_exits_ok(flag, capsys):
    """``-h`` 不是错误。它走 argparse 自己的 ``exit()``，不该被覆盖顺手改掉。"""
    with pytest.raises(SystemExit) as e:
        produce.parse_args([flag])
    assert e.value.code == produce.EXIT_OK
    assert "--episodes" in capsys.readouterr().out


def test_help_wins_over_missing_required_args(capsys):
    """``-h`` 优先于「缺 --source」，光给 ``-h`` 也得退 0 而不是报缺参数。"""
    with pytest.raises(SystemExit) as e:
        produce.parse_args(["-h"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "error" not in out


def test_valid_args_still_parse():
    """别把正常解析一起改坏：合法参数不许抛。"""
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s",
                               "--episodes", "3"])
    assert args.episodes == 3 and args.slug == "s"


# ── 3. 内容质量拒绝仍退 2（没被误伤）──────────────────────────────────────

def quote(rank, dur):
    return {"rank": rank, "score": 9.0, "clip_start_sec": 0.0,
            "clip_end_sec": float(dur), "clip_duration_sec": float(dur)}


def test_quality_rejection_still_exits_two(capsys):
    """金句闸门的拒绝码不许被这次改动带偏。"""
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds([quote(i, 29) for i in range(1, 4)],
                                    want=3)
    assert e.value.code == HL.EXIT_QUALITY
    assert e.value.code != HL.EXIT_CONFIG

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "insufficient_duration"


def test_cover_rejection_still_exits_two(capsys):
    """封面闸门同理。"""
    with pytest.raises(SystemExit) as e:
        COVER.reject_cover("no_frame_passed_vlm", candidates=8)
    assert e.value.code == COVER.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["stage"] == "cover"
    assert payload["reason"] == "no_frame_passed_vlm"


# ── 4. 其余三个入口：同样的撞号，同样的归类 ────────────────────────────────

def run_cli(mod, argv, monkeypatch):
    """跑一个入口的参数解析，返回退出码。

    ``highlight`` 和 ``step7_cover`` 的 ``main()`` 不收 argv，直接读
    ``sys.argv``，所以这里改 ``sys.argv`` 而不是传参。参数解析在任何外部调用
    之前，所以到不了 API key 检查那一步（``prune_releases`` 同理，到不了 gh）。
    """
    if mod in (PB, PR):
        return mod.main(argv)
    monkeypatch.setattr(sys, "argv", [mod.__name__ + ".py"] + argv)
    return mod.main()


@pytest.mark.parametrize("mod,needle", [
    pytest.param(HL, "--srt", id="highlight"),
    pytest.param(PB, "--queue", id="publish_bilibili"),
    pytest.param(COVER, "--manifest", id="step7_cover"),
    pytest.param(PR, "--index", id="prune_releases"),
])
def test_other_entrypoints_exit_config_on_usage_error(mod, needle,
                                                      monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        run_cli(mod, ["--badflag"], monkeypatch)
    assert e.value.code == mod.EXIT_CONFIG
    assert e.value.code != 2
    assert needle in capsys.readouterr().err


@pytest.mark.parametrize("mod", [HL, PB, COVER, PR])
def test_other_entrypoints_help_exits_ok(mod, monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        run_cli(mod, ["-h"], monkeypatch)
    assert e.value.code == 0
    capsys.readouterr()
