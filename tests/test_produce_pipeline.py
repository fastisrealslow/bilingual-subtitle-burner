"""produce.py 的流水线接线（不打真实 SiliconFlow）。

这里覆盖两件在单模块测试里看不出来的事：译文的标点归一化确实接进了
翻译这一步（`『不行』` 不能穿透到烧字幕），以及 assemble 真的能用 ffmpeg
把三段拼成一条 final.mp4。
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                   # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def write_srt(path: Path, cues) -> Path:
    def ts(sec):
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int(sec % 1 * 1000):03d}"

    blocks = [f"{i}\n{ts(a)} --> {ts(b)}\n{t}\n"
              for i, (a, b, t) in enumerate(cues, 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def quotes_for(windows):
    return [{"rank": i, "score": 9.0,
             "clip_start_sec": a, "clip_end_sec": b,
             "clip_duration_sec": b - a,
             "title_suggestion": "", "reason": ""}
            for i, (a, b) in enumerate(windows, 1)]


# ── 翻译这一步的标点归一化 ──────────────────────────────────────────────────

def test_translate_normalizes_outer_white_brackets(tmp_path, monkeypatch):
    """DeepSeek-V3 吐出的外层『』必须在写盘前被纠成「」。"""
    srt = write_srt(tmp_path / "t.srt", [
        (0.0, 5.0, "I called Munger and he just said no."),
        (5.0, 10.0, "That was the whole conversation."),
    ])
    # 模拟 DeepSeek-V3 的实际输出：最外层用了『』
    monkeypatch.setattr(produce.TR, "translate_all",
                        lambda *a, **k: ["我给芒格打电话 他只说『不行』",
                                         "整场对话就这么多"])

    out = produce.translate_windows(srt, quotes_for([(0.0, 10.0)]), tmp_path,
                                    "deepseek-v3", "sk-test", "https://x/v1")
    bi = json.loads(out.read_text(encoding="utf-8"))

    assert bi[0]["zh"] == "我给芒格打电话 他只说「不行」"
    assert "『" not in bi[0]["zh"]
    assert bi[0]["en"] == "I called Munger and he just said no."


def test_translate_keeps_nested_white_brackets(tmp_path, monkeypatch):
    srt = write_srt(tmp_path / "t.srt", [(0.0, 5.0, "He said Charlie just said no.")])
    monkeypatch.setattr(produce.TR, "translate_all",
                        lambda *a, **k: ["他说「查理只说『不』」"])

    out = produce.translate_windows(srt, quotes_for([(0.0, 5.0)]), tmp_path,
                                    "deepseek-v3", "sk-test", "https://x/v1")
    assert json.loads(out.read_text(encoding="utf-8"))[0]["zh"] == "他说「查理只说『不』」"


def test_translate_only_covers_selected_windows(tmp_path, monkeypatch):
    srt = write_srt(tmp_path / "t.srt", [
        (0.0, 5.0, "inside one"),
        (100.0, 105.0, "far away, should be skipped"),
        (200.0, 205.0, "inside two"),
    ])
    seen = {}

    def fake(texts, *a, **k):
        seen["texts"] = texts
        return ["译文" for _ in texts]

    monkeypatch.setattr(produce.TR, "translate_all", fake)
    produce.translate_windows(srt, quotes_for([(0.0, 10.0), (195.0, 210.0)]),
                              tmp_path, "deepseek-v3", "sk-test", "https://x/v1")

    assert seen["texts"] == ["inside one", "inside two"]


def test_translate_api_failure_exits_three(tmp_path, monkeypatch, capsys):
    srt = write_srt(tmp_path / "t.srt", [(0.0, 5.0, "hello")])

    def boom(*a, **k):
        raise RuntimeError("硅基流动全部模型均不可用")

    monkeypatch.setattr(produce.TR, "translate_all", boom)
    with pytest.raises(SystemExit) as e:
        produce.translate_windows(srt, quotes_for([(0.0, 5.0)]), tmp_path,
                                  "deepseek-v3", "sk-test", "https://x/v1")

    assert e.value.code == produce.EXIT_API
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "siliconflow_unavailable"


# ── 金句门槛在 produce 侧确实生效 ───────────────────────────────────────────

def test_short_source_is_rejected_with_exit_two(tmp_path, monkeypatch, capsys):
    srt = write_srt(tmp_path / "t.srt", [(0.0, 20.0, "a"), (20.0, 40.0, "b")])
    monkeypatch.setattr(produce.HL, "score_highlights",
                        lambda *a, **k: quotes_for([(0.0, 20.0), (20.0, 40.0)]))
    monkeypatch.setattr(produce.HL, "align_clips", lambda items, *a, **k: items)

    with pytest.raises(SystemExit) as e:
        produce.pick_quotes(srt, tmp_path, "芒格", 40.0, "sk-test",
                            "https://x/v1", strict=True)

    assert e.value.code == produce.EXIT_QUALITY
    assert json.loads(capsys.readouterr().err.strip())["stage"] == "highlight"


# ── 配置校验 ────────────────────────────────────────────────────────────────

def test_missing_api_key_exits_one(monkeypatch, capsys):
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "x.mp4", "--slug", "s"])
    assert e.value.code == produce.EXIT_CONFIG
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "missing_siliconflow_api_key"


def test_bad_slug_exits_one(monkeypatch, capsys):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "x.mp4", "--slug", "../etc/passwd"])
    assert e.value.code == produce.EXIT_CONFIG
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "invalid_slug"


def test_missing_local_source_exits_one(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        produce.resolve_source(str(tmp_path / "nope.mp4"), tmp_path)
    assert e.value.code == produce.EXIT_CONFIG
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "source_not_found"


def test_file_url_resolves_to_local_path(tmp_path):
    real = tmp_path / "clip.mp4"
    real.write_bytes(b"stub")
    assert produce.resolve_source(f"file://{real}", tmp_path) == real


# ── 真跑一遍 ffmpeg：切三段 → 烧字幕 → concat ────────────────────────────────

@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_assemble_concats_three_segments(tmp_path):
    video = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x180:d=60",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(video)],
        check=True, capture_output=True)

    cues = [(float(i * 5), float(i * 5 + 5), f"line {i}") for i in range(12)]
    srt = write_srt(tmp_path / "t.srt", cues)

    entries = produce.HL.parse_srt(str(srt))
    bilingual = tmp_path / "bi.json"
    bilingual.write_text(json.dumps(
        [{"index": e["index"], "start": e["start"], "end": e["end"],
          "en": e["text"], "zh": f"他只说「不行」{e['index']}"} for e in entries],
        ensure_ascii=False), encoding="utf-8")

    quotes = quotes_for([(0.0, 15.0), (20.0, 35.0), (40.0, 55.0)])
    final = tmp_path / "out" / "final.mp4"
    segs = produce.assemble(video, srt, bilingual, quotes, tmp_path, final)

    assert final.is_file() and final.stat().st_size > 0
    assert [s["index"] for s in segs] == [1, 2, 3]
    assert all(s["cues"] > 0 for s in segs)
    # 三段各 15s，concat 后应当接近 45s
    assert 40 < produce.probe_duration(final) < 50


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_sha256_and_meta_structure(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"not really a video")
    meta_path = produce.write_meta(
        tmp_path, "test-slug", "芒格谈耐心", "https://example.com/v",
        final, [{"index": 1, "duration_sec": 60.0}],
        {"cover_vlm_passed": False, "cover_vlm_rejections": [{"reason": "no_face"}],
         "files": {}},
        "deepseek-v3", "芒格")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["slug"] == "test-slug"
    assert meta["title"] == "芒格谈耐心"
    assert meta["source_url"] == "https://example.com/v"
    assert meta["models"]["translate"] == "deepseek-ai/DeepSeek-V3"
    assert meta["cover_vlm_passed"] is False
    assert meta["cover_vlm_rejections"] == [{"reason": "no_face"}]
    assert meta["commit"]
    assert len(meta["sha256"]["final.mp4"]) == 64
