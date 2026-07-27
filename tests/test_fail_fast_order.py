"""封面闸门必须在翻译之前生效（全程 mock，不打真实 SiliconFlow）。

CI run 30259127265 和 30260691746 都是走完翻译、走完 assemble，最后才在封面
阶段按阈值退 2 —— 翻译的钱已经花光，成片也白烧了。这里用「一被调用就炸」的桩
把「封面被拒时 translate 从未被调用」锁死。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                # noqa: E402


def quotes_for(windows):
    return [{"rank": i, "score": 9.0,
             "clip_start_sec": a, "clip_end_sec": b,
             "clip_duration_sec": b - a,
             "title_suggestion": "", "reason": ""}
            for i, (a, b) in enumerate(windows, 1)]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """把 produce.main 接到桩上，返回按发生顺序记录的阶段名。"""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")

    video = tmp_path / "source.mp4"
    video.write_bytes(b"fake")
    srt = tmp_path / "transcript.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nhello\n\n", encoding="utf-8")

    calls = []
    monkeypatch.setattr(produce, "resolve_source", lambda *a, **k: video)
    monkeypatch.setattr(produce, "transcribe", lambda *a, **k: srt)
    monkeypatch.setattr(produce, "probe_duration", lambda p: 600.0)
    monkeypatch.setattr(produce, "pick_quotes",
                        lambda *a, **k: (calls.append("highlight"),
                                         quotes_for([(0.0, 60.0)]))[1])

    def fake_translate_all(*a, **k):
        calls.append("translate")
        return ["译文"]

    monkeypatch.setattr(produce.TR, "translate_all", fake_translate_all)

    def fake_assemble(*a, **k):
        calls.append("assemble")
        return [], []

    monkeypatch.setattr(produce, "assemble", fake_assemble)
    monkeypatch.setattr(produce, "make_title",
                        lambda *a, **k: (calls.append("title"), "标题")[1])
    monkeypatch.setattr(produce, "render_covers",
                        lambda frame, title, speaker, out_dir, report:
                        (calls.append("cover-render"), report)[1])
    monkeypatch.setattr(produce, "write_meta",
                        lambda *a, **k: (calls.append("manifest"),
                                         tmp_path / "meta.json")[1])
    yield calls
    produce.sf_client.configure(
        cache_dir=ROOT / produce.sf_client.DEFAULT_CACHE_DIRNAME,
        cache_enabled=True,
        max_retries=produce.sf_client.DEFAULT_MAX_RETRIES)


def _args(tmp_path, *extra):
    return ["--source", "https://example.com/v", "--slug", "s",
            "--work", str(tmp_path / "w"), "--out", str(tmp_path / "o"),
            "--llm-cache-dir", str(tmp_path / "llmcache"), *extra]


# ── 本次改动的核心收益 ───────────────────────────────────────────────────────

def test_cover_rejection_never_reaches_translate(wired, tmp_path, monkeypatch,
                                                 capsys):
    """封面被拒时 translate 一次都不能被调用 —— 翻译是主要开销。"""
    def no_frame(**kw):
        kw["report"]["cover_vlm_rejections"] = [
            {"time_sec": 12.0, "person": "双人", "cover_score": 4,
             "reason": "均在背景中"}]
        return None

    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision", no_frame)

    with pytest.raises(SystemExit) as e:
        produce.main(_args(tmp_path))

    assert e.value.code == produce.EXIT_QUALITY
    assert "translate" not in wired, f"翻译被调用了，实际阶段顺序：{wired}"
    assert "assemble" not in wired
    assert wired == ["highlight"]

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["stage"] == "cover"
    assert payload["reason"] == "no_frame_passed_vlm"


def test_manual_cover_time_rejection_also_precedes_translate(
        wired, tmp_path, monkeypatch):
    """手动钉帧那条路径同样要提前：截不出帧也不能先把翻译钱花掉。"""
    monkeypatch.setattr(produce.COVER, "extract_frame",
                        lambda v, t, o, crop=None: False)

    with pytest.raises(SystemExit) as e:
        produce.main(_args(tmp_path, "--cover-time-sec", "99999"))

    assert e.value.code == produce.EXIT_CONFIG
    assert "translate" not in wired
    assert wired == ["highlight"]


def test_geometric_rejection_also_precedes_translate(wired, tmp_path,
                                                     monkeypatch):
    def reject(**kw):
        produce.COVER.reject_cover("no_frame_meets_geometry", candidates=8)

    monkeypatch.setattr(produce.COVER, "pick_best_frame_geometric", reject)

    with pytest.raises(SystemExit) as e:
        produce.main(_args(tmp_path, "--no-vlm"))

    assert e.value.code == produce.EXIT_QUALITY
    assert "translate" not in wired


# ── 顺利路径的阶段顺序 ───────────────────────────────────────────────────────

def test_happy_path_runs_cover_select_before_translate(wired, tmp_path,
                                                       monkeypatch):
    frame = tmp_path / "picked.jpg"
    frame.write_bytes(b"jpeg")

    def pick(**kw):
        wired.append("cover-select")
        return str(frame)

    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision", pick)

    assert produce.main(_args(tmp_path)) == produce.EXIT_OK
    assert wired == ["highlight", "cover-select", "translate", "assemble",
                     "title", "cover-render", "manifest"]


def test_stage_plan_is_visible_in_the_log(wired, tmp_path, monkeypatch, capsys):
    frame = tmp_path / "picked.jpg"
    frame.write_bytes(b"jpeg")
    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision",
                        lambda **kw: str(frame))

    produce.main(_args(tmp_path))
    out = capsys.readouterr().out

    assert "[stage] 阶段顺序：" in out
    assert out.index("cover-select ——") < out.index("translate ——")


def test_stage_order_lists_cover_select_before_translate():
    names = [n for n, _ in produce.STAGE_ORDER]
    assert names.index("cover-select") < names.index("translate")
    assert names.index("title") < names.index("cover-render")


# ── 出图这一步仍然拿到 title 阶段产出的标题 ─────────────────────────────────

def test_render_covers_burns_the_title_from_the_title_stage(tmp_path,
                                                            monkeypatch):
    seen = []
    monkeypatch.setattr(produce.COVER, "make_cover",
                        lambda frame, title, speaker, path, size:
                        (seen.append((title, speaker, size)),
                         Path(path).write_bytes(b"jpeg")))

    report = produce.render_covers("f.jpg", "芒格谈耐心", "芒格",
                                   tmp_path / "out", {"cover_crop": None})

    assert [t for t, _, _ in seen] == ["芒格谈耐心", "芒格谈耐心"]
    assert set(report["files"]) == {"cover_16x9.jpg", "cover_9x16.jpg"}
    assert all(p.is_file() for p in report["files"].values())
