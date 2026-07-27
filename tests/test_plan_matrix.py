"""Actions 出片任务汇总（.github/scripts/plan_matrix.py）。

配置错误要在 plan 阶段就拦下来，而不是等 40 分钟的 runner 跑到一半才炸。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "plan_matrix", ROOT / ".github" / "scripts" / "plan_matrix.py")
plan_matrix = importlib.util.module_from_spec(_spec)
sys.modules["plan_matrix"] = plan_matrix
_spec.loader.exec_module(plan_matrix)


def write_sources(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload, ensure_ascii=False),
                                 encoding="utf-8")
    return tmp_path


# ── workflow_dispatch ───────────────────────────────────────────────────────

def test_dispatch_builds_single_job():
    jobs = plan_matrix.build({
        "EVENT": "workflow_dispatch",
        "IN_SOURCE": "https://example.com/v", "IN_SLUG": "munger",
        "IN_TITLE": "", "IN_TRANSLATOR": "deepseek-v3", "IN_DUAL": "false",
    })
    assert jobs == [{"source": "https://example.com/v", "slug": "munger",
                     "title_override": "", "translator": "deepseek-v3",
                     "dual": "false", "cover_time_sec": "", "cover_crop": "",
                     "speaker": "", "sub_mode": "both", "sub_margin_v": "",
                     "sub_avoid_gap": "", "episodes": ""}]


def test_dispatch_dual_bool_becomes_string():
    jobs = plan_matrix.build({
        "EVENT": "workflow_dispatch", "IN_SOURCE": "u", "IN_SLUG": "s",
        "IN_DUAL": "true",
    })
    assert jobs[0]["dual"] == "true"
    assert jobs[0]["translator"] == "deepseek-v3"      # 默认值补齐


def test_dispatch_missing_slug_raises():
    with pytest.raises(ValueError, match="source 和 slug"):
        plan_matrix.build({"EVENT": "workflow_dispatch",
                           "IN_SOURCE": "u", "IN_SLUG": ""})


# ── push sources/*.json ─────────────────────────────────────────────────────

def test_single_object_source_file(tmp_path):
    write_sources(tmp_path, "a.json", {"source": "u1", "slug": "one"})
    jobs = plan_matrix.build({"EVENT": "push"}, tmp_path)
    assert len(jobs) == 1
    assert jobs[0]["slug"] == "one"
    assert jobs[0]["dual"] == "false"


def test_array_source_file_expands_to_matrix(tmp_path):
    write_sources(tmp_path, "batch.json", [
        {"source": "u1", "slug": "one"},
        {"source": "u2", "slug": "two", "dual": True, "title_override": "标题"},
    ])
    jobs = plan_matrix.build({"EVENT": "push"}, tmp_path)
    assert [j["slug"] for j in jobs] == ["one", "two"]
    assert jobs[1]["dual"] == "true"
    assert jobs[1]["title_override"] == "标题"


def test_multiple_files_are_merged_in_name_order(tmp_path):
    write_sources(tmp_path, "b.json", {"source": "u2", "slug": "two"})
    write_sources(tmp_path, "a.json", {"source": "u1", "slug": "one"})
    jobs = plan_matrix.build({"EVENT": "push"}, tmp_path)
    assert [j["slug"] for j in jobs] == ["one", "two"]


def test_duplicate_slug_raises(tmp_path):
    # 同名 slug 会让两个 job 抢同一个 artifact
    write_sources(tmp_path, "a.json", [
        {"source": "u1", "slug": "dup"},
        {"source": "u2", "slug": "dup"},
    ])
    with pytest.raises(ValueError, match="slug 重复"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)


def test_invalid_translator_raises(tmp_path):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "translator": "gpt-4"})
    with pytest.raises(ValueError, match="translator"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)


def test_empty_sources_dir_yields_empty_matrix(tmp_path):
    assert plan_matrix.build({"EVENT": "push"}, tmp_path) == []


def test_matrix_is_json_serializable_for_fromjson(tmp_path):
    write_sources(tmp_path, "a.json", {"source": "u", "slug": "s"})
    jobs = plan_matrix.build({"EVENT": "push"}, tmp_path)
    assert json.loads(json.dumps(jobs, ensure_ascii=False)) == jobs


# ── 手动封面时间点 ──────────────────────────────────────────────────────────

def test_cover_time_sec_defaults_to_empty(tmp_path):
    write_sources(tmp_path, "a.json", {"source": "u", "slug": "s"})
    assert plan_matrix.build({"EVENT": "push"}, tmp_path)[0]["cover_time_sec"] == ""


def test_cover_time_sec_passes_through_from_sources(tmp_path):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "cover_time_sec": 96.0})
    assert plan_matrix.build({"EVENT": "push"}, tmp_path)[0]["cover_time_sec"] == "96.0"


def test_cover_time_sec_passes_through_from_dispatch():
    jobs = plan_matrix.build({
        "EVENT": "workflow_dispatch", "IN_SOURCE": "u", "IN_SLUG": "s",
        "IN_COVER_TIME_SEC": "96",
    })
    assert jobs[0]["cover_time_sec"] == "96"


def test_non_numeric_cover_time_sec_raises(tmp_path):
    # 写错了要在 plan 阶段就拦下，不能等 runner 跑到最后一步才炸
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "cover_time_sec": "第96秒"})
    with pytest.raises(ValueError, match="cover_time_sec"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)


def test_negative_cover_time_sec_raises(tmp_path):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "cover_time_sec": -5})
    with pytest.raises(ValueError, match="cover_time_sec"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)


# ── 封面裁切 ────────────────────────────────────────────────────────────────

def test_cover_crop_defaults_to_empty(tmp_path):
    write_sources(tmp_path, "a.json", {"source": "u", "slug": "s"})
    assert plan_matrix.build({"EVENT": "push"}, tmp_path)[0]["cover_crop"] == ""


def test_cover_crop_passes_through_from_sources(tmp_path):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "cover_crop": "854:396:0:0"})
    assert plan_matrix.build({"EVENT": "push"},
                             tmp_path)[0]["cover_crop"] == "854:396:0:0"


def test_cover_crop_passes_through_from_dispatch():
    jobs = plan_matrix.build({
        "EVENT": "workflow_dispatch", "IN_SOURCE": "u", "IN_SLUG": "s",
        "IN_COVER_CROP": "854:396:0:0",
    })
    assert jobs[0]["cover_crop"] == "854:396:0:0"


@pytest.mark.parametrize("bad", ["854x396", "854:396:0", "854:396:0:0:0",
                                 "-1:396:0:0", "宽:高:0:0"])
def test_malformed_cover_crop_raises(tmp_path, bad):
    # 写错了要在 plan 阶段就拦下，不能等 runner 跑到最后一步才炸
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "cover_crop": bad})
    with pytest.raises(ValueError, match="cover_crop"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)


# ── speaker / 字幕语种 ──────────────────────────────────────────────────────
# 封面左上角的红标一直显示默认的「演讲者」，就是因为 workflow 没暴露 speaker。

def test_speaker_defaults_to_empty(tmp_path):
    write_sources(tmp_path, "a.json", {"source": "u", "slug": "s"})
    # 空串 = 不传 --speaker，由 produce.py 自己兜底
    assert plan_matrix.build({"EVENT": "push"}, tmp_path)[0]["speaker"] == ""


def test_speaker_passes_through_from_sources(tmp_path):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "speaker": "查理·芒格"})
    assert plan_matrix.build({"EVENT": "push"}, tmp_path)[0]["speaker"] == "查理·芒格"


def test_speaker_passes_through_from_dispatch():
    jobs = plan_matrix.build({
        "EVENT": "workflow_dispatch", "IN_SOURCE": "u", "IN_SLUG": "s",
        "IN_SPEAKER": "查理·芒格",
    })
    assert jobs[0]["speaker"] == "查理·芒格"


def test_sub_mode_defaults_to_both(tmp_path):
    write_sources(tmp_path, "a.json", {"source": "u", "slug": "s"})
    jobs = plan_matrix.build({"EVENT": "push"}, tmp_path)
    assert jobs[0]["sub_mode"] == "both"
    assert jobs[0]["sub_margin_v"] == ""


def test_sub_mode_passes_through_from_sources(tmp_path):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "sub_mode": "zh-only",
                   "sub_margin_v": 110})
    job = plan_matrix.build({"EVENT": "push"}, tmp_path)[0]
    assert job["sub_mode"] == "zh-only"
    assert job["sub_margin_v"] == "110"


def test_sub_mode_passes_through_from_dispatch():
    jobs = plan_matrix.build({
        "EVENT": "workflow_dispatch", "IN_SOURCE": "u", "IN_SLUG": "s",
        "IN_SUB_MODE": "zh-only", "IN_SUB_MARGIN_V": "96",
    })
    assert jobs[0]["sub_mode"] == "zh-only"
    assert jobs[0]["sub_margin_v"] == "96"


def test_invalid_sub_mode_raises(tmp_path):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "sub_mode": "zh_only"})
    with pytest.raises(ValueError, match="sub_mode"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)


@pytest.mark.parametrize("bad", ["96px", "-96", "9.6", "AUTO", "auto96"])
def test_malformed_sub_margin_v_raises(tmp_path, bad):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "sub_margin_v": bad})
    with pytest.raises(ValueError, match="sub_margin_v"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)


def test_sub_margin_v_auto_passes_through(tmp_path):
    """auto 是 produce.py 的默认挡，plan 阶段不能把它当成非法值拦下。"""
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "sub_mode": "zh-only",
                   "sub_margin_v": "auto", "sub_avoid_gap": 32})
    job = plan_matrix.build({"EVENT": "push"}, tmp_path)[0]
    assert job["sub_margin_v"] == "auto"
    assert job["sub_avoid_gap"] == "32"


def test_sub_avoid_gap_passes_through_from_dispatch():
    jobs = plan_matrix.build({
        "EVENT": "workflow_dispatch", "IN_SOURCE": "u", "IN_SLUG": "s",
        "IN_SUB_MARGIN_V": "auto", "IN_SUB_AVOID_GAP": "18",
    })
    assert jobs[0]["sub_margin_v"] == "auto"
    assert jobs[0]["sub_avoid_gap"] == "18"


@pytest.mark.parametrize("bad", ["24px", "-24", "2.4", "auto"])
def test_malformed_sub_avoid_gap_raises(tmp_path, bad):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "sub_avoid_gap": bad})
    with pytest.raises(ValueError, match="sub_avoid_gap"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)


# ── --episodes N ────────────────────────────────────────────────────────────

def test_episodes_passes_through_from_sources(tmp_path):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "episodes": 3})
    assert plan_matrix.build({"EVENT": "push"}, tmp_path)[0]["episodes"] == "3"


def test_episodes_passes_through_from_dispatch():
    jobs = plan_matrix.build({
        "EVENT": "workflow_dispatch", "IN_SOURCE": "u", "IN_SLUG": "s",
        "IN_EPISODES": "4",
    })
    assert jobs[0]["episodes"] == "4"


def test_episodes_defaults_to_empty_string(tmp_path):
    """留空表示用 produce.py 的默认 1，YAML 侧据此决定加不加 --episodes。"""
    write_sources(tmp_path, "a.json", {"source": "u", "slug": "s"})
    assert plan_matrix.build({"EVENT": "push"}, tmp_path)[0]["episodes"] == ""


@pytest.mark.parametrize("bad", ["0", "-1", "2.5", "两集", "3集"])
def test_malformed_episodes_raises(tmp_path, bad):
    write_sources(tmp_path, "a.json",
                  {"source": "u", "slug": "s", "episodes": bad})
    with pytest.raises(ValueError, match="episodes"):
        plan_matrix.build({"EVENT": "push"}, tmp_path)
