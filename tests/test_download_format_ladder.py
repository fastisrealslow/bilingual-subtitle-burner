"""format 降级链、360p 保底闸门，以及取源失败时的 --list-formats 诊断。

CI run 30347884807 挂在 1/9 input，退 3。cookies 完全正常（日志里是「已写入
80 条记录、20 个域名」），真正的错是 yt-dlp 解不开 n 签名挑战：

    WARNING: [youtube] NVD-m9seDe4: n challenge solving failed: Some formats
    may be missing. Ensure you have a supported JavaScript runtime and
    challenge solver script distribution installed.
    ERROR: [youtube] NVD-m9seDe4: Requested format is not available.

根因在环境（runner 上没有 EJS 运行时，见 produce.yml），但取源这一侧也有两个
问题：format 表达式是硬的，匹配不上就直接死；失败时只吐一句「格式不可用」，
要再开一轮 CI 才知道到底有哪些格式。这个文件守着修完之后的行为。
"""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                # noqa: E402

PRODUCE_YML = ROOT / ".github" / "workflows" / "produce.yml"

# 下面的 height_ok 夹具会把 produce.probe_height 打桩掉，这里先把真身留一份，
# 供直接测 probe_height 本身的用例使用。
REAL_PROBE_HEIGHT = produce.probe_height

# 真实的 n 挑战失败长什么样 —— 先一句 WARNING，再一句 ERROR
N_CHALLENGE = (
    "WARNING: [youtube] NVD-m9seDe4: n challenge solving failed: Some formats "
    "may be missing. Ensure you have a supported JavaScript runtime and "
    "challenge solver script distribution installed.")
FORMAT_MISSING = (
    "ERROR: [youtube] NVD-m9seDe4: Requested format is not available. "
    "Use --list-formats for a list of available formats")
NO_FORMATS = "ERROR: [generic] x: No video formats found!; please report this"
FIVE_HUNDRED = ("ERROR: unable to download video data: "
                "HTTP Error 500: Internal Server Error")
BOT_WALL = "ERROR: [youtube] x: Sign in to confirm you're not a bot."
NOT_FOUND = "ERROR: unable to download webpage: HTTP Error 404: Not Found"

FORMAT_TABLE = """\
[info] Available formats for NVD-m9seDe4:
ID  EXT   RESOLUTION FPS │  FILESIZE   TBR PROTO │ VCODEC       ACODEC
139 m4a   audio only     │   1.90MiB   49k https │ audio only   mp4a.40.5
160 mp4   256x144     15 │   1.62MiB   42k https │ avc1.4d400c  video only
18  mp4   640x360     30 │  15.31MiB  396k https │ avc1.42001E  mp4a.40.2
"""


class Result:
    def __init__(self, returncode, stderr="", stdout=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(produce.time, "sleep", slept.append)
    return slept


@pytest.fixture(autouse=True)
def height_ok(monkeypatch):
    """默认让保底闸门放行；专门测保底的用例自己再覆盖一遍。"""
    monkeypatch.setattr(produce, "probe_height", lambda p: 480)


def runner(monkeypatch, handler):
    """把 ``subprocess.run`` 换成 handler(cmd) -> Result，返回执行到的命令。"""
    cmds = []

    def fake_run(cmd, **kw):
        cmd = [str(c) for c in cmd]
        cmds.append(cmd)
        return handler(cmd)

    monkeypatch.setattr(produce.subprocess, "run", fake_run)
    return cmds


def is_list_formats(cmd) -> bool:
    return "--list-formats" in cmd


def fmt_of(cmd):
    return cmd[cmd.index("-f") + 1] if "-f" in cmd else None


def formats_used(cmds):
    return [fmt_of(c) for c in cmds if not is_list_formats(c)]


def always(result, *, table=FORMAT_TABLE):
    def handler(cmd):
        return Result(0, stdout=table) if is_list_formats(cmd) else result
    return handler


def succeed_at_level(level, *, other=FORMAT_MISSING):
    """第 ``level`` 级成功，其余各级报 ``other``。"""
    target = produce.FORMAT_LADDER[level - 1][0]

    def handler(cmd):
        if is_list_formats(cmd):
            return Result(0, stdout=FORMAT_TABLE)
        return Result(0) if fmt_of(cmd) == target else Result(1, other)
    return handler


def download(tmp_path, **kw):
    kw.setdefault("retries", 3)
    kw.setdefault("backoff_sec", 1.0)
    return produce.download_source(
        kw.pop("source", "https://youtu.be/NVD-m9seDe4"),
        tmp_path / "s.mp4", kw.pop("retries"), kw.pop("backoff_sec"), **kw)


def last_payload(capsys):
    return json.loads(capsys.readouterr().err.strip().splitlines()[-1])


# ── 1. 降级链：每一级都要真的能用上 ────────────────────────────────────────

def test_ladder_has_more_than_one_level():
    """只有一级就等于没有降级链。"""
    assert len(produce.FORMAT_LADDER) >= 2


# 降级链是契约，这里写死一遍。下面那些用例都拿 produce.FORMAT_LADDER 自己当参数，
# 删掉一级它们会跟着缩短、照样全绿（变异验证里 M6/M7 就是这么漏过去的），所以必须
# 有一处独立的期望值。改降级链时要显式改这里。
EXPECTED_LADDER = (
    "bv*[height<=720]+ba/b[height<=720]",
    "bv*[height<=1080]+ba/b[height<=1080]",
    "bv*+ba",
    "b",
)


def test_the_ladder_is_exactly_this_chain():
    assert tuple(f for f, _ in produce.FORMAT_LADDER) == EXPECTED_LADDER


def test_every_level_explains_itself():
    """每级都要有人读的说明，否则降级日志打出来也看不懂。"""
    for fmt, why in produce.FORMAT_LADDER:
        assert why.strip(), f"{fmt} 这一级没有说明"


def test_the_ladder_only_ever_relaxes():
    """高度上限只能一级级放宽，且最后一级不许再带上限 —— 否则兜不住底。"""
    caps = []
    for fmt, _ in produce.FORMAT_LADDER:
        found = re.findall(r"height<=(\d+)", fmt)
        caps.append(max(int(x) for x in found) if found else None)
    bounded = [c for c in caps if c is not None]
    assert bounded == sorted(bounded), f"降级链的高度上限不是单调放宽的：{caps}"
    assert caps[-1] is None, "最后一级还带着高度上限，等于没有兜底"


def test_first_level_still_prefers_720p_like_before(tmp_path, monkeypatch):
    """首选一级不许被降级链改坏：仍然是 ≤720p，成片清晰度不变。"""
    cmds = runner(monkeypatch, always(Result(0)))
    download(tmp_path)
    assert formats_used(cmds) == [produce.FORMAT_LADDER[0][0]]
    assert "height<=720" in produce.FORMAT_LADDER[0][0]


@pytest.mark.parametrize("level", range(1, len(produce.FORMAT_LADDER) + 1))
def test_each_level_is_reachable_and_tried_in_order(tmp_path, monkeypatch,
                                                    level):
    """第 N 级能成时，前 N-1 级都被试过、且顺序就是 FORMAT_LADDER 的顺序。"""
    cmds = runner(monkeypatch, succeed_at_level(level))
    download(tmp_path)
    assert formats_used(cmds) == [f for f, _ in produce.FORMAT_LADDER[:level]]


def test_running_out_of_levels_exits_three(tmp_path, monkeypatch, capsys):
    cmds = runner(monkeypatch, always(Result(1, FORMAT_MISSING)))
    with pytest.raises(SystemExit) as e:
        download(tmp_path)
    assert e.value.code == produce.EXIT_API
    assert formats_used(cmds) == [f for f, _ in produce.FORMAT_LADDER]
    payload = last_payload(capsys)
    assert payload["reason"] == "download_failed"
    assert payload["formats_tried"] == [f for f, _ in produce.FORMAT_LADDER]


def test_formats_tried_reports_only_the_levels_actually_attempted(
        tmp_path, monkeypatch, capsys):
    """网络问题会停在第一级，报成「四级都试过了」会把排查方向带偏。"""
    runner(monkeypatch, always(Result(1, FIVE_HUNDRED)))
    with pytest.raises(SystemExit):
        download(tmp_path, retries=2)
    assert last_payload(capsys)["formats_tried"] == [produce.FORMAT_LADDER[0][0]]


def test_the_real_n_challenge_output_walks_the_whole_ladder(tmp_path,
                                                           monkeypatch):
    """就是线上那两句话，必须被认成「格式不可用」而不是当成偶发错误重试。"""
    cmds = runner(monkeypatch,
                  always(Result(1, N_CHALLENGE + "\n" + FORMAT_MISSING)))
    with pytest.raises(SystemExit):
        download(tmp_path)
    assert formats_used(cmds) == [f for f, _ in produce.FORMAT_LADDER]


@pytest.mark.parametrize("output", [FORMAT_MISSING, NO_FORMATS])
def test_format_unavailable_wordings_are_recognized(output):
    assert produce.format_is_unavailable(output)


@pytest.mark.parametrize("output", [FIVE_HUNDRED, BOT_WALL, NOT_FOUND,
                                    "ERROR: The read operation timed out"])
def test_other_failures_are_not_read_as_format_unavailable(output):
    assert not produce.format_is_unavailable(output)


# ── 2. 降级只在「格式不可用」时发生 ────────────────────────────────────────

def test_format_unavailable_does_not_burn_retries(tmp_path, monkeypatch,
                                                  no_sleep):
    """同一条表达式再问一遍不会多出格式来，所以不该在这一级上退避重试。"""
    cmds = runner(monkeypatch, always(Result(1, FORMAT_MISSING)))
    with pytest.raises(SystemExit):
        download(tmp_path, retries=5)
    assert len(formats_used(cmds)) == len(produce.FORMAT_LADDER)
    assert no_sleep == []


def test_transient_failure_retries_the_same_level_instead_of_degrading(
        tmp_path, monkeypatch, no_sleep):
    """500 是网络问题，换 format 表达式没用还要多花好几倍时间。"""
    cmds = runner(monkeypatch, always(Result(1, FIVE_HUNDRED)))
    with pytest.raises(SystemExit):
        download(tmp_path, retries=3)
    used = formats_used(cmds)
    assert used == [produce.FORMAT_LADDER[0][0]] * 3, \
        "偶发失败不该触发 format 降级"
    assert len(no_sleep) == 2


def test_login_wall_never_degrades(tmp_path, monkeypatch, capsys):
    """登录墙换 format 一样没用，且要退 1 让人去换凭据。"""
    cmds = runner(monkeypatch, always(Result(1, BOT_WALL)))
    with pytest.raises(SystemExit) as e:
        download(tmp_path, cookies=tmp_path / "c.txt")
    assert e.value.code == produce.EXIT_CONFIG
    assert len(formats_used(cmds)) == 1
    assert last_payload(capsys)["reason"] == "youtube_credentials_expired"


def test_missing_source_never_degrades(tmp_path, monkeypatch, capsys):
    cmds = runner(monkeypatch, always(Result(1, NOT_FOUND)))
    with pytest.raises(SystemExit) as e:
        download(tmp_path)
    assert e.value.code == produce.EXIT_CONFIG
    assert len(formats_used(cmds)) == 1
    assert last_payload(capsys)["reason"] == "source_unavailable"


# ── 3. 降级必须打日志 ──────────────────────────────────────────────────────

def test_every_degradation_is_logged_with_the_level_and_the_reason(
        tmp_path, monkeypatch, capsys):
    """静默降级是明令禁止的：每一级都要说清用了哪一级、上一级为什么没中。"""
    runner(monkeypatch, succeed_at_level(3))
    download(tmp_path)
    out = capsys.readouterr().out

    for level in (2, 3):
        fmt = produce.FORMAT_LADDER[level - 1][0]
        prev = produce.FORMAT_LADDER[level - 2][0]
        assert f"{level}/{len(produce.FORMAT_LADDER)}" in out
        assert fmt in out, f"第 {level} 级的表达式没进日志"
        assert prev in out, f"没说明上一级 {prev} 为什么没中"
    assert "没有匹配到可用格式" in out


def test_the_level_that_worked_is_named_in_the_success_log(tmp_path,
                                                           monkeypatch,
                                                           capsys):
    runner(monkeypatch, succeed_at_level(2))
    download(tmp_path)
    out = capsys.readouterr().out
    assert "取源完成" in out and "480p" in out
    assert produce.FORMAT_LADDER[1][0] in out


# ── 4. 保底：低于 360p 一律退 3 ────────────────────────────────────────────

def test_min_source_height_is_360():
    """已交付的芒格两集是 854x480，480p 够用，360p 是底线。"""
    assert produce.MIN_SOURCE_HEIGHT == 360


@pytest.mark.parametrize("height", [144, 240, 359])
def test_below_the_floor_exits_three(tmp_path, monkeypatch, capsys, height):
    monkeypatch.setattr(produce, "probe_height", lambda p: height)
    runner(monkeypatch, always(Result(0)))

    with pytest.raises(SystemExit) as e:
        download(tmp_path)

    assert e.value.code == produce.EXIT_API, "取源质量不够属于外部依赖失败，退 3"
    payload = last_payload(capsys)
    assert payload["reason"] == "source_resolution_too_low"
    assert payload["height"] == height
    assert payload["min_height"] == 360


@pytest.mark.parametrize("height", [360, 480, 720, 1080])
def test_at_or_above_the_floor_passes(tmp_path, monkeypatch, height):
    """360 本身是通过的 —— 阈值是「低于」而不是「不高于」。"""
    monkeypatch.setattr(produce, "probe_height", lambda p: height)
    runner(monkeypatch, always(Result(0)))
    download(tmp_path)


@pytest.mark.parametrize("level", range(1, len(produce.FORMAT_LADDER) + 1))
def test_no_ladder_level_can_bypass_the_floor(tmp_path, monkeypatch, capsys,
                                              level):
    """降级链最后一级最容易给出 144p，保底必须照样卡住它。"""
    monkeypatch.setattr(produce, "probe_height", lambda p: 144)
    runner(monkeypatch, succeed_at_level(level))

    with pytest.raises(SystemExit) as e:
        download(tmp_path)

    assert e.value.code == produce.EXIT_API
    assert last_payload(capsys)["reason"] == "source_resolution_too_low"


def test_unreadable_height_is_a_failure_not_a_pass(tmp_path, monkeypatch,
                                                   capsys):
    """验不了清晰度就不许往下走 —— 否则保底闸门可以被一个坏文件绕过去。"""
    monkeypatch.setattr(produce, "probe_height", lambda p: 0)
    runner(monkeypatch, always(Result(0)))

    with pytest.raises(SystemExit) as e:
        download(tmp_path)

    assert e.value.code == produce.EXIT_API
    assert last_payload(capsys)["reason"] == "source_probe_failed"


def test_probe_height_reads_the_first_video_stream(tmp_path, monkeypatch):
    """保底判的是实际文件，不是 yt-dlp 报的 format 元数据。"""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = [str(c) for c in cmd]
        return Result(0, stdout="480\n")

    monkeypatch.setattr(produce.subprocess, "run", fake_run)

    assert REAL_PROBE_HEIGHT(tmp_path / "s.mp4") == 480
    assert seen["cmd"][0] == "ffprobe"
    assert "stream=height" in seen["cmd"]
    assert "v:0" in seen["cmd"]


@pytest.mark.parametrize("stdout", ["", "N/A\n", "\n"])
def test_probe_height_returns_zero_when_ffprobe_says_nothing(tmp_path,
                                                             monkeypatch,
                                                             stdout):
    monkeypatch.setattr(produce.subprocess, "run",
                        lambda *a, **k: Result(1, stdout=stdout))
    assert REAL_PROBE_HEIGHT(tmp_path / "s.mp4") == 0


# ── 5. 失败诊断：自动补跑 --list-formats ───────────────────────────────────

def test_list_formats_runs_after_the_last_retry_fails(tmp_path, monkeypatch):
    cmds = runner(monkeypatch, always(Result(1, FORMAT_MISSING)))
    with pytest.raises(SystemExit):
        download(tmp_path)
    diag = [c for c in cmds if is_list_formats(c)]
    assert len(diag) == 1, "取源失败必须补跑一次 --list-formats"
    assert diag[0] == cmds[-1], "诊断应该是最后一步"


def test_list_formats_output_lands_in_the_json_payload(tmp_path, monkeypatch,
                                                       capsys):
    """只进 stdout 不行：失败诊断 JSON 是机器读的那一份。"""
    runner(monkeypatch, always(Result(1, FORMAT_MISSING)))
    with pytest.raises(SystemExit):
        download(tmp_path)
    payload = last_payload(capsys)
    assert "640x360" in payload["available_formats"]
    assert "256x144" in payload["available_formats"]


def test_list_formats_output_also_lands_in_the_log(tmp_path, monkeypatch,
                                                   capsys):
    """既要进 JSON 也要进人读的日志：只进 JSON 的话，看 CI 网页日志的人还得自己
    去 payload 里翻。"""
    runner(monkeypatch, always(Result(1, FORMAT_MISSING)))
    with pytest.raises(SystemExit):
        download(tmp_path)
    cap = capsys.readouterr()
    assert "--list-formats" in cap.out
    # die() 那行 JSON 里也有 available_formats，不摘掉的话「诊断不打进日志」
    # 这种退化会被 payload 蒙混过关（变异验证里 M13 就是这么漏的）
    human = "\n".join(line for line in cap.err.splitlines()
                      if not line.lstrip().startswith("{"))
    assert "640x360" in human, "格式表没进人读的日志，只躲在 JSON 里"


def test_list_formats_reuses_the_same_cookies_and_timeout(tmp_path,
                                                          monkeypatch):
    """诊断得和下载问同一个源站、用同一份凭据，否则结论不可比。"""
    cookies = tmp_path / "c.txt"
    cmds = runner(monkeypatch, always(Result(1, FORMAT_MISSING)))
    with pytest.raises(SystemExit):
        download(tmp_path, cookies=cookies, socket_timeout_sec=45)

    diag = [c for c in cmds if is_list_formats(c)][0]
    assert diag[diag.index("--cookies") + 1] == str(cookies)
    assert diag[diag.index("--socket-timeout") + 1] == "45"
    assert "https://youtu.be/NVD-m9seDe4" in diag


def test_list_formats_omits_cookies_flag_when_there_are_none(tmp_path,
                                                             monkeypatch):
    cmds = runner(monkeypatch, always(Result(1, FORMAT_MISSING)))
    with pytest.raises(SystemExit):
        download(tmp_path)
    assert "--cookies" not in [c for c in cmds if is_list_formats(c)][0]


def test_diagnostic_logs_the_cookies_path_but_never_its_content(
        tmp_path, monkeypatch, capsys):
    """路径可以打（要能确认用的是哪份凭据），内容一个字都不许进日志。"""
    secret = "AAAA_SESSION_TOKEN_AAAA"
    cookies = tmp_path / "c.txt"
    leak = f".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\t{secret}"
    runner(monkeypatch, always(Result(1, FORMAT_MISSING), table=leak))

    with pytest.raises(SystemExit):
        download(tmp_path, cookies=cookies)

    captured = capsys.readouterr()
    assert str(cookies) in captured.out
    assert secret not in captured.out + captured.err


def test_diagnostic_never_turns_on_verbose_flags(tmp_path, monkeypatch):
    """--verbose / --print-traffic 会把请求头连 Cookie 一起打出来。"""
    cmds = runner(monkeypatch, always(Result(1, FORMAT_MISSING)))
    with pytest.raises(SystemExit):
        download(tmp_path, cookies=tmp_path / "c.txt")
    diag = [c for c in cmds if is_list_formats(c)][0]
    for flag in ("--verbose", "-v", "--print-traffic", "--dump-pages"):
        assert flag not in diag


def test_diagnostic_output_is_truncated(tmp_path, monkeypatch, capsys):
    """整表能有几十行，别把失败日志冲垮。"""
    runner(monkeypatch,
           always(Result(1, FORMAT_MISSING), table="x" * 50000))
    with pytest.raises(SystemExit):
        download(tmp_path)
    payload = last_payload(capsys)
    assert len(payload["available_formats"]) <= produce.LIST_FORMATS_DIAG_CHARS


def test_diagnostic_failure_does_not_mask_the_real_error(tmp_path, monkeypatch,
                                                         capsys):
    """诊断本身炸了也得把原始失败原样报出来。"""
    def handler(cmd):
        if is_list_formats(cmd):
            raise OSError("yt-dlp 不见了")
        return Result(1, FORMAT_MISSING)

    runner(monkeypatch, handler)
    with pytest.raises(SystemExit) as e:
        download(tmp_path)

    assert e.value.code == produce.EXIT_API
    payload = last_payload(capsys)
    assert payload["reason"] == "download_failed"
    assert "yt-dlp 不见了" in payload["available_formats"]


def test_no_diagnostic_when_retrying_is_pointless(tmp_path, monkeypatch):
    """登录墙 / 片源不存在时补跑 --list-formats 只是白花一次请求。"""
    for output in (BOT_WALL, NOT_FOUND):
        cmds = runner(monkeypatch, always(Result(1, output)))
        with pytest.raises(SystemExit):
            download(tmp_path)
        assert not [c for c in cmds if is_list_formats(c)]


def test_no_diagnostic_on_success(tmp_path, monkeypatch):
    cmds = runner(monkeypatch, always(Result(0)))
    download(tmp_path)
    assert not [c for c in cmds if is_list_formats(c)]


# ── 6. workflow 把 EJS 装齐了（静态核对，不跑 act）────────────────────────

@pytest.fixture(scope="module")
def produce_yml() -> str:
    return PRODUCE_YML.read_text(encoding="utf-8")


def test_workflow_installs_both_ejs_pieces(produce_yml):
    """[default] 带求解脚本 yt-dlp-ejs，[deno] 带运行时本体，少一个都解不开
    n 挑战。裸 `pip install yt-dlp` 两件都没有 —— 线上就是这么挂的。"""
    assert re.search(r'pip install --upgrade "yt-dlp\[[^"]*default[^"]*\]"',
                     produce_yml), "没装 [default]（yt-dlp-ejs 求解脚本）"
    assert re.search(r'pip install --upgrade "yt-dlp\[[^"]*deno[^"]*\]"',
                     produce_yml), "没装 [deno]（JS 运行时本体）"


def test_workflow_no_longer_installs_bare_ytdlp(produce_yml):
    """反向断言：别又退回没有 extra 的那一行。"""
    assert not re.search(r"^\s*pip install --upgrade yt-dlp\s*$",
                         produce_yml, re.MULTILINE)


def self_check_step(produce_yml: str) -> str:
    step = produce_yml[produce_yml.index("自检 EJS 运行时"):]
    return step[:step.index("\n      - name:")]


def test_workflow_self_checks_the_js_runtime(produce_yml):
    step = self_check_step(produce_yml)
    assert "JS runtimes" in step, "没核对 yt-dlp 认出来的运行时"
    assert "deno-" in step
    assert "yt_dlp_ejs-" in step, "没核对求解脚本包"


def test_self_check_prints_both_versions(produce_yml):
    """两个版本号都要打出来：解不开挑战时，第一眼要能看到装的是哪个版本。

    deno 那一行同时兼作「运行时在不在 PATH 上」的早期守卫。
    """
    step = self_check_step(produce_yml)
    assert "deno --version" in step, "没打 deno 版本"
    install = produce_yml[produce_yml.index("安装 yt-dlp 与 EJS 依赖"):]
    assert "yt-dlp --version" in install[:install.index("\n      - name:")]


def test_every_self_check_guard_actually_exits_nonzero(produce_yml):
    """每个 `|| { ... }` 守卫都得自己 exit 1。

    只断言「步骤里有 exit 1」是不够的：三个守卫里随便删掉一个的 exit 1，那条
    缺件路径就静默放过，而整步仍然有别的 exit 1（变异验证里 M23 就是这么漏的）。
    """
    step = self_check_step(produce_yml)
    guards = step.count("|| {")
    assert guards >= 2, f"自检只有 {guards} 个守卫，太少"
    assert step.count("exit 1") == guards, \
        f"{guards} 个守卫却只有 {step.count('exit 1')} 个 exit 1，有守卫不退非零"


def test_self_check_runs_before_producing(produce_yml):
    """缺件要在取源之前炸，别等跑到 1/9 input 才发现。"""
    assert (produce_yml.index("自检 EJS 运行时")
            < produce_yml.index("name: 出片"))


def test_self_check_does_not_need_the_network(produce_yml):
    """不带 URL 的 --simulate 会打完 debug 头再报缺参数，拿诊断不用碰 YouTube。"""
    step = self_check_step(produce_yml)
    assert "--simulate" in step
    assert "youtu" not in step, "自检不该真的去请求 YouTube"


def test_workflow_yaml_still_parses_and_orders_the_new_steps():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PRODUCE_YML.read_text(encoding="utf-8"))
    names = [s.get("name", "") for s in doc["jobs"]["produce"]["steps"]]
    assert "安装 yt-dlp 与 EJS 依赖" in names
    assert "自检 EJS 运行时" in names
    assert (names.index("安装 yt-dlp 与 EJS 依赖")
            < names.index("自检 EJS 运行时") < names.index("出片"))
