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
    segs, placements = produce.assemble(
        video, srt, bilingual, quotes, tmp_path, final)

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


# ── 手动指定封面时间点 ──────────────────────────────────────────────────────
# 有些源片天生没有合格的人物封面帧：解说式剪辑是原声 + 素材空镜，全片没有
# 主讲人正脸，自动选帧再怎么调也只会挑到不相干的素材人物。

def test_cover_time_sec_skips_face_filter_and_vlm(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("手动指定封面时不该再走自动选帧")

    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision", boom)
    monkeypatch.setattr(produce.COVER, "pick_best_frame_geometric", boom)
    monkeypatch.setattr(produce.COVER, "filter_frames_by_face", boom)
    monkeypatch.setattr(produce.COVER, "call_vision_llm", boom)

    grabbed = {}

    def fake_extract(video, t, out, crop=None):
        grabbed["t"] = t
        Path(out).write_bytes(b"jpeg")
        return True

    monkeypatch.setattr(produce.COVER, "extract_frame", fake_extract)

    _, report = produce.select_cover_frame(
        Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
        "sk-test", no_vlm=False, cover_time_sec=96.0)

    assert grabbed["t"] == 96.0
    assert report["cover_source"] == "manual"
    assert report["cover_time_sec"] == 96.0
    assert report["cover_vlm_passed"] is False


def test_cover_time_sec_works_without_api_key(tmp_path, monkeypatch):
    # 手动路径不调 VLM，就不该再强制要求 SILICONFLOW_API_KEY
    monkeypatch.setattr(produce.COVER, "extract_frame",
                        lambda v, t, o, crop=None: (Path(o).write_bytes(b"jpeg"), True)[1])

    _, report = produce.select_cover_frame(
        Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
        api_key="", no_vlm=False, cover_time_sec=96.0)
    assert report["cover_source"] == "manual"


def test_cover_time_sec_out_of_range_exits_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(produce.COVER, "extract_frame", lambda v, t, o, crop=None: False)

    with pytest.raises(SystemExit) as e:
        produce.select_cover_frame(
            Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
            "sk-test", no_vlm=False, cover_time_sec=99999.0)

    assert e.value.code == produce.EXIT_CONFIG
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "manual_frame_extract_failed"


def test_auto_path_records_cover_source_auto(tmp_path, monkeypatch):
    frame = tmp_path / "picked.jpg"
    frame.write_bytes(b"jpeg")
    monkeypatch.setattr(produce.COVER, "pick_best_frame_geometric",
                        lambda **k: str(frame))

    _, report = produce.select_cover_frame(
        Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
        "sk-test", no_vlm=True)
    assert report["cover_source"] == "auto"


def test_cover_candidates_is_passed_through_to_picker(tmp_path, monkeypatch):
    frame = tmp_path / "picked.jpg"
    frame.write_bytes(b"jpeg")
    seen = {}

    def fake_pick(**kw):
        seen.update(kw)
        return str(frame)

    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision", fake_pick)
    produce.select_cover_frame(
        Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
        "sk-test", no_vlm=False, cover_time_sec=None, candidates=32)
    assert seen["candidates"] == 32


def test_vlm_failure_in_produce_carries_hint(tmp_path, monkeypatch, capsys):
    def no_frame(**kw):
        kw["report"]["cover_vlm_rejections"] = [
            {"time_sec": 631.1, "person": "双人", "cover_score": 4,
             "reason": "均在背景中"}]
        return None

    monkeypatch.setattr(produce.COVER, "pick_best_frame_vision", no_frame)

    with pytest.raises(SystemExit) as e:
        produce.select_cover_frame(
            Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
            "sk-test", no_vlm=False)

    assert e.value.code == produce.EXIT_QUALITY
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "no_frame_passed_vlm"
    assert payload["best_score"] == 4
    assert "--cover-time-sec" in payload["hint"]


def test_cli_exposes_cover_flags():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s",
                               "--cover-time-sec", "96", "--cover-candidates", "30"])
    assert args.cover_time_sec == 96.0
    assert args.cover_candidates == 30


def test_cover_flags_default_to_auto():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s"])
    assert args.cover_time_sec is None
    assert args.cover_candidates == produce.COVER.DEFAULT_COVER_CANDIDATES
    assert args.cover_crop is None


# ── 封面裁切 ────────────────────────────────────────────────────────────────
# 源片底部烧死的英文硬字幕不裁就会留在成品封面上。

def test_cover_crop_reaches_manual_extract(tmp_path, monkeypatch):
    seen = {}

    def fake_extract(video, t, out, crop=None):
        seen["crop"] = crop
        Path(out).write_bytes(b"jpeg")
        return True

    monkeypatch.setattr(produce.COVER, "extract_frame", fake_extract)
    _, report = produce.select_cover_frame(
        Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
        "sk-test", no_vlm=False, cover_time_sec=96.0,
        cover_crop="854:396:0:0")

    assert seen["crop"] == "854:396:0:0"
    assert report["cover_crop"] == "854:396:0:0"


@pytest.mark.parametrize("picker,no_vlm", [
    ("pick_best_frame_geometric", True),
    ("pick_best_frame_vision", False),
])
def test_cover_crop_reaches_both_pickers(tmp_path, monkeypatch, picker, no_vlm):
    # 候选帧和成品封面必须共用同一个 crop，否则预筛看到的不是最终画面
    frame = tmp_path / "picked.jpg"
    frame.write_bytes(b"jpeg")
    seen = {}

    def fake_pick(**kw):
        seen.update(kw)
        return str(frame)

    monkeypatch.setattr(produce.COVER, picker, fake_pick)
    produce.select_cover_frame(
        Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
        "sk-test", no_vlm=no_vlm, cover_crop="854:396:0:0")
    assert seen["crop"] == "854:396:0:0"


def test_cover_crop_defaults_to_none_in_report(tmp_path, monkeypatch):
    frame = tmp_path / "picked.jpg"
    frame.write_bytes(b"jpeg")
    monkeypatch.setattr(produce.COVER, "pick_best_frame_geometric",
                        lambda **k: str(frame))

    _, report = produce.select_cover_frame(
        Path("v.mp4"), quotes_for([(0.0, 60.0)]), "芒格", tmp_path / "work",
        "sk-test", no_vlm=True)
    assert report["cover_crop"] is None


def test_cli_accepts_cover_crop():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s",
                               "--cover-crop", "854:396:0:0"])
    assert args.cover_crop == "854:396:0:0"


@pytest.mark.parametrize("bad", ["854x396", "854:396:0", "854:396:0:0:0",
                                 "-1:396:0:0", "宽:高:0:0"])
def test_malformed_cover_crop_exits_one(monkeypatch, capsys, bad):
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "x.mp4", "--slug", "s", "--cover-crop", bad])

    assert e.value.code == produce.EXIT_CONFIG
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "invalid_cover_crop"
    assert "W:H:X:Y" in payload["detail"]


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_meta_records_cover_crop(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"stub")
    meta_path = produce.write_meta(
        tmp_path, "munger", "标题", "https://example.com/v", final, [],
        {"cover_crop": "854:396:0:0", "files": {}}, "deepseek-v3", "芒格")
    assert json.loads(meta_path.read_text(encoding="utf-8"))["cover_crop"] == "854:396:0:0"


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_meta_cover_crop_is_null_when_not_given(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"stub")
    meta_path = produce.write_meta(
        tmp_path, "munger", "标题", "https://example.com/v", final, [],
        {"files": {}}, "deepseek-v3", "芒格")
    assert json.loads(meta_path.read_text(encoding="utf-8"))["cover_crop"] is None


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_meta_records_manual_cover_source(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"stub")
    meta_path = produce.write_meta(
        tmp_path, "munger", "标题", "https://example.com/v", final, [],
        {"cover_source": "manual", "cover_time_sec": 96.0,
         "cover_vlm_passed": False, "files": {}},
        "deepseek-v3", "芒格")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["cover_source"] == "manual"
    assert meta["cover_time_sec"] == 96.0
    assert meta["models"]["vision"] is None      # 手动路径没调 vision


# ── zh-only：只烧中文，压在源片硬字幕上方 ────────────────────────────────────
# CI run 30261816842 的成片抽帧目视：源片自带的英文硬字幕 + 我们烧的 EN + ZH
# 三层叠在一起，完全没法看。源片那条英文直接当英文轨用就够了。

def _assemble_fixture(tmp_path, video):
    srt = write_srt(tmp_path / "t.srt", [(0.0, 5.0, "Patience is not a virtue.")])
    entries = produce.HL.parse_srt(str(srt))
    bilingual = tmp_path / "bi.json"
    bilingual.write_text(json.dumps(
        [{"index": e["index"], "start": e["start"], "end": e["end"],
          "en": e["text"], "zh": "耐心不是美德"} for e in entries],
        ensure_ascii=False), encoding="utf-8")
    return srt, bilingual, quotes_for([(0.0, 5.0)])


def _captured_make_ass(monkeypatch, tmp_path):
    """拦下 make_ass 的入参，同时仍然写出真 ASS 供后续步骤使用。"""
    seen = {}
    real = produce.make_ass

    def spy(cues, path, **kw):
        seen.update(kw)
        seen["path"] = path
        return real(cues, path, **kw)

    monkeypatch.setattr(produce, "make_ass", spy)
    return seen


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_zh_only_drops_the_english_track_from_the_ass(tmp_path, monkeypatch):
    video = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=854x480:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", str(video)],
        check=True, capture_output=True)

    seen = _captured_make_ass(monkeypatch, tmp_path)
    srt, bilingual, quotes = _assemble_fixture(tmp_path, video)
    produce.assemble(video, srt, bilingual, quotes, tmp_path,
                     tmp_path / "out" / "final.mp4",
                     sub_mode="zh-only", sub_margin_v=110)

    assert seen["sub_mode"] == "zh_only"
    assert seen["zh_margin_v"] == 110
    ass = Path(seen["path"]).read_text(encoding="utf-8-sig")
    zh = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: ")]
    assert zh and all(",ZH," in ln for ln in zh)
    assert not any(",EN," in ln for ln in zh)
    assert zh[0].split(",", 8)[7] == "110"


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_both_mode_still_burns_bilingual(tmp_path, monkeypatch):
    """回归保护：默认 both 的产出必须和改动前一模一样。"""
    video = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=854x480:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", str(video)],
        check=True, capture_output=True)

    seen = _captured_make_ass(monkeypatch, tmp_path)
    srt, bilingual, quotes = _assemble_fixture(tmp_path, video)
    produce.assemble(video, srt, bilingual, quotes, tmp_path,
                     tmp_path / "out" / "final.mp4")

    assert seen["sub_mode"] == "bilingual"
    assert seen["zh_margin_v"] is None
    ass = Path(seen["path"]).read_text(encoding="utf-8-sig")
    dialogues = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: ")]
    assert any(",EN," in ln for ln in dialogues)
    assert any(",ZH," in ln for ln in dialogues)
    # 改动前中文逐条 MarginV 就是 0（沿用样式值），不能被新分支改掉
    zh = next(ln for ln in dialogues if ",ZH," in ln)
    assert zh.split(",", 8)[7] == "0"


def test_cli_sub_mode_defaults_to_both():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s"])
    assert args.sub_mode == "both"
    # 默认改成自动挡：固定值躲不开源片大字号引言板那种更高的硬字幕
    assert args.sub_margin_v == produce.SUB_MARGIN_V_AUTO
    assert args.sub_avoid_gap == produce.DEFAULT_SUB_AVOID_GAP


def test_cli_accepts_zh_only():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s",
                               "--sub-mode", "zh-only", "--sub-margin-v", "110"])
    assert args.sub_mode == "zh-only"
    assert args.sub_margin_v == 110


def test_cli_rejects_unknown_sub_mode():
    with pytest.raises(SystemExit):
        produce.parse_args(["--source", "v.mp4", "--slug", "s",
                            "--sub-mode", "zh_only"])


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_meta_records_sub_mode(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"stub")
    meta_path = produce.write_meta(
        tmp_path, "munger", "标题", "https://example.com/v", final, [],
        {"files": {}}, "deepseek-v3", "芒格", "zh-only", 110)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["sub_mode"] == "zh-only"
    assert meta["sub_margin_v"] == 110


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_meta_defaults_to_both(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"stub")
    meta_path = produce.write_meta(
        tmp_path, "munger", "标题", "https://example.com/v", final, [],
        {"files": {}}, "deepseek-v3", "芒格")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["sub_mode"] == "both"
    assert meta["sub_margin_v"] == produce.DEFAULT_SUB_MARGIN_V


# ── 真跑 ffmpeg：中文必须落在源片硬字幕带之上 ────────────────────────────────
# 单看 ASS 文本只能证明 MarginV 写对了，证明不了 libass 渲出来的像素真的躲开了
# 那条带子。这条测试造一个 854x480、y=408~456 有白色硬字幕带的源片，烧完之后
# 直接数像素。

HARDSUB_TOP, HARDSUB_BOTTOM = 408, 456


def _make_hardsubbed_source(path: Path) -> None:
    """造一个自带「烧死的英文硬字幕」的源片：底部一条白带。"""
    subprocess.run(
        ["ffmpeg", "-y",
         "-f", "lavfi", "-i",
         f"color=c=black:s=854x480:d=6,"
         f"drawbox=x=180:y={HARDSUB_TOP}:w=494:"
         f"h={HARDSUB_BOTTOM - HARDSUB_TOP}:color=white:t=fill",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
         "-c:a", "aac", str(path)],
        check=True, capture_output=True)


def _luma(video: Path, out: Path):
    import numpy as np
    from PIL import Image
    subprocess.run(["ffmpeg", "-y", "-ss", "2", "-i", str(video),
                    "-vframes", "1", str(out)], check=True, capture_output=True)
    return np.asarray(Image.open(out).convert("L")).astype(int)


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_zh_only_burns_above_the_sources_hardsub_band(tmp_path):
    import numpy as np

    video = tmp_path / "src.mp4"
    _make_hardsubbed_source(video)

    srt, bilingual, quotes = _assemble_fixture(tmp_path, video)
    final = tmp_path / "out" / "final.mp4"
    produce.assemble(video, srt, bilingual, quotes, tmp_path, final,
                     sub_mode="zh-only",
                     sub_margin_v=produce.DEFAULT_SUB_MARGIN_V)

    burned = _luma(final, tmp_path / "burned.png")
    base = _luma(video, tmp_path / "base.png")

    # 1) 中文确实烧上去了，而且整块都在硬字幕带上方
    zh_rows = [y for y in range(HARDSUB_TOP) if (burned[y] >= 200).any()]
    assert zh_rows, "zh-only 模式下画面里找不到中文字幕"
    assert max(zh_rows) < 400, f"中文压到了 y={max(zh_rows)}，离硬字幕带太近"

    # 2) 源片自带的那条硬字幕带一个像素都没被盖到（留 2 的量给 h264 量化误差）
    band = slice(HARDSUB_TOP, HARDSUB_BOTTOM)
    assert np.abs(burned[band] - base[band]).max() <= 2
    assert base[band].max() >= 200      # 带子本身确实是白的


# ── sub_margin_v=auto：逐条 cue 探测硬字幕带，各自避让 ───────────────────────
# run 30263406353 的成片目视：固定 MarginV=96 贴着对白那条带摆，00:45 处源片
# 换成了大字号两行引言板（位置明显更高），中文行正好糊在它第二行上。

def test_auto_margin_v_reproduces_the_measured_default():
    """480 高、上沿 408、间隙 24 —— 正是 PR #5 那个 96 的来路。"""
    assert produce.auto_margin_v(480, 408, 24) == produce.DEFAULT_SUB_MARGIN_V


def test_auto_margin_v_lifts_higher_for_the_quote_board():
    """引言板上沿抬到 330，摆位得跟着抬到 174，不能还留在 96。"""
    assert produce.auto_margin_v(480, 330, 24) == 174


def test_auto_margin_v_honours_the_gap():
    assert produce.auto_margin_v(480, 408, 0) == 72
    assert produce.auto_margin_v(480, 408, 40) == 112


def test_auto_margin_v_falls_back_when_nothing_detected():
    assert produce.auto_margin_v(480, None, 24) == produce.DEFAULT_SUB_MARGIN_V
    assert produce.auto_margin_v(480, None, 999) == produce.DEFAULT_SUB_MARGIN_V


def _cues(spans):
    return [{"start_sec": a, "end_sec": b} for a, b in spans]


def test_plan_cue_placements_records_every_cue(tmp_path, monkeypatch):
    tops = {0: 408, 1: 330}
    seen = []

    def fake(video, a, b, tmp, **kw):
        seen.append((a, b))
        return tops[len(seen) - 1], ""

    monkeypatch.setattr(produce.HP, "probe_cue_band", fake)
    got = produce.plan_cue_placements(
        Path("v.mp4"), 480, _cues([(0.0, 3.0), (3.0, 6.0)]),
        clip_start=100.0, gap=24, tmp_dir=tmp_path / "probe")

    # 探测要回到源片的绝对时间，cue 里存的是切片内相对时间
    assert seen == [(100.0, 103.0), (103.0, 106.0)]
    assert [p["cue_index"] for p in got] == [1, 2]
    assert [p["hardsub_top_y"] for p in got] == [408, 330]
    assert [p["margin_v"] for p in got] == [96, 174]
    assert [p["fallback"] for p in got] == [False, False]


def test_plan_cue_placements_falls_back_per_cue(tmp_path, monkeypatch, capsys):
    """探不到的那条单独回落到 96，探到的那条照常按检测值走。"""
    results = iter([(None, "no_text_rows：31 个亮行没有一行像文字"
                           "（文字感最高 0.17，阈值 0.35）"), (330, "")])
    monkeypatch.setattr(produce.HP, "probe_cue_band",
                        lambda *a, **k: next(results))
    got = produce.plan_cue_placements(
        Path("v.mp4"), 480, _cues([(0.0, 3.0), (3.0, 6.0)]),
        clip_start=0.0, gap=24, tmp_dir=tmp_path / "probe")

    assert got[0] == {"cue_index": 1, "source_start_sec": 0.0,
                      "source_end_sec": 3.0, "hardsub_top_y": None,
                      "margin_v": produce.DEFAULT_SUB_MARGIN_V,
                      "fallback": True}
    assert got[1]["margin_v"] == 174 and got[1]["fallback"] is False
    # 回落必须在日志里说清楚，不能静默
    # 回落必须在日志里说清楚，不能静默：既要说是回落，也要说没过哪道闸
    out = capsys.readouterr().out
    assert "未探到可信硬字幕带" in out
    assert "no_text_rows" in out and "文字感最高 0.17" in out


def test_auto_refuses_to_push_the_zh_line_above_the_midline(
        tmp_path, monkeypatch, capsys):
    """探到了可信的带、但避让确实做不到时才退 2。

    上沿 250 过得了探测器那几道闸（在中线之下），可是配上 24 的间隙，
    中文块就会被顶到画面上半部分 —— 这是源片硬字幕位置本身没法躲，
    不是探测误判，所以拒绝硬出。加上文字感闸门之后这条路极罕见。
    """
    monkeypatch.setattr(produce.HP, "probe_cue_band", lambda *a, **k: (250, ""))

    with pytest.raises(SystemExit) as e:
        produce.plan_cue_placements(
            Path("v.mp4"), 480, _cues([(0.0, 3.0), (3.0, 6.0)]),
            clip_start=10.0, gap=24, tmp_dir=tmp_path / "probe")

    assert e.value.code == produce.EXIT_QUALITY
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "auto_sub_margin_v_above_midline"
    assert payload["stage"] == "assemble"
    # JSON 里要能直接看出是哪一条、检测到了什么、算出了多少
    assert payload["cue_index"] == 1
    assert payload["cue_source_start_sec"] == 10.0
    assert payload["hardsub_top_y"] == 250
    assert payload["margin_v"] == 254
    assert payload["video_height"] == 480
    assert payload["sub_avoid_gap"] == 24


def test_an_untrusted_probe_falls_back_instead_of_exiting_two(
        tmp_path, monkeypatch, capsys):
    """探测器报「没探到」时一律回落，哪怕回落值本身越过了中线。

    退 2 是留给「探到了可信的带、避让确实做不到」的。探测器都没给出位置，
    再拿它去拒绝硬出就是把误判升级成停产 —— CI run 30269220766 正是如此。
    这里的画面只有 160 高，中线 80，默认的 96 反而在中线之上。
    """
    monkeypatch.setattr(produce.HP, "probe_cue_band",
                        lambda *a, **k: (None, "no_text_rows：一行都不像文字"))
    got = produce.plan_cue_placements(
        Path("v.mp4"), 160, _cues([(0.0, 3.0)]),
        clip_start=0.0, gap=24, tmp_dir=tmp_path / "probe")

    assert got[0]["fallback"] is True
    assert got[0]["margin_v"] == produce.DEFAULT_SUB_MARGIN_V
    assert "no_text_rows：一行都不像文字" in capsys.readouterr().out


def test_the_measured_default_stays_below_the_midline(tmp_path, monkeypatch):
    """回归保护：408 这个实测上沿绝不能被新的闸门拦住。"""
    monkeypatch.setattr(produce.HP, "probe_cue_band", lambda *a, **k: (408, ""))
    got = produce.plan_cue_placements(
        Path("v.mp4"), 480, _cues([(0.0, 3.0)]),
        clip_start=0.0, gap=24, tmp_dir=tmp_path / "probe")
    assert got[0]["margin_v"] == produce.DEFAULT_SUB_MARGIN_V


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_integer_sub_margin_v_never_probes(tmp_path, monkeypatch):
    """给整数时行为与 PR #5 逐字一致：一帧都不抽，MarginV 全片钉死。"""
    video = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=854x480:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", str(video)],
        check=True, capture_output=True)

    def boom(*a, **k):
        raise AssertionError("给了整数还去探测源片")

    monkeypatch.setattr(produce.HP, "probe_cue_band", boom)
    seen = _captured_make_ass(monkeypatch, tmp_path)
    srt, bilingual, quotes = _assemble_fixture(tmp_path, video)
    segs, placements = produce.assemble(
        video, srt, bilingual, quotes, tmp_path, tmp_path / "out" / "final.mp4",
        sub_mode="zh-only", sub_margin_v=110)

    assert seen["zh_margin_v"] == 110
    assert placements == []
    ass = Path(seen["path"]).read_text(encoding="utf-8-sig")
    zh = [ln for ln in ass.splitlines() if ln.startswith("Dialogue: ")]
    assert zh[0].split(",", 8)[7] == "110"


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_both_mode_never_probes(tmp_path, monkeypatch):
    """默认 both 不该被自动挡带上：它不抬字幕，也就不用探测。"""
    video = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=854x480:d=6",
         "-c:v", "libx264", "-preset", "ultrafast", str(video)],
        check=True, capture_output=True)

    def boom(*a, **k):
        raise AssertionError("both 模式去探测源片了")

    monkeypatch.setattr(produce.HP, "probe_cue_band", boom)
    srt, bilingual, quotes = _assemble_fixture(tmp_path, video)
    segs, placements = produce.assemble(
        video, srt, bilingual, quotes, tmp_path, tmp_path / "out" / "final.mp4",
        sub_margin_v=produce.SUB_MARGIN_V_AUTO)
    assert placements == []


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_auto_writes_per_cue_margins_into_the_ass(tmp_path, monkeypatch):
    """两条 cue 探到不同高度时，ASS 里两行 Dialogue 的 MarginV 必须不同。"""
    video = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=gray:s=854x480:d=12",
         "-c:v", "libx264", "-preset", "ultrafast", str(video)],
        check=True, capture_output=True)

    srt = write_srt(tmp_path / "t.srt", [(0.0, 4.0, "Simple is hard."),
                                        (4.0, 8.0, "As simple as possible.")])
    entries = produce.HL.parse_srt(str(srt))
    bilingual = tmp_path / "bi.json"
    bilingual.write_text(json.dumps(
        [{"index": e["index"], "start": e["start"], "end": e["end"],
          "en": e["text"], "zh": f"简单很难{e['index']}"} for e in entries],
        ensure_ascii=False), encoding="utf-8")

    tops = iter([(408, ""), (330, "")])
    monkeypatch.setattr(produce.HP, "probe_cue_band",
                        lambda *a, **k: next(tops))
    seen = _captured_make_ass(monkeypatch, tmp_path)
    segs, placements = produce.assemble(
        video, srt, bilingual, quotes_for([(0.0, 8.0)]), tmp_path,
        tmp_path / "out" / "final.mp4", sub_mode="zh-only",
        sub_margin_v=produce.SUB_MARGIN_V_AUTO)

    assert [p["margin_v"] for p in placements] == [96, 174]
    ass = Path(seen["path"]).read_text(encoding="utf-8-sig")
    zh = [ln for ln in ass.splitlines() if ",ZH," in ln]
    assert [ln.split(",", 8)[7] for ln in zh] == ["96", "174"]


def test_cli_accepts_auto():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s",
                               "--sub-mode", "zh-only",
                               "--sub-margin-v", "auto",
                               "--sub-avoid-gap", "30"])
    assert args.sub_margin_v == produce.SUB_MARGIN_V_AUTO
    assert args.sub_avoid_gap == 30


@pytest.mark.parametrize("bad", ["", "AUTO", "auto96", "-4", "96px", "9.6"])
def test_cli_rejects_malformed_sub_margin_v(bad):
    with pytest.raises(SystemExit):
        produce.parse_args(["--source", "v.mp4", "--slug", "s",
                            "--sub-margin-v", bad])


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_meta_records_auto_and_the_per_cue_placements(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"stub")
    placements = [{"cue_index": 1, "source_start_sec": 0.0,
                   "source_end_sec": 3.0, "hardsub_top_y": 408,
                   "margin_v": 96, "fallback": False},
                  {"cue_index": 2, "source_start_sec": 198.9,
                   "source_end_sec": 202.0, "hardsub_top_y": 330,
                   "margin_v": 174, "fallback": False}]
    meta_path = produce.write_meta(
        tmp_path, "munger", "标题", "https://example.com/v", final, [],
        {"files": {}}, "deepseek-v3", "芒格", "zh-only",
        produce.SUB_MARGIN_V_AUTO, 24, placements)

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["sub_margin_v"] == "auto"
    assert meta["sub_avoid_gap"] == 24
    assert meta["sub_placements"] == placements


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_meta_defaults_carry_no_placements(tmp_path):
    final = tmp_path / "final.mp4"
    final.write_bytes(b"stub")
    meta_path = produce.write_meta(
        tmp_path, "munger", "标题", "https://example.com/v", final, [],
        {"files": {}}, "deepseek-v3", "芒格")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["sub_avoid_gap"] == produce.DEFAULT_SUB_AVOID_GAP
    assert meta["sub_placements"] == []


# ── 真跑 ffmpeg：一条源片里两种高度的硬字幕带 ─────────────────────────────────
# 固定 MarginV 的天花板只有在「源片硬字幕高度会变」的素材上才暴露得出来。
# 这条造一条前后两段硬字幕高度不同的源片，走 auto 真烧，然后数像素。

DIALOG_TOP, DIALOG_BOTTOM = 408, 456        # 前 6s：常规对白，小字号一行
QUOTE_TOP, QUOTE_BOTTOM = 330, 400          # 后 6s：大字号引言板，两行


def _make_two_height_source(path: Path) -> None:
    """两条带都画成 2px 亮 / 6px 暗的竖条纹，而不是实心白块。

    实心白块过不了探测器的文字感闸门 —— 那正是闸门存在的意义：源片里
    y=240 那片 120px 厚的 B-roll 亮画面就是被当成字幕带才闯出 CI run
    30269220766 的。条纹的行统计与实测真字幕对得上（亮占比 14%/19%，
    文字感 1.00，实测源片 219.7s 那条带是 10% / 1.15），而条带的上下沿仍
    钉在常量上，下面按像素比对的断言照旧成立。
    """
    band = (f"if(lt(T\\,6)\\,"
            f" between(Y\\,{DIALOG_TOP}\\,{DIALOG_BOTTOM - 1})"
            f"*between(X\\,180\\,673)\\,"
            f" between(Y\\,{QUOTE_TOP}\\,{QUOTE_BOTTOM - 1})"
            f"*between(X\\,100\\,753))")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         f"color=c=black:s=854x480:d=12,"
         f"geq=lum='16+239*{band}*lt(mod(X\\,8)\\,2)':cb=128:cr=128",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
         "-c:a", "aac", str(path)],
        check=True, capture_output=True)


def _luma_at(video: Path, t: float, out: Path):
    import numpy as np
    from PIL import Image
    subprocess.run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(video),
                    "-vframes", "1", str(out)], check=True, capture_output=True)
    return np.asarray(Image.open(out).convert("L")).astype(int)


@pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
def test_auto_avoids_two_different_hardsub_heights_in_one_source(tmp_path):
    import numpy as np

    video = tmp_path / "src.mp4"
    _make_two_height_source(video)

    srt = write_srt(tmp_path / "t.srt", [(0.0, 5.5, "Patience is a weapon."),
                                        (6.0, 11.5, "As simple as possible.")])
    entries = produce.HL.parse_srt(str(srt))
    bilingual = tmp_path / "bi.json"
    bilingual.write_text(json.dumps(
        [{"index": e["index"], "start": e["start"], "end": e["end"],
          "en": e["text"], "zh": "凡事应尽可能简单"} for e in entries],
        ensure_ascii=False), encoding="utf-8")

    final = tmp_path / "out" / "final.mp4"
    segs, placements = produce.assemble(
        video, srt, bilingual, quotes_for([(0.0, 6.0), (6.0, 12.0)]),
        tmp_path, final, sub_mode="zh-only",
        sub_margin_v=produce.SUB_MARGIN_V_AUTO,
        sub_avoid_gap=produce.DEFAULT_SUB_AVOID_GAP)

    # 探测各自报出了自己那一段的上沿，摆位跟着分开
    assert [p["hardsub_top_y"] for p in placements] == [DIALOG_TOP, QUOTE_TOP]
    assert [p["margin_v"] for p in placements] == [96, 174]

    # final.mp4 = 两段各 6s 拼起来，取各段中间那一帧
    zh_bottoms = {}
    for name, t, band_top, band_bottom in (
            ("dialog", 2.0, DIALOG_TOP, DIALOG_BOTTOM),
            ("quote", 8.0, QUOTE_TOP, QUOTE_BOTTOM)):
        burned = _luma_at(final, t, tmp_path / f"burned_{name}.png")
        base = _luma_at(video, t, tmp_path / f"base_{name}.png")

        # 1) 中文确实烧上去了，而且整块都在这一段自己那条硬字幕带上方
        zh_rows = [y for y in range(band_top) if (burned[y] >= 200).any()]
        assert zh_rows, f"{name} 段画面里找不到中文字幕"
        assert max(zh_rows) < band_top, \
            f"{name} 段中文压到了 y={max(zh_rows)}，硬字幕带上沿是 {band_top}"
        zh_bottoms[name] = max(zh_rows)

        # 2) 这一段的硬字幕带一个像素都没被盖到。
        # 这里比「亮/不亮」的二值掩码而不是直接卡 maxdiff：中文是白字描黑边，
        # 压到白带上必然把 255 的像素拽到 0 附近，二值必翻；而 h264 在白框尖角
        # 处的振铃只有几个像素的 ±4，翻不动 200 这道线。实测引言板那条带
        # （y=330，宽 654）在右上角 x=752~753 就有 2 个像素差 4，位置离中文块
        # （y=280~300，居中）十万八千里，卡 maxdiff<=2 会被这种噪声误伤。
        band = slice(band_top, band_bottom)
        assert base[band].max() >= 200, f"{name} 段的硬字幕带本身不是白的"
        diff = np.abs(burned[band] - base[band])
        assert ((burned[band] >= 200) == (base[band] >= 200)).all(), \
            f"{name} 段的硬字幕带被中文盖到了（有像素跨过了亮度分界）"
        # 再把一道量级：真被字压上是 ±200 的量级，不可能只有个位数
        assert diff.max() <= 8, \
            f"{name} 段硬字幕带出现了不像量化噪声的改动 maxdiff={diff.max()}"
        assert diff.mean() < 0.1, \
            f"{name} 段硬字幕带整体被改动了 meandiff={diff.mean():.4f}"

    # 3) 两段的中文摆在不同高度 —— 这正是固定 MarginV 做不到的事
    assert zh_bottoms["quote"] < zh_bottoms["dialog"] - 40, \
        f"两段中文落在了几乎相同的高度 {zh_bottoms}，动态避让没生效"
