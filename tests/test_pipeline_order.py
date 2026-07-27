"""阶段顺序（封面先于翻译）与 yt-dlp 下载的分类重试。

本文件最核心的一条是 ``test_cover_rejection_never_reaches_translate``：封面被
拒时翻译必须一次都没被调用过。CI run 30259127265 / 30260691746 都是走完翻译、
走完 assemble 才卡在封面阈值上退 2 —— 钱已经花光，成片也白烧。用 mock 计数把
这个顺序锁死，以后谁把封面挪回翻译后面都会红。

全部用 mock：不下载、不转写、不打 SiliconFlow。
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                 # noqa: E402
import sf_client                               # noqa: E402

QUOTES = [{"rank": i, "score": 9.0, "clip_start_sec": 10.0 * i,
           "clip_end_sec": 10.0 * i + 60.0, "transcript_zh": "金句",
           "transcript_en": "quote", "reason": "好"}
          for i in (1, 2, 3)]


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """把八步流水线换成「只记名字」的替身，返回调用顺序列表。

    封面那一步保留真身：本文件要断言的正是它的拒绝时机。
    """
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr(produce, "_sleep", lambda s: None)
    sf_client.configure(cache_dir=None, cache_enabled=False)

    order = []

    def record(name, result):
        def fake(*a, **k):
            order.append(name)
            return result
        return fake

    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake mp4")
    srt = tmp_path / "transcript.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\nhi\n\n", encoding="utf-8")

    monkeypatch.setattr(produce, "resolve_source", record("input", video))
    monkeypatch.setattr(produce, "transcribe", record("transcribe", srt))
    monkeypatch.setattr(produce, "probe_duration", lambda p: 600.0)
    monkeypatch.setattr(produce, "pick_quotes", record("highlight", QUOTES))
    monkeypatch.setattr(produce, "make_title", record("title", "标题"))
    monkeypatch.setattr(produce, "translate_windows",
                        record("translate", tmp_path / "bilingual.json"))
    monkeypatch.setattr(produce, "assemble", record("assemble", ([], [])))
    monkeypatch.setattr(produce, "write_meta", record("manifest", None))

    # 封面的 I/O 侧（截帧、出图）打桩，但选帧判定与阈值逻辑保持真身
    monkeypatch.setattr(produce.COVER, "extract_frame",
                        lambda *a, **k: True)
    monkeypatch.setattr(produce.COVER, "make_cover", lambda *a, **k: None)

    def argv(*extra):
        return ["--source", str(video), "--slug", "t",
                "--out", str(tmp_path / "deliver"),
                "--work", str(tmp_path / "work"),
                "--no-llm-cache", *extra]

    return order, argv


# ── 核心：封面被拒 → 翻译一次都没被调用 ────────────────────────────────────────

def test_cover_rejection_never_reaches_translate(pipeline, monkeypatch, capsys):
    """封面挑不出合格帧时，翻译的钱一分都不该花出去。

    CI run 30259127265 / 30260691746 的原样复现：VLM 一帧都没判合格。
    """
    order, argv = pipeline
    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision",
                        lambda **k: None)

    with pytest.raises(SystemExit) as e:
        produce.main(argv())

    assert e.value.code == produce.EXIT_QUALITY
    assert "translate" not in order, "封面被拒之后不该再调用翻译"
    assert "assemble" not in order
    assert order == ["input", "transcribe", "highlight", "title"]
    assert '"reason": "no_frame_passed_vlm"' in capsys.readouterr().err


def test_geometric_rejection_also_precedes_translate(pipeline, monkeypatch,
                                                     capsys):
    """``--no-vlm`` 路径同样要在翻译之前判死。"""
    order, argv = pipeline
    monkeypatch.setattr(
        produce.COVER, "pick_best_frame_geometric",
        lambda **k: produce.COVER.reject_cover("no_frame_meets_geometry"))

    with pytest.raises(SystemExit) as e:
        produce.main(argv("--no-vlm"))

    assert e.value.code == produce.EXIT_QUALITY
    assert "translate" not in order
    assert '"reason": "no_frame_meets_geometry"' in capsys.readouterr().err


def test_happy_path_runs_cover_before_translate(pipeline, monkeypatch):
    order, argv = pipeline
    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision",
                        lambda **k: "frame.jpg")

    assert produce.main(argv()) == produce.EXIT_OK
    assert order == ["input", "transcribe", "highlight", "title",
                     "translate", "assemble", "manifest"]


def test_manual_cover_pin_is_also_evaluated_before_translate(pipeline,
                                                             monkeypatch):
    """``--cover-time-sec`` 的人工出口也在翻译前面：截不出帧就当场退 1。"""
    order, argv = pipeline
    monkeypatch.setattr(produce.COVER, "extract_frame", lambda *a, **k: False)

    with pytest.raises(SystemExit) as e:
        produce.main(argv("--cover-time-sec", "42"))

    assert e.value.code == produce.EXIT_CONFIG
    assert "translate" not in order


def test_stage_order_constant_matches_the_documented_order():
    assert produce.STAGE_ORDER == ("input", "transcribe", "highlight", "title",
                                   "cover", "translate", "assemble", "manifest")
    assert (produce.STAGE_ORDER.index("cover")
            < produce.STAGE_ORDER.index("translate"))


def test_stage_order_is_printed_and_in_help(pipeline, monkeypatch, capsys):
    order, argv = pipeline
    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision",
                        lambda **k: "frame.jpg")
    produce.main(argv())
    assert produce.STAGE_ORDER_TEXT in capsys.readouterr().out

    with pytest.raises(SystemExit):
        produce.main(["--help"])
    assert produce.STAGE_ORDER_TEXT in capsys.readouterr().out


# ── 下载重试 ──────────────────────────────────────────────────────────────────

class FakeRun:
    """按脚本依次返回 yt-dlp 的退出结果，并记录每次调用的命令。"""

    def __init__(self, results, out=None):
        self.results = list(results)
        self.cmds = []
        self.out = out

    def __call__(self, cmd, capture_output=False, text=False):
        self.cmds.append(cmd)
        rc, err = self.results.pop(0) if self.results else (1, "boom")
        if rc == 0 and self.out is not None:
            self.out.write_bytes(b"video")
        return subprocess.CompletedProcess(cmd, rc, "", err)


@pytest.fixture
def dl(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(produce, "_sleep", slept.append)
    return tmp_path / "source.mp4", slept


def test_http_500_is_retried(dl, monkeypatch):
    """CI run 30263087066：archive.org 返 500，人手重跑就过了。"""
    out, slept = dl
    fake = FakeRun([(1, "ERROR: unable to download: HTTP Error 500"),
                    (1, "ERROR: unable to download: HTTP Error 500"),
                    (0, "")], out=out)
    monkeypatch.setattr(subprocess, "run", fake)

    produce.download_source("https://archive.org/x", out, attempts=3,
                            backoff=2.0)

    assert len(fake.cmds) == 3
    assert slept == [2.0, 4.0]          # 指数退避


def test_http_404_is_not_retried(dl, monkeypatch, capsys):
    out, slept = dl
    fake = FakeRun([(1, "ERROR: HTTP Error 404: Not Found")])
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(SystemExit) as e:
        produce.download_source("https://x/gone", out, attempts=5, backoff=2.0)

    assert e.value.code == produce.EXIT_API
    assert len(fake.cmds) == 1, "404 重试纯属浪费时间"
    assert slept == []


def test_unsupported_url_fails_as_a_config_error(dl, monkeypatch):
    out, slept = dl
    fake = FakeRun([(1, "ERROR: Unsupported URL: htp://typo")])
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(SystemExit) as e:
        produce.download_source("htp://typo", out, attempts=5, backoff=2.0)

    assert e.value.code == produce.EXIT_CONFIG
    assert len(fake.cmds) == 1
    assert slept == []


def test_retries_are_exhausted_into_exit_3(dl, monkeypatch):
    out, slept = dl
    fake = FakeRun([(1, "HTTP Error 503")] * 3)
    monkeypatch.setattr(subprocess, "run", fake)

    with pytest.raises(SystemExit) as e:
        produce.download_source("https://x/y", out, attempts=3, backoff=1.0)

    assert e.value.code == produce.EXIT_API
    assert len(fake.cmds) == 3
    assert len(slept) == 2


def test_backoff_is_capped(dl, monkeypatch):
    out, slept = dl
    monkeypatch.setattr(subprocess, "run", FakeRun([(1, "HTTP Error 502")] * 6))

    with pytest.raises(SystemExit):
        produce.download_source("https://x/y", out, attempts=6, backoff=30.0)

    assert max(slept) <= produce.MAX_DOWNLOAD_BACKOFF_SEC


def test_ytdlp_gets_its_own_fragment_retries(dl, monkeypatch):
    """分片级抖动让 yt-dlp 自己吞掉，不必退到外层从头下。"""
    out, _ = dl
    fake = FakeRun([(0, "")], out=out)
    monkeypatch.setattr(subprocess, "run", fake)

    produce.download_source("https://x/y", out, attempts=3, backoff=1.0)

    cmd = fake.cmds[0]
    assert cmd[cmd.index("--retries") + 1] == str(produce.YTDLP_RETRIES)
    assert (cmd[cmd.index("--fragment-retries") + 1]
            == str(produce.YTDLP_FRAGMENT_RETRIES))


@pytest.mark.parametrize("output,verdict", [
    ("ERROR: HTTP Error 500: Internal Server Error", "retry"),
    ("ERROR: HTTP Error 503", "retry"),
    ("ERROR: The read operation timed out", "retry"),
    ("ERROR: Connection reset by peer", "retry"),
    ("ERROR: HTTP Error 404: Not Found", "gone"),
    ("ERROR: Video unavailable", "gone"),
    ("ERROR: This video is private", "gone"),
    ("ERROR: Unsupported URL: foo", "config"),
    ("ERROR: 谁也没见过的报错", "retry"),      # 认不出来就当抖动，多试两次更划算
])
def test_classify_download_failure(output, verdict):
    assert produce.classify_download_failure(output)[0] == verdict
