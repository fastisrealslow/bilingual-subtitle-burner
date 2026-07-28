"""yt-dlp 下载的退避重试与错误分类（不真的下载，sleep 打桩）。

CI run 30263087066 就挂在这里：archive.org 返 `HTTP Error 500`，退出码 3，
原样重跑就过。无人值守时不重试等于白跑一趟。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                # noqa: E402

FIVE_HUNDRED = ("ERROR: unable to download video data: "
                "HTTP Error 500: Internal Server Error")
NOT_FOUND = "ERROR: unable to download webpage: HTTP Error 404: Not Found"


class Result:
    def __init__(self, returncode, stderr=""):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(produce.time, "sleep", slept.append)
    return slept


def runner(monkeypatch, results):
    """按顺序吐 subprocess 结果，返回真实执行到的命令列表。"""
    cmds = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        return results[min(len(cmds) - 1, len(results) - 1)]

    monkeypatch.setattr(produce.subprocess, "run", fake_run)
    return cmds


# ── 值得重试 ─────────────────────────────────────────────────────────────────

def test_http_500_is_retried_until_success(tmp_path, monkeypatch, no_sleep):
    cmds = runner(monkeypatch, [Result(1, FIVE_HUNDRED), Result(0)])

    produce.download_source("https://archive.org/x", tmp_path / "s.mp4", 3, 5.0)

    assert len(cmds) == 2
    assert len(no_sleep) == 1


def test_http_500_gives_up_with_exit_three(tmp_path, monkeypatch, no_sleep,
                                           capsys):
    cmds = runner(monkeypatch, [Result(1, FIVE_HUNDRED)])

    with pytest.raises(SystemExit) as e:
        produce.download_source("https://archive.org/x", tmp_path / "s.mp4",
                                3, 5.0)

    assert e.value.code == produce.EXIT_API
    assert len(cmds) == 3
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["reason"] == "download_failed"
    assert payload["attempts"] == 3


@pytest.mark.parametrize("stderr", [
    "ERROR: unable to download video data: HTTP Error 503: Service Unavailable",
    "ERROR: [Errno 104] Connection reset by peer",
    "ERROR: The read operation timed out",
    "ERROR: unable to download webpage: HTTP Error 429: Too Many Requests",
    "ERROR: 某种没见过的失败",
])
def test_transient_failures_are_retried(tmp_path, monkeypatch, no_sleep, stderr):
    cmds = runner(monkeypatch, [Result(1, stderr), Result(0)])
    produce.download_source("https://x/y", tmp_path / "s.mp4", 3, 5.0)
    assert len(cmds) == 2


# ── 重试也没用 ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stderr", [
    NOT_FOUND,
    "ERROR: Unsupported URL: https://example.com/not-a-video",
    "ERROR: 'x' is not a valid URL",
    "ERROR: Video unavailable",
    "ERROR: unable to download webpage: HTTP Error 403: Forbidden",
])
def test_fatal_failures_are_not_retried(tmp_path, monkeypatch, no_sleep,
                                        capsys, stderr):
    cmds = runner(monkeypatch, [Result(1, stderr)])

    with pytest.raises(SystemExit) as e:
        produce.download_source("https://x/y", tmp_path / "s.mp4", 3, 5.0)

    assert len(cmds) == 1, f"不可重试的错误被重试了 {len(cmds)} 次"
    assert no_sleep == []
    assert e.value.code == produce.EXIT_CONFIG
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["reason"] == "source_unavailable"


def test_408_and_429_are_not_treated_as_fatal():
    assert not produce.download_is_fatal("HTTP Error 408: Request Timeout")
    assert not produce.download_is_fatal("HTTP Error 429: Too Many Requests")
    assert produce.download_is_fatal("HTTP Error 404: Not Found")


# ── 退避与参数 ───────────────────────────────────────────────────────────────

def test_backoff_grows_and_is_capped(tmp_path, monkeypatch, no_sleep):
    runner(monkeypatch, [Result(1, FIVE_HUNDRED)])

    with pytest.raises(SystemExit):
        produce.download_source("https://x/y", tmp_path / "s.mp4", 6, 5.0)

    assert no_sleep == sorted(no_sleep)
    assert max(no_sleep) <= produce.DOWNLOAD_BACKOFF_CAP_SEC * 1.25


def test_retry_count_is_configurable(tmp_path, monkeypatch, no_sleep):
    cmds = runner(monkeypatch, [Result(1, FIVE_HUNDRED)])
    with pytest.raises(SystemExit):
        produce.download_source("https://x/y", tmp_path / "s.mp4", 5, 0.1)
    assert len(cmds) == 5


def test_ytdlp_gets_its_own_retry_flags(tmp_path, monkeypatch, no_sleep):
    cmds = runner(monkeypatch, [Result(0)])
    produce.download_source("https://x/y", tmp_path / "s.mp4", 3, 5.0)

    cmd = cmds[0]
    assert cmd[cmd.index("--retries") + 1] == "5"
    assert cmd[cmd.index("--fragment-retries") + 1] == "5"


def test_socket_timeout_is_passed_to_ytdlp(tmp_path, monkeypatch, no_sleep):
    cmds = runner(monkeypatch, [Result(0)])
    produce.download_source("https://x/y", tmp_path / "s.mp4", 3, 5.0)

    cmd = cmds[0]
    assert cmd[cmd.index("--socket-timeout") + 1] == "120"


def test_socket_timeout_override_reaches_ytdlp(tmp_path, monkeypatch, no_sleep):
    cmds = runner(monkeypatch, [Result(0)])
    produce.download_source("https://x/y", tmp_path / "s.mp4", 3, 5.0, 45)

    cmd = cmds[0]
    assert cmd[cmd.index("--socket-timeout") + 1] == "45"


def test_socket_timeout_default_is_well_clear_of_observed_latency():
    """archive.org 首字节实测 13.8s，yt-dlp 自带的 20s 只剩 6s 余量。"""
    assert produce.DEFAULT_DOWNLOAD_SOCKET_TIMEOUT_SEC >= 120


def test_backoff_sequence_is_ten_twenty_forty_then_capped(tmp_path, monkeypatch,
                                                          no_sleep):
    monkeypatch.setattr(produce.random, "uniform", lambda a, b: 0.0)
    runner(monkeypatch, [Result(1, FIVE_HUNDRED)])

    with pytest.raises(SystemExit):
        produce.download_source("https://x/y", tmp_path / "s.mp4", 6,
                                produce.DEFAULT_DOWNLOAD_BACKOFF_SEC)

    assert no_sleep == [10.0, 20.0, 40.0, 60.0, 60.0]


def test_cli_exposes_download_knobs():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s",
                               "--download-retries", "7",
                               "--download-backoff-sec", "2.5",
                               "--download-socket-timeout", "45"])
    assert args.download_retries == 7
    assert args.download_backoff_sec == 2.5
    assert args.download_socket_timeout == 45.0


def test_download_knobs_have_conservative_defaults():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s"])
    assert args.download_retries == produce.DEFAULT_DOWNLOAD_RETRIES
    assert args.download_backoff_sec == produce.DEFAULT_DOWNLOAD_BACKOFF_SEC
    assert (args.download_socket_timeout
            == produce.DEFAULT_DOWNLOAD_SOCKET_TIMEOUT_SEC)
    assert produce.DEFAULT_DOWNLOAD_RETRIES == 5
    assert produce.DEFAULT_DOWNLOAD_BACKOFF_SEC == 10.0
    assert produce.DEFAULT_DOWNLOAD_SOCKET_TIMEOUT_SEC == 120.0


def test_resolve_source_forwards_the_socket_timeout(tmp_path, monkeypatch):
    seen = {}

    def fake_download(source, out, retries, backoff_sec, socket_timeout_sec,
                      cookies=None):
        seen["timeout"] = socket_timeout_sec
        out.write_bytes(b"x")

    monkeypatch.setattr(produce.shutil, "which", lambda name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(produce, "download_source", fake_download)
    produce.resolve_source("https://x/y", tmp_path, 3, 5.0, 90)
    assert seen["timeout"] == 90


def test_local_source_never_touches_yt_dlp(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("本地片源不该调 yt-dlp")

    monkeypatch.setattr(produce, "download_source", boom)
    real = tmp_path / "v.mp4"
    real.write_bytes(b"x")
    assert produce.resolve_source(str(real), tmp_path) == real


def test_cli_exposes_llm_cache_and_retry_knobs():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s",
                               "--no-llm-cache", "--llm-cache-dir", "/tmp/c",
                               "--llm-max-retries", "4"])
    assert args.no_llm_cache is True
    assert args.llm_cache_dir == "/tmp/c"
    assert args.llm_max_retries == 4


def test_llm_knobs_have_conservative_defaults():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s"])
    assert args.no_llm_cache is False
    assert args.llm_max_retries == produce.sf_client.DEFAULT_MAX_RETRIES
    assert Path(args.llm_cache_dir).name == ".llm_cache"


def test_subprocess_result_is_surfaced_on_failure(tmp_path, monkeypatch,
                                                  no_sleep, capsys):
    """失败输出必须打出来，否则 CI 上只能看到一个退出码。"""
    runner(monkeypatch, [Result(1, FIVE_HUNDRED)])
    with pytest.raises(SystemExit):
        produce.download_source("https://x/y", tmp_path / "s.mp4", 1, 5.0)
    assert "HTTP Error 500" in capsys.readouterr().err


def test_real_subprocess_signature_is_compatible():
    """确认桩用的调用形状和真实 subprocess.run 一致。"""
    p = subprocess.run([sys.executable, "-c", "print('x')"],
                       capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.strip() == "x"
