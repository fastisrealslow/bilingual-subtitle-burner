"""多集出片（``produce.py --episodes N``）与 ``queue.json``。

两件事必须钉死：
1. ``--episodes 1``（默认）的行为和加这个开关之前逐字一致 —— 产物还落在
   ``deliver/<slug>/``，阶段顺序、每一步的调用次数都不变。
2. N>1 时下载和转写只做一次，片段组互不重叠，源不够就少出几集而不是凑数。
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                   # noqa: E402


# ── 把整条流水线的外部依赖换成计数器 ────────────────────────────────────────

def candidates(spans):
    """score_highlights 的返回形态：已排好 rank 的候选段。"""
    return [{"rank": i, "score": 10.0 - i,
             "start_sec": a, "end_sec": b, "duration_sec": b - a,
             "transcript_en": f"seg {i}", "transcript_zh": "",
             "title_suggestion": "", "reason": ""}
            for i, (a, b) in enumerate(spans, 1)]


def aligned(spans):
    """align_clips 之后的形态：带 clip_* 字段。"""
    out = []
    for item in candidates(spans):
        out.append({**item,
                    "clip_start_sec": item["start_sec"],
                    "clip_end_sec": item["end_sec"],
                    "clip_duration_sec": item["duration_sec"]})
    return out


def even_spans(n, length=60.0, gap=10.0):
    """n 段互不重叠、每段都够长的候选。"""
    return [(i * (length + gap), i * (length + gap) + length) for i in range(n)]


class Harness:
    def __init__(self):
        self.calls = {"download": 0, "transcribe": 0, "cover_select": 0,
                      "translate": 0, "assemble": 0, "title": 0, "cover_render": 0}
        self.stages = []
        self.titles = []
        self.assembled = []


@pytest.fixture
def harness(tmp_path, monkeypatch):
    h = Harness()
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.delenv("GITHUB_SERVER_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    video = tmp_path / "source.mp4"
    video.write_bytes(b"video")

    def fake_resolve(source, work, *a, **k):
        h.calls["download"] += 1
        return video

    def fake_transcribe(v, work, language):
        h.calls["transcribe"] += 1
        srt = Path(work) / "transcript.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nhi\n\n",
                       encoding="utf-8")
        return srt

    def fake_cover_select(*a, **k):
        h.calls["cover_select"] += 1
        return "frame.jpg", {"files": {}, "cover_source": "auto"}

    def fake_translate(srt, quotes, work, translator, *a, **k):
        h.calls["translate"] += 1
        out = Path(work) / f"quotes_zh.{translator}.json"
        out.write_text("[]", encoding="utf-8")
        return out

    def fake_assemble(video_, srt, bilingual, quotes, work, out_path, *a, **k):
        h.calls["assemble"] += 1
        h.assembled.append(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"final" * h.calls["assemble"])
        segs = [{"index": i, "source_start_sec": q["clip_start_sec"],
                 "source_end_sec": q["clip_end_sec"],
                 "duration_sec": q["clip_duration_sec"], "cues": 7}
                for i, q in enumerate(quotes, 1)]
        return segs, []

    def fake_title(quotes, speaker, *a, **k):
        h.calls["title"] += 1
        title = f"标题{h.calls['title']}"
        h.titles.append(title)
        return title

    def fake_render_covers(frame, title, speaker, out_dir, report):
        h.calls["cover_render"] += 1
        out_dir.mkdir(parents=True, exist_ok=True)
        files = {}
        for name in ("cover_16x9.jpg", "cover_9x16.jpg"):
            path = out_dir / name
            path.write_bytes(b"jpeg")
            files[name] = path
        report["files"] = files
        return report

    monkeypatch.setattr(produce, "resolve_source", fake_resolve)
    monkeypatch.setattr(produce, "transcribe", fake_transcribe)
    monkeypatch.setattr(produce, "select_cover_frame", fake_cover_select)
    monkeypatch.setattr(produce, "translate_windows", fake_translate)
    monkeypatch.setattr(produce, "assemble", fake_assemble)
    monkeypatch.setattr(produce, "make_title", fake_title)
    monkeypatch.setattr(produce, "render_covers", fake_render_covers)
    monkeypatch.setattr(produce, "probe_duration", lambda p: 600.0)
    monkeypatch.setattr(produce, "probe_size", lambda p: (854, 480))
    monkeypatch.setattr(produce.sf_client, "log_cache_stats", lambda: None)

    real_stage = produce.stage
    monkeypatch.setattr(produce, "stage",
                        lambda name: (h.stages.append(name), real_stage(name))[1])

    def set_candidates(spans):
        monkeypatch.setattr(produce.HL, "score_highlights",
                            lambda *a, **k: candidates(spans))
        monkeypatch.setattr(produce.HL, "align_clips",
                            lambda items, *a, **k: aligned(spans))

    h.set_candidates = set_candidates
    set_candidates(even_spans(9))
    return h


def deliver(slug="munger"):
    return Path("deliver") / slug


def read_queue(slug="munger"):
    return json.loads((deliver(slug) / "queue.json").read_text(encoding="utf-8"))


# ── 回归：单集行为完全不变 ──────────────────────────────────────────────────

SINGLE_STAGES = ["input", "transcribe", "highlight", "cover-select",
                 "translate", "assemble", "title", "cover-render", "manifest"]


def test_default_is_one_episode_with_the_old_layout(harness):
    assert produce.main(["--source", "s.mp4", "--slug", "munger"]) == 0

    # 产物还在老位置，没有多出 ep01/ 这一层
    assert (deliver() / "final.mp4").is_file()
    assert (deliver() / "cover_16x9.jpg").is_file()
    assert (deliver() / "cover_9x16.jpg").is_file()
    assert (deliver() / "meta.json").is_file()
    assert not (deliver() / "ep01").exists()

    # 阶段顺序和每步调用次数逐字不变
    assert harness.stages == SINGLE_STAGES
    assert harness.calls == {"download": 1, "transcribe": 1, "cover_select": 1,
                             "translate": 1, "assemble": 1, "title": 1,
                             "cover_render": 1}


def test_explicit_episodes_one_matches_the_default(harness, tmp_path):
    produce.main(["--source", "s.mp4", "--slug", "a"])
    first = sorted(p.relative_to(deliver("a")).as_posix()
                   for p in deliver("a").rglob("*"))
    stages_default = list(harness.stages)
    calls_default = dict(harness.calls)

    harness.stages.clear()
    for k in harness.calls:
        harness.calls[k] = 0
    produce.main(["--source", "s.mp4", "--slug", "b", "--episodes", "1"])
    second = sorted(p.relative_to(deliver("b")).as_posix()
                    for p in deliver("b").rglob("*"))

    assert first == second
    assert harness.stages == stages_default
    assert harness.calls == calls_default


def test_single_episode_meta_is_unchanged(harness):
    produce.main(["--source", "s.mp4", "--slug", "munger", "--speaker", "芒格"])
    meta = json.loads((deliver() / "meta.json").read_text(encoding="utf-8"))
    assert meta["slug"] == "munger"
    assert meta["speaker"] == "芒格"
    assert meta["segment_count"] == produce.SEGMENTS
    assert meta["models"]["translate"] == "deepseek-ai/DeepSeek-V3"


def test_single_episode_still_writes_a_one_entry_queue(harness):
    produce.main(["--source", "s.mp4", "--slug", "munger"])
    queue = read_queue()
    assert [ep["id"] for ep in queue["episodes"]] == ["ep01"]
    assert queue["release_tag"] == "clips-munger"


# ── 多集 ────────────────────────────────────────────────────────────────────

def test_three_episodes_share_download_and_transcribe(harness):
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--episodes", "3"]) == 0
    assert harness.calls["download"] == 1
    assert harness.calls["transcribe"] == 1
    # 每集各走一遍选帧/翻译/烧字幕/标题/封面
    assert harness.calls["cover_select"] == 3
    assert harness.calls["translate"] == 3
    assert harness.calls["assemble"] == 3
    assert harness.calls["title"] == 3
    assert harness.calls["cover_render"] == 3


def test_multi_episode_layout_is_per_episode_dirs(harness):
    produce.main(["--source", "s.mp4", "--slug", "munger", "--episodes", "3"])
    for eid in ("ep01", "ep02", "ep03"):
        for name in ("final.mp4", "cover_16x9.jpg", "cover_9x16.jpg",
                     "meta.json"):
            assert (deliver() / eid / name).is_file(), f"{eid}/{name} 缺失"
    assert not (deliver() / "final.mp4").exists()
    assert (deliver() / "queue.json").is_file()


def test_episode_stage_order_repeats_per_episode(harness):
    produce.main(["--source", "s.mp4", "--slug", "munger", "--episodes", "2"])
    per_episode = ["cover-select", "translate", "assemble", "title",
                   "cover-render", "manifest"]
    assert harness.stages == ["input", "transcribe", "highlight"] \
        + per_episode * 2


def test_episodes_do_not_overlap(harness):
    produce.main(["--source", "s.mp4", "--slug", "munger", "--episodes", "3"])
    spans = []
    for ep in read_queue()["episodes"]:
        spans.append((ep["source_start_sec"], ep["source_end_sec"]))
    for i, (a, b) in enumerate(spans):
        for c, d in spans[i + 1:]:
            assert not (b > c and a < d), f"{(a, b)} 与 {(c, d)} 重叠"


def test_short_source_yields_fewer_episodes_without_padding(harness):
    """只有 4 段候选：够第一集的 3 段，第二集只剩 1 段 —— 不凑数，只出 1 集。"""
    harness.set_candidates(even_spans(4))
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--episodes", "3"]) == 0
    assert len(read_queue()["episodes"]) == 1
    assert harness.calls["assemble"] == 1


def test_no_group_at_all_is_a_quality_rejection(harness, capsys):
    """一组都出不来 → 按内容质量拒绝退 2，不降级出片。"""
    harness.set_candidates(even_spans(2))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "3"])
    assert e.value.code == produce.EXIT_QUALITY
    assert json.loads(capsys.readouterr().err.strip())["stage"] == "highlight"


def test_each_episode_gets_its_own_title_and_meta(harness):
    produce.main(["--source", "s.mp4", "--slug", "munger", "--episodes", "2"])
    titles = [json.loads((deliver() / eid / "meta.json")
                         .read_text(encoding="utf-8"))["title"]
              for eid in ("ep01", "ep02")]
    assert titles == harness.titles
    assert len(set(titles)) == 2


def test_per_episode_work_dirs_do_not_collide(harness):
    produce.main(["--source", "s.mp4", "--slug", "munger", "--episodes", "2"])
    work = Path("_tmp") / "munger"
    assert (work / "quotes.json").is_file()
    assert (work / "quotes_ep02.json").is_file()
    assert (work / "ep02" / "quotes_zh.deepseek-v3.json").is_file()


def test_zero_episodes_is_a_config_error(harness, capsys):
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "0"])
    assert e.value.code == produce.EXIT_CONFIG
    assert json.loads(capsys.readouterr().err.strip())["reason"] == "invalid_episodes"


def test_dual_with_multiple_episodes_is_refused(harness, capsys):
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2", "--dual"])
    assert e.value.code == produce.EXIT_CONFIG
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "dual_with_multiple_episodes"


def test_cover_time_sec_with_multiple_episodes_is_refused(harness, capsys):
    """钉死的时间点未必落在第 2 集的片段里，两集共用一张封面是错的。"""
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2", "--cover-time-sec", "287"])
    assert e.value.code == produce.EXIT_CONFIG
    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["reason"] == "cover_time_with_multiple_episodes"


def test_cover_time_sec_is_fine_for_a_single_episode(harness):
    produce.main(["--source", "s.mp4", "--slug", "munger",
                  "--cover-time-sec", "287"])


def test_cli_episodes_defaults_to_one():
    args = produce.parse_args(["--source", "v.mp4", "--slug", "s"])
    assert args.episodes == produce.DEFAULT_EPISODES == 1


# ── 分组逻辑本身 ────────────────────────────────────────────────────────────

def test_group_episodes_splits_by_rank():
    groups = produce.group_episodes(aligned(even_spans(6)), episodes=2,
                                    strict=True)
    assert [[q["rank"] for q in g] for g in groups] == [[1, 2, 3], [4, 5, 6]]


def test_group_episodes_stops_when_the_pool_runs_out():
    groups = produce.group_episodes(aligned(even_spans(5)), episodes=3,
                                    strict=True)
    assert len(groups) == 1


def test_group_episodes_rejects_a_group_that_is_too_short():
    """后 3 段各 20s，合计 60s 达不到 150s 的成片下限 → 只出 1 集。"""
    spans = even_spans(3) + [(1000.0, 1020.0), (1030.0, 1050.0),
                             (1060.0, 1080.0)]
    groups = produce.group_episodes(aligned(spans), episodes=2, strict=True)
    assert len(groups) == 1


def test_group_episodes_skips_overlapping_candidates():
    """与已用片段重叠的候选不能被第二集捡走 —— 两条片子会出现同一段画面。"""
    # rank 4 落在第一集的两段之间，跨着它们；rank 5~7 在源片后段，干净可用
    spans = even_spans(3) + [(30.0, 90.0), (1000.0, 1060.0),
                             (1070.0, 1130.0), (1140.0, 1200.0)]
    groups = produce.group_episodes(aligned(spans), episodes=2, strict=True)
    picked = [(q["clip_start_sec"], q["clip_end_sec"]) for q in groups[1]]
    assert (30.0, 90.0) not in picked
    assert len(groups) == 2


def test_group_episodes_default_returns_exactly_one_group():
    groups = produce.group_episodes(aligned(even_spans(9)))
    assert len(groups) == 1
    assert [q["rank"] for q in groups[0]] == [1, 2, 3]


# ── queue.json 的 schema ────────────────────────────────────────────────────

FIXED_NOW = datetime(2026, 7, 27, 14, 47, 0, tzinfo=timezone.utc)


def sample_queue(**kw):
    episodes = kw.pop("episodes", [
        {"index": 1, "title": "简单思维与长期准备", "duration_sec": 198.921,
         "source_start_sec": 120.0, "source_end_sec": 340.0, "cue_count": 47,
         "sha256": "a" * 64},
        {"index": 2, "title": "别人恐惧时贪婪", "duration_sec": 210.0,
         "source_start_sec": 400.0, "source_end_sec": 620.0, "cue_count": 51,
         "sha256": "b" * 64},
    ])
    params = {"slug": "munger_multi",
              "source_url": "https://archive.org/download/x/x.mp4",
              "speaker": "查理·芒格", "episodes": episodes,
              "generated_at": FIXED_NOW, "commit": "deadbeef",
              "repo": "owner/repo", "server_url": "https://github.com"}
    params.update(kw)
    return produce.build_queue(**params)


def test_queue_top_level_fields():
    q = sample_queue()
    assert q["schema"] == 1
    assert q["slug"] == "munger_multi"
    assert q["speaker"] == "查理·芒格"
    assert q["generated_at"] == "2026-07-27T14:47:00Z"
    assert q["commit"] == "deadbeef"
    assert q["release_tag"] == "clips-munger_multi"
    assert len(q["episodes"]) == 2


def test_queue_episode_ids_and_files():
    ep = sample_queue()["episodes"][1]
    assert ep["index"] == 2 and ep["id"] == "ep02"
    assert ep["files"] == {"video": "ep02.mp4",
                           "cover_16x9": "ep02_cover_16x9.jpg",
                           "cover_9x16": "ep02_cover_9x16.jpg"}


def test_queue_urls_are_release_direct_links():
    ep = sample_queue()["episodes"][0]
    assert ep["urls"]["video"] == ("https://github.com/owner/repo/releases/"
                                   "download/clips-munger_multi/ep01.mp4")
    assert ep["urls"]["cover_9x16"].endswith("/ep01_cover_9x16.jpg")


def test_queue_scheduled_dates_start_the_day_after_generation():
    dates = [ep["scheduled_date"] for ep in sample_queue()["episodes"]]
    assert dates == ["2026-07-28", "2026-07-29"]


def test_queue_status_and_publish_placeholders():
    for ep in sample_queue()["episodes"]:
        assert ep["status"] == "pending"
        assert ep["publish"] == {"bilibili": None, "douyin": None}


def test_queue_tags_lead_with_the_speaker():
    assert sample_queue()["episodes"][0]["tags"][0] == "查理·芒格"
    assert len(sample_queue()["episodes"][0]["tags"]) == 3


def test_queue_desc_ends_with_the_source_link():
    desc = sample_queue()["episodes"][0]["desc"]
    assert desc.rstrip().endswith("https://archive.org/download/x/x.mp4")
    assert "简单思维与长期准备" in desc


def test_queue_rounds_durations_and_keeps_the_hash():
    ep = sample_queue()["episodes"][0]
    assert ep["duration_sec"] == 198.92
    assert ep["cue_count"] == 47
    assert ep["sha256"] == {"video": "a" * 64}


def test_queue_repo_comes_from_the_actions_env(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/else")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://ghe.example.com")
    q = produce.build_queue("s", "u", "谁", [
        {"index": 1, "title": "t", "duration_sec": 1.0, "source_start_sec": 0.0,
         "source_end_sec": 1.0, "cue_count": 1, "sha256": "c" * 64}],
        generated_at=FIXED_NOW)
    assert q["episodes"][0]["urls"]["video"].startswith(
        "https://ghe.example.com/someone/else/releases/download/clips-s/")


def test_queue_written_by_a_real_run_matches_the_files(harness):
    produce.main(["--source", "https://example.com/v.mp4", "--slug", "munger",
                  "--episodes", "2", "--speaker", "芒格"])
    queue = read_queue()
    assert queue["source_url"] == "https://example.com/v.mp4"
    assert queue["speaker"] == "芒格"
    for ep in queue["episodes"]:
        video = deliver() / ep["id"] / "final.mp4"
        assert produce.sha256(video) == ep["sha256"]["video"]
        assert ep["cue_count"] == 7 * produce.SEGMENTS
        assert ep["duration_sec"] == 600.0
