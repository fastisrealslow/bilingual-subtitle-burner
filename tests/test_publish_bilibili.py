"""B 站投稿预留接口（scripts/publish_bilibili.py）。

这条链路现在默认关闭 —— workflow 那一步的条件是 BILIBILI_COOKIES 非空，而这个
secret 不存在。这里只测参数拼装，以及最要紧的一条：cookie 缺失时必须明确报错，
不能静默「成功」让人以为片子发出去了。

这些测试不需要任何 secret，也一次都不会真的投稿。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import publish_bilibili as PB                     # noqa: E402


def sample_episode(eid="ep01"):
    return {"index": 1, "id": eid, "title": "简单思维与长期准备",
            "duration_sec": 198.92,
            "tags": ["查理·芒格", "价值投资", "投资思维"],
            "desc": "简介正文\n原视频出处：https://example.com/v",
            "files": {"video": f"{eid}.mp4"}}


def make_delivery(tmp_path, ids=("ep01",), per_episode_dirs=True):
    slug_dir = tmp_path / "deliver" / "munger"
    for eid in ids:
        d = slug_dir / eid if per_episode_dirs else slug_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "final.mp4").write_bytes(b"video")
        (d / "cover_16x9.jpg").write_bytes(b"wide")

    queue = {"schema": 1, "slug": "munger", "speaker": "查理·芒格",
             "episodes": [sample_episode(eid) for eid in ids]}
    path = slug_dir / "queue.json"
    path.write_text(json.dumps(queue, ensure_ascii=False), encoding="utf-8")
    return path


def cookie_file(tmp_path):
    path = tmp_path / "cookies.json"
    path.write_text("{}", encoding="utf-8")
    return path


# ── 参数拼装 ────────────────────────────────────────────────────────────────

def test_upload_args_carry_every_biliup_flag(tmp_path):
    cmd = PB.build_upload_args(sample_episode(), Path("v.mp4"),
                               Path("c.jpg"), cookie_file(tmp_path))

    assert cmd[0] == "biliup" and "upload" in cmd
    for flag in ("--title", "--tid", "--tag", "--desc", "--cover",
                 "--copyright", "--line"):
        assert flag in cmd, f"少了 {flag}"
    assert cmd[cmd.index("--title") + 1] == "简单思维与长期准备"
    assert cmd[cmd.index("--cover") + 1] == "c.jpg"


def test_every_tag_gets_its_own_flag(tmp_path):
    cmd = PB.build_upload_args(sample_episode(), Path("v.mp4"),
                               Path("c.jpg"), cookie_file(tmp_path))
    tags = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--tag"]
    assert tags == ["查理·芒格", "价值投资", "投资思维"]


def test_desc_is_passed_through_verbatim(tmp_path):
    ep = sample_episode()
    cmd = PB.build_upload_args(ep, Path("v.mp4"), Path("c.jpg"),
                               cookie_file(tmp_path))
    assert cmd[cmd.index("--desc") + 1] == ep["desc"]


def test_tid_copyright_and_line_are_overridable(tmp_path):
    cmd = PB.build_upload_args(sample_episode(), Path("v.mp4"), Path("c.jpg"),
                               cookie_file(tmp_path), tid=95, copyright_=2,
                               line="ws")
    assert cmd[cmd.index("--tid") + 1] == "95"
    assert cmd[cmd.index("--copyright") + 1] == "2"
    assert cmd[cmd.index("--line") + 1] == "ws"


def test_defaults_are_the_documented_ones(tmp_path):
    cmd = PB.build_upload_args(sample_episode(), Path("v.mp4"), Path("c.jpg"),
                               cookie_file(tmp_path))
    assert cmd[cmd.index("--tid") + 1] == str(PB.DEFAULT_TID)
    assert cmd[cmd.index("--line") + 1] == PB.DEFAULT_LINE


# ── cookie 缺失必须明确报错 ─────────────────────────────────────────────────

def test_missing_cookie_raises_instead_of_skipping(monkeypatch):
    monkeypatch.delenv(PB.COOKIE_ENV, raising=False)
    with pytest.raises(PB.ConfigError, match="不静默跳过"):
        PB.resolve_cookies()


def test_missing_cookie_exits_one_from_the_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv(PB.COOKIE_ENV, raising=False)
    q = make_delivery(tmp_path)

    assert PB.main(["--queue", str(q), "--episode", "ep01"]) == PB.EXIT_CONFIG
    err = capsys.readouterr().err
    assert PB.COOKIE_ENV in err


def test_cookie_path_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(PB.ConfigError, match="cookie 文件不存在"):
        PB.resolve_cookies(str(tmp_path / "nope.json"))


def test_cookie_comes_from_the_env_when_not_passed(tmp_path, monkeypatch):
    path = cookie_file(tmp_path)
    monkeypatch.setenv(PB.COOKIE_ENV, str(path))
    assert PB.resolve_cookies() == path


# ── 定位产物与集号 ──────────────────────────────────────────────────────────

def test_unknown_episode_id_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(PB.COOKIE_ENV, str(cookie_file(tmp_path)))
    q = make_delivery(tmp_path)

    assert PB.main(["--queue", str(q), "--episode", "ep09"]) == PB.EXIT_CONFIG
    assert "ep09" in capsys.readouterr().err


def test_single_episode_files_at_the_slug_root_are_found(tmp_path):
    q = make_delivery(tmp_path, per_episode_dirs=False)
    queue = json.loads(q.read_text(encoding="utf-8"))
    video, cover = PB.episode_files(q, queue["episodes"][0])
    assert video.is_file() and cover.is_file()


def test_missing_video_is_refused(tmp_path):
    q = make_delivery(tmp_path)
    (tmp_path / "deliver" / "munger" / "ep01" / "final.mp4").unlink()
    queue = json.loads(q.read_text(encoding="utf-8"))
    with pytest.raises(PB.ConfigError, match="找不到成片"):
        PB.episode_files(q, queue["episodes"][0])


# ── dry-run 一次都不投稿 ────────────────────────────────────────────────────

def test_dry_run_prints_the_command_and_uploads_nothing(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(PB.COOKIE_ENV, str(cookie_file(tmp_path)))

    def boom(*a, **k):
        raise AssertionError("dry-run 居然真的调了 biliup")

    monkeypatch.setattr(PB.subprocess, "run", boom)
    q = make_delivery(tmp_path)

    assert PB.main(["--queue", str(q), "--episode", "ep01",
                    "--dry-run"]) == PB.EXIT_OK
    assert "biliup" in capsys.readouterr().out


# ── 投稿上限校验（缺口五）───────────────────────────────────────────────

def test_parse_max_episodes_blank_falls_back_to_default():
    assert PB.parse_max_episodes("") == PB.DEFAULT_MAX_EPISODES
    assert PB.parse_max_episodes(None) == PB.DEFAULT_MAX_EPISODES


def test_parse_max_episodes_default_is_one():
    # 用户明确要求：默认先只投一集。这个断言存在的意义就是为了在有人把
    # DEFAULT_MAX_EPISODES 改大时把测试靠红。
    assert PB.DEFAULT_MAX_EPISODES == 1


def test_parse_max_episodes_accepts_positive_integer_strings():
    assert PB.parse_max_episodes("3") == 3
    assert PB.parse_max_episodes("  7 ") == 7


@pytest.mark.parametrize("raw", ["0", "-1", "-7"])
def test_parse_max_episodes_rejects_non_positive(raw):
    with pytest.raises(PB.ConfigError, match="不允许静默当成"):
        PB.parse_max_episodes(raw)


@pytest.mark.parametrize("raw", ["abc", "1.5", "one", "7集", "inf"])
def test_parse_max_episodes_rejects_non_integers(raw):
    with pytest.raises(PB.ConfigError, match="必须是整数"):
        PB.parse_max_episodes(raw)


# ── 幂等：已投过的集直接跳过 ────────────────────────────────────────

def sample_index(slug="munger", entries=None):
    return {"episodes": entries or []}


def test_already_published_returns_none_when_no_matching_record():
    index = sample_index(entries=[
        {"slug": "munger", "id": "ep01", "publish": {"bilibili": None}}])
    assert PB.already_published(index, "munger", "ep02") is None
    assert PB.already_published(index, "other-slug", "ep01") is None


def test_already_published_returns_none_when_publish_field_empty():
    index = sample_index(entries=[
        {"slug": "munger", "id": "ep01", "publish": {"bilibili": None}},
        {"slug": "munger", "id": "ep02"},  # 没 publish 字段也得算未投过
    ])
    assert PB.already_published(index, "munger", "ep01") is None
    assert PB.already_published(index, "munger", "ep02") is None


def test_already_published_returns_marker_when_present():
    index = sample_index(entries=[
        {"slug": "munger", "id": "ep01",
         "publish": {"bilibili": "BV1abFAKE001"}}])
    assert PB.already_published(index, "munger", "ep01") == "BV1abFAKE001"


def test_main_skips_already_published_episode_without_touching_cookies(
        tmp_path, monkeypatch, capsys):
    # 故意不设定 cookie 环境变量——还能跳过就证明幂等检查在 cookie 解析之前。
    monkeypatch.delenv(PB.COOKIE_ENV, raising=False)
    q = make_delivery(tmp_path)

    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "episodes": [{"slug": "munger", "id": "ep01",
                      "publish": {"bilibili": "BV1abFAKE001"}}]
    }, ensure_ascii=False), encoding="utf-8")

    rc = PB.main(["--queue", str(q), "--episode", "ep01",
                  "--index", str(index_path)])
    assert rc == PB.EXIT_OK
    out = capsys.readouterr().out
    assert "已投过" in out and "跳过" in out


def test_main_does_not_skip_when_not_yet_published(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(PB.COOKIE_ENV, str(cookie_file(tmp_path)))
    q = make_delivery(tmp_path)

    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "episodes": [{"slug": "munger", "id": "ep01", "publish": {}}]
    }, ensure_ascii=False), encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("不该真调 biliup")

    monkeypatch.setattr(PB.subprocess, "run", boom)

    rc = PB.main(["--queue", str(q), "--episode", "ep01",
                  "--index", str(index_path), "--dry-run"])
    assert rc == PB.EXIT_OK
    assert "biliup" in capsys.readouterr().out


# ── bvid 提取：摸得到就用，摸不到诚实说摸不到 ──────────────────────

def test_extract_bvid_finds_a_valid_bvid_in_noisy_output():
    text = "投稿成功！稿件地址：https://www.bilibili.com/video/BV1abFAKE001\n"
    assert PB.extract_bvid(text) == "BV1abFAKE001"


def test_extract_bvid_returns_none_when_absent():
    assert PB.extract_bvid("投稿成功，但输出里啥都没有") is None


def test_extract_bvid_never_fabricates_from_empty_string():
    assert PB.extract_bvid("") is None


# ── 投稿结果写回索引，其余字段保留 ──────────────────────────────

def test_record_publish_result_updates_matching_entry_and_preserves_rest(
        tmp_path):
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "updated_at": "2020-01-01T00:00:00Z",
        "episodes": [
            {"slug": "munger", "id": "ep01", "title": "标题一",
             "status": "delivered", "publish": {"bilibili": None}},
            {"slug": "munger", "id": "ep02", "title": "标题二",
             "status": "delivered", "publish": {"bilibili": None}},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    ok = PB.record_publish_result(index_path, "munger", "ep01",
                                   "BV1abFAKE001")
    assert ok is True

    data = json.loads(index_path.read_text(encoding="utf-8"))
    ep1, ep2 = data["episodes"]
    assert ep1["publish"]["bilibili"] == "BV1abFAKE001"
    assert ep1["status"] == "published"
    assert ep1["title"] == "标题一"  # 其余字段不受影响
    assert ep2["publish"]["bilibili"] is None  # 没动别的集
    assert data["updated_at"] != "2020-01-01T00:00:00Z"  # 时间戳被刷新


def test_record_publish_result_returns_false_when_index_missing(tmp_path):
    missing = tmp_path / "nope.json"
    assert PB.record_publish_result(missing, "munger", "ep01",
                                     "BV1abFAKE001") is False


def test_record_publish_result_returns_false_when_entry_not_found(tmp_path):
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({"episodes": []}), encoding="utf-8")
    assert PB.record_publish_result(index_path, "munger", "ep01",
                                     "BV1abFAKE001") is False


def test_main_writes_bvid_back_to_index_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv(PB.COOKIE_ENV, str(cookie_file(tmp_path)))
    q = make_delivery(tmp_path)

    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "episodes": [{"slug": "munger", "id": "ep01", "status": "delivered",
                      "publish": {"bilibili": None}}]
    }, ensure_ascii=False), encoding="utf-8")

    class FakeResult:
        returncode = 0
        stdout = "投稿成功 https://www.bilibili.com/video/BV1abFAKE002\n"
        stderr = ""

    monkeypatch.setattr(PB.shutil, "which", lambda name: "/usr/bin/biliup")
    monkeypatch.setattr(PB.subprocess, "run", lambda *a, **k: FakeResult())

    rc = PB.main(["--queue", str(q), "--episode", "ep01",
                  "--index", str(index_path)])
    assert rc == PB.EXIT_OK

    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["episodes"][0]["publish"]["bilibili"] == "BV1abFAKE002"
    assert data["episodes"][0]["status"] == "published"


def test_main_warns_but_still_records_when_bvid_cannot_be_found(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(PB.COOKIE_ENV, str(cookie_file(tmp_path)))
    q = make_delivery(tmp_path)

    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps({
        "episodes": [{"slug": "munger", "id": "ep01", "status": "delivered",
                      "publish": {"bilibili": None}}]
    }, ensure_ascii=False), encoding="utf-8")

    class FakeResult:
        returncode = 0
        stdout = "投稿完成，但没有链接\n"
        stderr = ""

    monkeypatch.setattr(PB.shutil, "which", lambda name: "/usr/bin/biliup")
    monkeypatch.setattr(PB.subprocess, "run", lambda *a, **k: FakeResult())

    rc = PB.main(["--queue", str(q), "--episode", "ep01",
                  "--index", str(index_path)])
    assert rc == PB.EXIT_OK
    assert "摸到" in capsys.readouterr().err

    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["episodes"][0]["publish"]["bilibili"].startswith(
        "published-no-bvid-")


def test_error_messages_never_contain_cookie_content(tmp_path, monkeypatch):
    """缺口四回归：不管走哪条报错路径，异常文案里绝不能出现凭据内容。

    这里故意在环境变量里塞一个「看起来像真凭据」的假字面量，确认它不会被
    原样拼进任何 ConfigError 文案（现有报错设计只提到路径/变量名，不提取
    文件内容，所以这条断言应当恒成立；写成测试是为了防止未来有人手滑在
    某条报错分支里 f-string 拼了 cookie 内容进去）。
    """
    fake_secret = "SESSDATA=fake-not-a-real-cookie;bili_jct=also-fake-9999"
    monkeypatch.setenv(PB.COOKIE_ENV, fake_secret)  # 故意传一个不是路径的假货

    with pytest.raises(PB.ConfigError) as exc_info:
        PB.resolve_cookies()

    assert fake_secret not in str(exc_info.value)
    assert "SESSDATA" not in str(exc_info.value)
    assert "bili_jct" not in str(exc_info.value)
