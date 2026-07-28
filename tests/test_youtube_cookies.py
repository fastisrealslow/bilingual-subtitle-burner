"""YouTube 登录态：secret 落盘、格式校验、取源报错可辨识、内容不进日志。

数据中心 IP 上的 yt-dlp 一律被 YouTube 回「Sign in to confirm you're not a
bot」，GitHub runner 也是数据中心 IP。凭据早就存在 ``YOUTUBE_COOKIES_B64`` 这个
secret 里，但只有老的 ``pipeline.yml`` 接了，当前在用的 ``produce.yml`` 没接 ——
凭据有，链路没通。

这里锁五件事：

1. **不静默降级**：解码失败、格式非法、``COOKIES_FILE`` 指向的文件不存在，一律
   退 1，绝不退化成不带 cookies 去下载。
2. **报错可辨识**：带了 cookies 还被挡 = 凭据过期，没带 cookies 被挡 = 没配凭据。
   两种情况的 ``reason`` 必须不同，文案里要点出该动哪个 secret。
3. **内容不进日志**：GitHub 的 secret masking 只遮蔽 secret **原文**，base64 解码
   后的内容不在遮蔽范围内。所以校验报错、yt-dlp 输出回显都不许带出 cookie 值。
4. **不落进工作区**：工作区里的文件会被 ``git add`` 连带提交，也会被
   ``upload-artifact`` 打包带出去（失败时整个 ``_tmp/`` 都上传）。
5. **明文槽位容忍粘贴伤但不掩盖非法**：``YOUTUBE_COOKIES`` 是给「手上没有命令行」
   的人留的，全文粘进网页输入框即可。BOM、CRLF、首尾空行要抹掉，畸形 cookie 行
   一概不修 —— 「修」出来的凭据到 yt-dlp 那里才炸，报错里带的是会话令牌。

一律打桩，不真的下载 YouTube 视频。
"""

import base64
import json
import re
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                  # noqa: E402
import youtube_cookies as YC                    # noqa: E402

PRODUCE_YML = ROOT / ".github" / "workflows" / "produce.yml"

# 会话令牌的替身。凡是「不许泄露」的断言都拿它当哨兵：只要它出现在日志或结构化
# 报错里，就说明有一条泄露路径。
SECRET_VALUE = "s3cr3t-sapisid-value"

GOOD = ("# Netscape HTTP Cookie File\n"
        "# 这是一行注释\n"
        f".youtube.com\tTRUE\t/\tTRUE\t1800000000\tSAPISID\t{SECRET_VALUE}\n"
        f"#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tSID\t{SECRET_VALUE}\n")

# 各种坏法，每一种都埋着哨兵值
BROKEN = {
    "缺文件头": f".youtube.com\tTRUE\t/\tTRUE\t0\tSID\t{SECRET_VALUE}\n",
    "空格分隔": ("# Netscape HTTP Cookie File\n"
              f".youtube.com TRUE / TRUE 0 SID {SECRET_VALUE}\n"),
    "字段少一个": ("# Netscape HTTP Cookie File\n"
               f".youtube.com\tTRUE\t/\tTRUE\t0\t{SECRET_VALUE}\n"),
    "字段多一个": ("# Netscape HTTP Cookie File\n"
               f".youtube.com\tTRUE\t/\tTRUE\t0\tSID\t{SECRET_VALUE}\tx\n"),
    "domain-为空": ("# Netscape HTTP Cookie File\n"
                 f"\tTRUE\t/\tTRUE\t0\tSID\t{SECRET_VALUE}\n"),
    "expires-不是时间戳": ("# Netscape HTTP Cookie File\n"
                     f".youtube.com\tTRUE\t/\tTRUE\t永不\tSID\t{SECRET_VALUE}\n"),
    "只有注释": "# Netscape HTTP Cookie File\n# 什么都没有\n",
    "空内容": "",
    "JSON-格式": '[{"name": "SID", "value": "%s"}]\n' % SECRET_VALUE,
}

BOT_WALL = ("ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you're not a bot."
            " Use --cookies-from-browser or --cookies for the authentication.")


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """每条用例都从「什么都没配」起步，别让宿主环境漏进来。"""
    for name in (YC.B64_ENV, YC.PLAIN_ENV, YC.COOKIES_ENV, "GITHUB_WORKSPACE"):
        monkeypatch.delenv(name, raising=False)


# ── 1. base64 解码：坏了就报错，不当明文用 ──────────────────────────────────

def test_valid_base64_round_trips():
    assert YC.decode_secret(b64(GOOD)) == GOOD


def test_base64_tolerates_wrapped_newlines():
    """`base64` 不带 -w0 时会折行，粘进 secret 里就带着换行。"""
    wrapped = "\n".join(re.findall(r".{1,40}", b64(GOOD)))
    assert YC.decode_secret(wrapped) == GOOD


@pytest.mark.parametrize("raw,why", [
    ("not base64!!!", "根本不是 base64"),
    ("YWJj=", "填充位置不对"),
    ("QUJD QUJ", "长度不是 4 的倍数"),
    ("   ", "只有空白"),
])
def test_broken_base64_raises(raw, why):
    with pytest.raises(YC.CookiesError):
        YC.decode_secret(raw)


def test_plaintext_in_the_b64_slot_is_rejected_not_guessed():
    """明文塞进 B64 槽位要报错并指路，不许猜着当明文用。

    猜对了这一次，下次就再也说不清这个 secret 该存哪种格式。
    """
    with pytest.raises(YC.CookiesError) as e:
        YC.decode_secret(GOOD)
    assert YC.PLAIN_ENV in str(e.value)


def test_non_utf8_payload_reports_reason_without_dumping_bytes():
    with pytest.raises(YC.CookiesError) as e:
        YC.decode_secret(base64.b64encode(b"\xff\xfe\x00binary").decode())
    msg = str(e.value)
    assert "UTF-8" in msg
    assert "\\xff" not in msg and "0xff" not in msg


# ── 2. Netscape 格式校验 ────────────────────────────────────────────────────

def test_good_file_counts_records_and_domains():
    records, domains = YC.validate_netscape(GOOD)
    assert records == 2          # 注释不算，#HttpOnly_ 那行算
    assert domains == 2          # `.youtube.com` 和 `#HttpOnly_.youtube.com`


def test_httponly_line_is_data_not_comment():
    only_httponly = ("# Netscape HTTP Cookie File\n"
                     "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tv\n")
    assert YC.validate_netscape(only_httponly)[0] == 1


def test_header_variants_are_accepted():
    for header in ("# Netscape HTTP Cookie File",
                   "# HTTP Cookie File",
                   "#Netscape HTTP Cookie File"):
        body = f"{header}\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tv\n"
        assert YC.validate_netscape(body)[0] == 1


def test_empty_expires_is_accepted():
    """有些导出把会话 cookie 的 expires 留空，MozillaCookieJar 认这种。"""
    body = ("# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t\tSID\tv\n")
    assert YC.validate_netscape(body)[0] == 1


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_broken_files_are_rejected(name):
    with pytest.raises(YC.CookiesError):
        YC.validate_netscape(BROKEN[name])


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_rejection_message_never_leaks_cookie_values(name):
    """报错文案只许有行号和计数。

    yt-dlp 自己拒这种文件时会把出错那一行**原样**打出来，正是要避免的东西。
    """
    with pytest.raises(YC.CookiesError) as e:
        YC.validate_netscape(BROKEN[name])
    assert SECRET_VALUE not in str(e.value)


def test_rejection_message_points_at_the_offending_line_number():
    """只说「格式不对」等于让人去猜。得说清是第几行。"""
    body = ("# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\tSID\tv\n"
            ".youtube.com\tTRUE\t/\tTRUE\t0\tHSID\n")
    with pytest.raises(YC.CookiesError) as e:
        YC.validate_netscape(body)
    assert "第 3 行" in str(e.value)


# ── 3. 明文槽位：容忍粘贴伤，但不掩盖非法 ──────────────────────────────────

BOM = "\ufeff"
# 网页输入框里粘贴会带进来的几种伤，每一种单独一条 case
PASTE_DAMAGE = {
    "BOM-开头": BOM + GOOD,
    "CRLF-换行": GOOD.replace("\n", "\r\n"),
    "孤立-CR-换行": GOOD.replace("\n", "\r"),
    "首尾空行": "\n\n" + GOOD + "\n\n",
    "首尾空白行": "  \n\t\n" + GOOD + "\n   \n",
    "全都赶上了": BOM + "\r\n" + GOOD.replace("\n", "\r\n") + "  \r\n",
}


def test_clean_text_is_left_alone():
    """没伤的全文不许被动手脚，只有末尾那个换行由落盘环节补。"""
    assert YC.normalize_cookie_text(GOOD) + "\n" == GOOD


@pytest.mark.parametrize("name", sorted(PASTE_DAMAGE))
def test_paste_damage_is_normalized_away(name):
    assert YC.normalize_cookie_text(PASTE_DAMAGE[name]) + "\n" == GOOD


@pytest.mark.parametrize("name", sorted(PASTE_DAMAGE))
def test_damaged_paste_still_validates(name):
    """归一化之后必须还能过校验 —— 否则「容忍」只是嘴上说说。"""
    text = YC.normalize_cookie_text(PASTE_DAMAGE[name])
    assert YC.validate_netscape(text) == (2, 2)


def test_bom_would_break_it_without_normalizing():
    """反向证明这一步不是摆设：BOM 是隐形字符，不抹掉时校验直接不认文件头。"""
    with pytest.raises(YC.CookiesError):
        YC.validate_netscape(BOM + GOOD)


def test_normalized_file_is_loadable_by_a_real_cookie_jar(tmp_path):
    """终局判据：受伤的粘贴过一遍归一化，标准库的 MozillaCookieJar 认得。

    只看自家校验器会漏掉「校验器和真实消费方口径不一致」这种错。
    """
    from http.cookiejar import MozillaCookieJar
    out = tmp_path / "c.txt"
    YC.write_cookies_file(out, YC.normalize_cookie_text(PASTE_DAMAGE["全都赶上了"]))
    jar = MozillaCookieJar(str(out))
    jar.load()
    # 标准库把 `#HttpOnly_` 当注释跳过（yt-dlp / curl 认它是数据行），所以只剩一条
    assert [c.name for c in jar] == ["SAPISID"]


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_normalizing_never_repairs_a_malformed_jar(name):
    """畸形行一概不修：空格分隔、字段个数不对，归一化之后照样该拒。"""
    with pytest.raises(YC.CookiesError):
        YC.validate_netscape(YC.normalize_cookie_text(BROKEN[name]))


def test_normalizing_does_not_touch_tabs_inside_lines():
    """把行内制表符也一起「规范」掉的话，合法 jar 会被自己弄坏。"""
    assert "\t" in YC.normalize_cookie_text(GOOD.replace("\n", "\r\n"))


def test_plain_slot_takes_the_text_verbatim():
    assert YC.load_plain_secret(GOOD) + "\n" == GOOD


def test_base64_in_the_plain_slot_is_rescued(capsys):
    """他把 base64 粘错了槽位：解开继续，但要在日志里点名说清用错了。

    直接报「不是 Netscape 格式」的话，他会去检查 cookies.txt 本身 —— 而那份文件
    没问题，问题只是粘进了另一个 secret。
    """
    assert YC.load_plain_secret(b64(GOOD)) + "\n" == GOOD
    warned = capsys.readouterr().err
    assert YC.PLAIN_ENV in warned and YC.B64_ENV in warned
    assert SECRET_VALUE not in warned


def test_rescue_cannot_misfire_on_genuine_netscape_text():
    """合法明文永远走不进 base64 分支 —— 文件头那个 `#` 就不在 base64 字母表里。"""
    assert YC.rescue_base64_in_plain_slot(GOOD) is None
    for name, body in BROKEN.items():
        if body.lstrip().startswith("#"):
            assert YC.rescue_base64_in_plain_slot(body) is None, name


@pytest.mark.parametrize("blob,why", [
    (b64("随便一段不是 cookies 的文本\n"), "解得开但不是 Netscape"),
    (base64.b64encode(b"\xff\xfe\x00binary").decode(), "解出来不是 UTF-8"),
    ("QUJDRA", "长度不是 4 的倍数"),
    ("YWJj=", "填充位置不对"),
])
def test_rescue_only_accepts_a_real_jar(blob, why):
    """仲裁标准是「解出来本身就是合法 jar」，达不到就当没这回事。"""
    assert YC.rescue_base64_in_plain_slot(blob) is None, why


def test_unrescuable_plain_text_keeps_the_precise_error(capsys):
    """救不出来时报的还是那条带行号的错，不许被一句「不是 base64」盖掉。"""
    with pytest.raises(YC.CookiesError) as e:
        YC.validate_netscape(YC.load_plain_secret(BROKEN["空格分隔"]))
    assert "第 2 行" in str(e.value)
    assert SECRET_VALUE not in str(e.value) + capsys.readouterr().err


# ── 4. 落盘：权限、位置 ─────────────────────────────────────────────────────

def test_written_file_is_owner_only(tmp_path):
    out = tmp_path / "sub" / "c.txt"
    YC.write_cookies_file(out, GOOD)
    assert out.read_text() == GOOD
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_file_is_born_tight_not_tightened_afterwards(tmp_path, monkeypatch):
    """创建时就得是 600。先 644 落盘再 chmod 的话，中间那一瞬同机别的进程能读到。

    把 chmod 打成空操作，剩下的就只有 os.open 的 mode 在起作用。
    """
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)
    out = tmp_path / "c.txt"
    YC.write_cookies_file(out, GOOD)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_preexisting_loose_permissions_are_tightened(tmp_path):
    out = tmp_path / "c.txt"
    out.write_text("旧内容")
    out.chmod(0o644)
    YC.write_cookies_file(out, GOOD)
    assert stat.S_IMODE(out.stat().st_mode) == 0o600


def test_missing_trailing_newline_is_added(tmp_path):
    """Netscape 文件最后一行没换行时 MozillaCookieJar 会漏掉它。"""
    out = tmp_path / "c.txt"
    YC.write_cookies_file(out, GOOD.rstrip("\n"))
    assert out.read_text().endswith("\n")


def test_out_inside_the_repo_is_refused():
    with pytest.raises(YC.CookiesError) as e:
        YC.guard_outside_workspace(ROOT / "secrets" / "youtube_cookies.txt")
    assert "$RUNNER_TEMP" in str(e.value)


def test_out_inside_github_workspace_is_refused(tmp_path, monkeypatch):
    """CI 上工作区不是仓库目录本身，得照着 GITHUB_WORKSPACE 判一遍。"""
    workspace = tmp_path / "work" / "repo"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("GITHUB_WORKSPACE", str(workspace))
    with pytest.raises(YC.CookiesError):
        YC.guard_outside_workspace(workspace / "secrets" / "c.txt")


def test_runner_temp_layout_is_accepted(tmp_path, monkeypatch):
    """CI 的真实布局：_temp 和工作区是兄弟目录，不该被误拦。"""
    root = tmp_path / "work"
    monkeypatch.setenv("GITHUB_WORKSPACE", str(root / "repo" / "repo"))
    YC.guard_outside_workspace(root / "_temp" / "youtube_cookies.txt")


# ── 5. 脚本入口 ────────────────────────────────────────────────────────────

def run_script(tmp_path, monkeypatch, out=None, github_env=True, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    out = out or tmp_path / "youtube_cookies.txt"
    argv = ["--out", str(out)]
    genv = tmp_path / "github_env"
    if github_env:
        genv.touch()
        argv += ["--github-env", str(genv)]
    return YC.main(argv), Path(out), genv


def test_missing_secret_succeeds_without_writing_anything(tmp_path, monkeypatch,
                                                          capsys):
    """secret 没配是合法状态：非 YouTube 源不需要登录态。"""
    rc, out, genv = run_script(tmp_path, monkeypatch)
    assert rc == 0
    assert not out.exists()
    # 没写 COOKIES_FILE 才能让取源那边区分「没配」和「配坏了」
    assert genv.read_text() == ""
    said = capsys.readouterr().out
    assert "未配置" in said and "不受影响" in said


def test_b64_secret_is_written_and_exported(tmp_path, monkeypatch, capsys):
    rc, out, genv = run_script(tmp_path, monkeypatch,
                               **{YC.B64_ENV: b64(GOOD)})
    assert rc == 0
    assert out.read_text() == GOOD
    assert genv.read_text().strip() == f"{YC.COOKIES_ENV}={out}"
    assert SECRET_VALUE not in capsys.readouterr().out


def test_plaintext_secret_is_the_fallback(tmp_path, monkeypatch, capsys):
    """明文槽位走完整条链路：落盘、导出 COOKIES_FILE、600、日志点名来源。"""
    rc, out, genv = run_script(tmp_path, monkeypatch, **{YC.PLAIN_ENV: GOOD})
    assert rc == 0
    assert out.read_text() == GOOD
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert genv.read_text().strip() == f"{YC.COOKIES_ENV}={out}"
    said = capsys.readouterr().out
    assert YC.PLAIN_ENV in said              # 得看得出这次用的是哪个 secret
    assert SECRET_VALUE not in said


def test_the_log_names_which_secret_was_used(tmp_path, monkeypatch, capsys):
    """两个槽位打出来的来源必须不同，否则日志里看不出他改的是哪个 secret 生效了。"""
    sources = {}
    for name, value in ((YC.B64_ENV, b64(GOOD)), (YC.PLAIN_ENV, GOOD)):
        monkeypatch.delenv(YC.B64_ENV, raising=False)
        monkeypatch.delenv(YC.PLAIN_ENV, raising=False)
        home = tmp_path / name
        home.mkdir()
        run_script(home, monkeypatch, **{name: value})
        sources[name] = capsys.readouterr().out
    assert YC.B64_ENV in sources[YC.B64_ENV]
    assert sources[YC.PLAIN_ENV].count(YC.B64_ENV) == 0


def test_b64_wins_over_plaintext(tmp_path, monkeypatch, capsys):
    other = GOOD.replace("SAPISID", "APISID")
    rc, out, _ = run_script(tmp_path, monkeypatch,
                            **{YC.B64_ENV: b64(other), YC.PLAIN_ENV: GOOD})
    assert rc == 0 and out.read_text() == other
    assert YC.B64_ENV in capsys.readouterr().out


@pytest.mark.parametrize("empty,why", [
    ("", "secret 没配时 GitHub 把 env 展开成空串"),
    ("   \n", "只有空白也算没配"),
])
def test_empty_b64_falls_through_to_plaintext(tmp_path, monkeypatch, capsys,
                                              empty, why):
    """B64 槽位空着不算「配了」—— 否则他只填明文那个就会撞上一句 base64 解码失败。

    GitHub 对不存在的 secret 是展开成空串，所以这条是默认路径而不是边角情况。
    """
    rc, out, _ = run_script(tmp_path, monkeypatch,
                            **{YC.B64_ENV: empty, YC.PLAIN_ENV: GOOD})
    assert rc == 0, why
    assert out.read_text() == GOOD
    assert YC.PLAIN_ENV in capsys.readouterr().out


@pytest.mark.parametrize("name", sorted(PASTE_DAMAGE))
def test_damaged_paste_goes_through_the_script(tmp_path, monkeypatch, capsys,
                                               name):
    """粘贴伤要在落盘前抹掉：写出去的文件得是干净的 LF、不带 BOM。"""
    rc, out, genv = run_script(tmp_path, monkeypatch,
                               **{YC.PLAIN_ENV: PASTE_DAMAGE[name]})
    assert rc == 0
    assert out.read_text(encoding="utf-8") == GOOD
    assert "\r" not in out.read_text(encoding="utf-8")
    assert genv.read_text().strip() == f"{YC.COOKIES_ENV}={out}"
    assert SECRET_VALUE not in capsys.readouterr().out


@pytest.mark.parametrize("name",
                         sorted(n for n in BROKEN if BROKEN[n].strip()))
def test_malformed_plaintext_exits_config_and_writes_nothing(
        tmp_path, monkeypatch, capsys, name):
    """畸形明文照样退 1：容忍粘贴伤不等于容忍畸形 jar。

    「空内容」不在这里 —— 空 secret 是「没配」，走的是退 0 那条路。
    """
    rc, out, genv = run_script(tmp_path, monkeypatch,
                               **{YC.PLAIN_ENV: BROKEN[name]})
    assert rc == YC.EXIT_CONFIG
    assert rc != 0
    assert not out.exists()
    assert genv.read_text() == ""
    captured = capsys.readouterr()
    assert SECRET_VALUE not in captured.err + captured.out


def test_base64_pasted_into_the_plain_slot_still_works(tmp_path, monkeypatch,
                                                       capsys):
    """粘错槽位不该报「不是 Netscape 格式」—— 那会让他去查一份没问题的文件。"""
    rc, out, genv = run_script(tmp_path, monkeypatch,
                               **{YC.PLAIN_ENV: b64(GOOD)})
    assert rc == 0
    assert out.read_text() == GOOD
    assert genv.read_text().strip() == f"{YC.COOKIES_ENV}={out}"
    captured = capsys.readouterr()
    warned = captured.err
    assert YC.B64_ENV in warned and YC.PLAIN_ENV in warned
    assert SECRET_VALUE not in captured.err + captured.out


def test_plain_slot_holding_real_text_never_warns(tmp_path, monkeypatch, capsys):
    """反向断言：正常粘全文时不许冒出「你用错 secret 了」这句话。"""
    run_script(tmp_path, monkeypatch, **{YC.PLAIN_ENV: GOOD})
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("secret,why", [
    ("not base64!!!", "解码失败"),
    (b64("# Netscape HTTP Cookie File\n没有制表符\n"), "格式非法"),
])
def test_broken_secret_exits_config_and_writes_nothing(tmp_path, monkeypatch,
                                                       capsys, secret, why):
    """坏凭据必须退 1，且不许留下半个文件让下游当成「有凭据」。"""
    rc, out, genv = run_script(tmp_path, monkeypatch, **{YC.B64_ENV: secret})
    assert rc == YC.EXIT_CONFIG, why
    assert rc != 0
    assert not out.exists()
    assert genv.read_text() == ""
    err = capsys.readouterr().err
    assert YC.B64_ENV in err          # 得说清该去动哪个 secret


def test_broken_secret_error_does_not_echo_content(tmp_path, monkeypatch,
                                                   capsys):
    rc, _, _ = run_script(tmp_path, monkeypatch,
                          **{YC.B64_ENV: b64(BROKEN["缺文件头"])})
    assert rc == YC.EXIT_CONFIG
    captured = capsys.readouterr()
    assert SECRET_VALUE not in captured.err + captured.out


def test_out_in_workspace_exits_config(tmp_path, monkeypatch):
    rc, _, _ = run_script(tmp_path, monkeypatch,
                          out=ROOT / "secrets" / "c.txt",
                          **{YC.B64_ENV: b64(GOOD)})
    assert rc == YC.EXIT_CONFIG


def test_usage_error_exits_config_not_quality(capsys):
    """参数错退 1 —— 2 在本仓库是「拒绝硬出」的专用码。"""
    with pytest.raises(SystemExit) as e:
        YC.main(["--out", "/tmp/c.txt", "--nonsense"])
    assert e.value.code == YC.EXIT_CONFIG
    assert e.value.code != produce.EXIT_QUALITY
    # 退出码对了还不够：得看得出是哪个参数惹的祸
    assert "--nonsense" in capsys.readouterr().err


# ── 6. produce.py 取源：读 COOKIES_FILE ────────────────────────────────────

def test_no_cookies_env_is_explicit_in_the_log(monkeypatch, capsys):
    assert produce.cookies_file_from_env() is None
    said = capsys.readouterr().out
    assert "未配置" in said and "不受影响" in said


def test_valid_cookies_file_is_used(tmp_path, monkeypatch, capsys):
    f = tmp_path / "c.txt"
    f.write_text(GOOD)
    monkeypatch.setenv(YC.COOKIES_ENV, str(f))
    assert produce.cookies_file_from_env() == f
    assert SECRET_VALUE not in capsys.readouterr().out


def test_missing_cookies_file_exits_config(tmp_path, monkeypatch, capsys):
    """配了 COOKIES_FILE 却没有这个文件 = 链路坏了，不许当成「没配」。"""
    monkeypatch.setenv(YC.COOKIES_ENV, str(tmp_path / "nope.txt"))
    with pytest.raises(SystemExit) as e:
        produce.cookies_file_from_env()
    assert e.value.code == produce.EXIT_CONFIG
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["reason"] == "cookies_file_missing"


def test_invalid_cookies_file_exits_config_without_leaking(tmp_path, monkeypatch,
                                                           capsys):
    f = tmp_path / "c.txt"
    f.write_text(BROKEN["空格分隔"])
    monkeypatch.setenv(YC.COOKIES_ENV, str(f))
    with pytest.raises(SystemExit) as e:
        produce.cookies_file_from_env()
    assert e.value.code == produce.EXIT_CONFIG
    captured = capsys.readouterr()
    payload = json.loads(captured.err.strip().splitlines()[-1])
    assert payload["reason"] == "cookies_file_invalid"
    assert SECRET_VALUE not in captured.err + captured.out


def test_invalid_cookies_never_degrades_to_downloading_without_them(
        tmp_path, monkeypatch):
    """坏凭据要在下载之前就拦住 —— 带着「没有 cookies」去问 YouTube，
    回来的是登录墙那句话，真实原因就被埋掉了。"""
    f = tmp_path / "c.txt"
    f.write_text(BROKEN["缺文件头"])
    monkeypatch.setenv(YC.COOKIES_ENV, str(f))
    monkeypatch.setattr(produce.shutil, "which", lambda n: "/usr/bin/yt-dlp")
    monkeypatch.setattr(
        produce, "download_source",
        lambda *a, **k: pytest.fail("凭据非法却还是发起了下载"))
    with pytest.raises(SystemExit) as e:
        produce.resolve_source("https://youtu.be/x", tmp_path)
    assert e.value.code == produce.EXIT_CONFIG


# ── 7. yt-dlp 命令行 ───────────────────────────────────────────────────────

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


@pytest.fixture(autouse=True)
def height_ok(monkeypatch):
    """默认让 360p 保底闸门放行 —— 这个文件测的是凭据，不是清晰度。"""
    monkeypatch.setattr(produce, "probe_height", lambda p: 480)


def runner(monkeypatch, results):
    cmds = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        return results[min(len(cmds) - 1, len(results) - 1)]

    monkeypatch.setattr(produce.subprocess, "run", fake_run)
    return cmds


def test_cookies_reach_ytdlp(tmp_path, monkeypatch, no_sleep):
    cmds = runner(monkeypatch, [Result(0)])
    produce.download_source("https://youtu.be/x", tmp_path / "s.mp4", 3, 5.0,
                            cookies=tmp_path / "c.txt")
    cmd = cmds[0]
    assert cmd[cmd.index("--cookies") + 1] == str(tmp_path / "c.txt")


def test_no_cookies_means_no_flag(tmp_path, monkeypatch, no_sleep):
    cmds = runner(monkeypatch, [Result(0)])
    produce.download_source("https://archive.org/x", tmp_path / "s.mp4", 3, 5.0)
    assert "--cookies" not in cmds[0]


def test_verbose_flags_stay_off(tmp_path, monkeypatch, no_sleep):
    """--verbose / --print-traffic 会把请求头连 Cookie 一起打进日志。"""
    cmds = runner(monkeypatch, [Result(0)])
    produce.download_source("https://youtu.be/x", tmp_path / "s.mp4", 3, 5.0,
                            cookies=tmp_path / "c.txt")
    for flag in ("--verbose", "-v", "--print-traffic", "--dump-pages"):
        assert flag not in cmds[0]


def test_resolve_source_forwards_env_cookies(tmp_path, monkeypatch):
    f = tmp_path / "c.txt"
    f.write_text(GOOD)
    monkeypatch.setenv(YC.COOKIES_ENV, str(f))
    seen = {}

    def fake_download(source, out, retries, backoff_sec, socket_timeout_sec,
                      cookies=None):
        seen["cookies"] = cookies
        out.write_bytes(b"x")

    monkeypatch.setattr(produce.shutil, "which", lambda n: "/usr/bin/yt-dlp")
    monkeypatch.setattr(produce, "download_source", fake_download)
    produce.resolve_source("https://youtu.be/x", tmp_path)
    assert seen["cookies"] == f


# ── 8. 登录墙：两种情况两个 reason ─────────────────────────────────────────

def test_bot_wall_with_cookies_reads_as_expired_credentials(
        tmp_path, monkeypatch, no_sleep, capsys):
    cmds = runner(monkeypatch, [Result(1, BOT_WALL)])
    with pytest.raises(SystemExit) as e:
        produce.download_source("https://youtu.be/x", tmp_path / "s.mp4", 5, 5.0,
                                cookies=tmp_path / "c.txt")

    assert e.value.code == produce.EXIT_CONFIG
    assert len(cmds) == 1, "登录墙重试没有意义，不该重试"
    assert no_sleep == []
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["reason"] == "youtube_credentials_expired"
    # 「一眼看出是凭据过期」：文案要点出过期，以及该更新哪个 secret
    assert "过期" in payload["detail"]
    assert YC.B64_ENV in payload["detail"]


def test_bot_wall_without_cookies_reads_as_missing_credentials(
        tmp_path, monkeypatch, no_sleep, capsys):
    cmds = runner(monkeypatch, [Result(1, BOT_WALL)])
    with pytest.raises(SystemExit) as e:
        produce.download_source("https://youtu.be/x", tmp_path / "s.mp4", 5, 5.0)

    assert e.value.code == produce.EXIT_CONFIG
    assert len(cmds) == 1
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["reason"] == "youtube_login_required"
    assert YC.B64_ENV in payload["detail"]


def test_the_two_login_reasons_are_distinguishable(tmp_path, monkeypatch,
                                                   no_sleep, capsys):
    """同一句 YouTube 报错，带没带凭据得给出两个不同的 reason。

    合并成一个的话，日志里读到的永远是「YouTube 挡了」，看不出该去配 secret
    还是该去换 secret。
    """
    reasons = []
    for cookies in (None, tmp_path / "c.txt"):
        runner(monkeypatch, [Result(1, BOT_WALL)])
        with pytest.raises(SystemExit):
            produce.download_source("https://youtu.be/x", tmp_path / "s.mp4",
                                    2, 1.0, cookies=cookies)
        reasons.append(
            json.loads(capsys.readouterr().err.strip().splitlines()[-1])
            ["reason"])
    assert reasons[0] != reasons[1]


def test_login_wall_is_not_lumped_into_generic_download_failure(
        tmp_path, monkeypatch, no_sleep, capsys):
    """反向断言：不许退成 download_failed / source_unavailable。

    前者会被读成「重跑就好」，后者会被读成「换片源」，而真实动作是去更新凭据。
    """
    runner(monkeypatch, [Result(1, BOT_WALL)])
    with pytest.raises(SystemExit):
        produce.download_source("https://youtu.be/x", tmp_path / "s.mp4", 3, 1.0,
                                cookies=tmp_path / "c.txt")
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["reason"] not in ("download_failed", "source_unavailable")


@pytest.mark.parametrize("output", [
    "ERROR: [youtube] x: Sign in to confirm you're not a bot.",
    "ERROR: [youtube] x: Sign in to confirm you’re not a bot.",   # 花引号
    "ERROR: [youtube] x: Sign in to confirm your age",
    "Use --cookies-from-browser or --cookies for the authentication.",
    "ERROR: [youtube] x: Please sign in",
])
def test_login_wall_wordings_are_recognized(output):
    assert produce.download_needs_login(output)


@pytest.mark.parametrize("output", [
    "ERROR: unable to download video data: HTTP Error 500: Internal Server Error",
    "ERROR: The read operation timed out",
    "ERROR: Video unavailable",
])
def test_ordinary_failures_are_not_read_as_login_wall(output):
    assert not produce.download_needs_login(output)


def test_transient_failures_still_retry_with_cookies(tmp_path, monkeypatch,
                                                     no_sleep):
    """别把重试一起改坏：500 仍然该重试，cookies 也要跟着一起重试。"""
    cmds = runner(monkeypatch, [
        Result(1, "ERROR: HTTP Error 500: Internal Server Error"), Result(0)])
    produce.download_source("https://archive.org/x", tmp_path / "s.mp4", 3, 5.0,
                            cookies=tmp_path / "c.txt")
    assert len(cmds) == 2
    assert "--cookies" in cmds[1]


# ── 9. yt-dlp 输出回显不许带出 cookie ─────────────────────────────────────

# yt-dlp 解析 cookies 失败时打的是 repr：制表符成了字面的 \t
YTDLP_COOKIE_LEAK = (
    "ERROR: unable to load cookies: invalid Netscape format cookies file "
    "'/tmp/c.txt': "
    f"'.youtube.com\\tTRUE\\t/\\tTRUE\\t0\\tSAPISID\\t{SECRET_VALUE}\\n'")
RAW_COOKIE_LINE = f".youtube.com\tTRUE\t/\tTRUE\t0\tSAPISID\t{SECRET_VALUE}"


@pytest.mark.parametrize("leak", [YTDLP_COOKIE_LEAK, RAW_COOKIE_LINE])
def test_cookie_lines_are_scrubbed_from_echoed_output(tmp_path, monkeypatch,
                                                      no_sleep, capsys, leak):
    """GitHub 只遮蔽 secret 原文，解码后的内容不在遮蔽范围内 —— 自己擦。"""
    runner(monkeypatch, [Result(1, leak)])
    with pytest.raises(SystemExit):
        produce.download_source("https://youtu.be/x", tmp_path / "s.mp4", 1, 1.0,
                                cookies=tmp_path / "c.txt")
    captured = capsys.readouterr()
    assert SECRET_VALUE not in captured.err + captured.out


def test_scrubbing_keeps_the_useful_part_of_the_error(tmp_path, monkeypatch,
                                                      no_sleep, capsys):
    """擦不能擦成一片空白，否则等于把排查线索一起删了。"""
    runner(monkeypatch, [Result(1, YTDLP_COOKIE_LEAK + "\nERROR: 下载失败了")])
    with pytest.raises(SystemExit):
        produce.download_source("https://youtu.be/x", tmp_path / "s.mp4", 1, 1.0)
    assert "下载失败了" in capsys.readouterr().err


def test_structured_payload_is_scrubbed_too(tmp_path, monkeypatch, no_sleep,
                                            capsys):
    """结构化 JSON 里的 output 字段同样会进日志。"""
    runner(monkeypatch, [Result(1, RAW_COOKIE_LINE)])
    with pytest.raises(SystemExit):
        produce.download_source("https://youtu.be/x", tmp_path / "s.mp4", 1, 1.0)
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert SECRET_VALUE not in json.dumps(payload, ensure_ascii=False)


def test_scrubber_leaves_ordinary_text_alone():
    plain = "ERROR: unable to download video data: HTTP Error 500"
    assert produce.scrub_cookie_material(plain) == plain


# ── 10. workflow 接线（静态核对，不跑 act）─────────────────────────────────

@pytest.fixture(scope="module")
def produce_yml() -> str:
    return PRODUCE_YML.read_text(encoding="utf-8")


def test_workflow_passes_the_b64_secret_to_the_script(produce_yml):
    """凭据早就在仓库里，缺的就是这一根线。"""
    assert "secrets.YOUTUBE_COOKIES_B64" in produce_yml
    assert "scripts/youtube_cookies.py" in produce_yml


def test_workflow_writes_cookies_outside_the_workspace(produce_yml):
    """RUNNER_TEMP 才不会被 git add 或 upload-artifact 带出去。"""
    outs = re.findall(r"--out\s+\"([^\"]+)\"", produce_yml)
    assert outs, "workflow 里没有 --out"
    for out in outs:
        assert "$RUNNER_TEMP" in out
        assert not out.startswith("secrets/")
        assert "$GITHUB_WORKSPACE" not in out


def test_workflow_does_not_enable_shell_tracing(produce_yml):
    """set -x 会把解码后的内容回显进日志。"""
    assert not re.search(r"^\s*set\s+-[a-wyz]*x", produce_yml, re.MULTILINE)


def test_workflow_never_pipes_the_secret_through_the_shell(produce_yml):
    """secret 只走 env 交给 Python，不在 shell 里 printf / base64 -d。"""
    assert "base64 -d" not in produce_yml
    assert "$YOUTUBE_COOKIES_B64" not in produce_yml


def test_cookies_are_prepared_before_producing(produce_yml):
    assert (produce_yml.index("准备 YouTube 登录态")
            < produce_yml.index("name: 出片"))


def test_uploaded_artifacts_cannot_contain_the_cookies_file(produce_yml):
    """上传的是 deliver/ 和 _tmp/，cookies 在 RUNNER_TEMP，两边不相交。"""
    paths = re.findall(r"^\s+path:\s*(\S+)\s*$", produce_yml, re.MULTILINE)
    assert paths
    for p in paths:
        assert "RUNNER_TEMP" not in p
        assert "cookies" not in p


def test_workflow_yaml_parses():
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(PRODUCE_YML.read_text(encoding="utf-8"))
    steps = doc["jobs"]["produce"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert "准备 YouTube 登录态" in names
    assert names.index("准备 YouTube 登录态") < names.index("出片")

    prep = steps[names.index("准备 YouTube 登录态")]
    assert set(prep["env"]) == {YC.B64_ENV, YC.PLAIN_ENV}
    # COOKIES_FILE 由脚本写进 $GITHUB_ENV，出片那步不许自己写死一个路径
    assert YC.COOKIES_ENV not in steps[names.index("出片")].get("env", {})


def test_repo_has_no_committed_cookies_file():
    """老 pipeline.yml 写的是仓库里的 secrets/，这条守着「别再回去」。"""
    assert not (ROOT / "secrets").exists()
    for entry in ROOT.iterdir():
        if entry.name == ".git" or not entry.is_dir():
            continue
        for leftover in entry.rglob("*cookies*.txt"):
            pytest.fail(f"仓库里有 cookies 文件：{leftover}")


def test_cookies_env_name_matches_the_existing_fetch_step():
    """沿用 steps/step1_fetch.py 早就在读的变量名，别另发明一套。"""
    fetch = (ROOT / "steps" / "step1_fetch.py").read_text(encoding="utf-8")
    assert f'os.environ.get("{YC.COOKIES_ENV}"' in fetch
    assert YC.COOKIES_ENV == "COOKIES_FILE"
