"""B 站投稿预留接口（scripts/publish_bilibili.py）。

这条链路现在默认关闭 —— workflow 那一步的条件是 BILIBILI_COOKIES 非空，而这个
secret 不存在。这里只测参数拼装，以及最要紧的一条：cookie 缺失时必须明确报错，
不能静默「成功」让人以为片子发出去了。

这些测试不需要任何 secret，也一次都不会真的投稿。
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_missing_cover_is_refused(tmp_path):
    """B 站投稿必须带封面，缺了要在调 biliup 之前就报错。"""
    q = make_delivery(tmp_path)
    (tmp_path / "deliver" / "munger" / "ep01" / "cover_16x9.jpg").unlink()
    queue = json.loads(q.read_text(encoding="utf-8"))
    with pytest.raises(PB.ConfigError, match="找不到封面"):
        PB.episode_files(q, queue["episodes"][0])


def test_missing_queue_file_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(PB.COOKIE_ENV, str(cookie_file(tmp_path)))
    missing = tmp_path / "nope.json"

    assert PB.main(["--queue", str(missing),
                    "--episode", "ep01"]) == PB.EXIT_CONFIG
    assert "nope.json" in capsys.readouterr().err


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


# ── 真投那条路径的退出码（biliup 全程是桩，不会发出任何请求）────────────────

def ready(tmp_path, monkeypatch, returncode):
    """把 biliup 换成一个只记参数、返回指定退出码的桩。"""
    monkeypatch.setenv(PB.COOKIE_ENV, str(cookie_file(tmp_path)))
    monkeypatch.setattr(PB.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen = []
    monkeypatch.setattr(PB.subprocess, "run",
                        lambda cmd: (seen.append(cmd),
                                     SimpleNamespace(returncode=returncode))[1])
    return make_delivery(tmp_path), seen


def test_biliup_is_not_installed_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(PB.COOKIE_ENV, str(cookie_file(tmp_path)))
    monkeypatch.setattr(PB.shutil, "which", lambda name: None)

    def boom(*a, **k):
        raise AssertionError("没装 biliup 还是把它调起来了")

    monkeypatch.setattr(PB.subprocess, "run", boom)
    q = make_delivery(tmp_path)

    assert PB.main(["--queue", str(q), "--episode", "ep01"]) == PB.EXIT_CONFIG
    assert "biliup" in capsys.readouterr().err


def test_biliup_failure_is_an_api_error(tmp_path, monkeypatch):
    """biliup 退非零 → 退 3（外部 API 失败），不能当成投稿成功。"""
    q, seen = ready(tmp_path, monkeypatch, returncode=7)
    assert PB.main(["--queue", str(q), "--episode", "ep01"]) == PB.EXIT_API
    assert seen and seen[0][0] == "biliup"


def test_biliup_success_exits_zero(tmp_path, monkeypatch):
    q, seen = ready(tmp_path, monkeypatch, returncode=0)
    assert PB.main(["--queue", str(q), "--episode", "ep01"]) == PB.EXIT_OK
    assert len(seen) == 1
