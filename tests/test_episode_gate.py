"""段数闸门：``--episodes N`` 凑不齐 N 集就必须退 2，一集都不出。

「不允许硬出」有三种走样，这里逐个钉死：

1. **少出集** —— 请求 3 集只给回 1 集、退 0。下游按 ``--episodes N`` 排了 N 天
   的发布档期，静默的 M<N 会被当成正常结果收下，等于换个方式硬出。
2. **凑不合格的段** —— 把不到 ``MIN_QUOTE_SEC`` 的碎片、或总时长不够
   ``MIN_TOTAL_SEC`` 的组当成正常一集发出去。
3. **复用同一段** —— 两集共用同一段素材。

闸门实现在 ``produce.group_episodes``：第一组走 ``HL.enforce_quote_thresholds``
（拒绝理由 ``insufficient_quotes`` / ``insufficient_duration``），第二组起在
``group_episodes`` 自己判（``insufficient_episode_quotes`` /
``insufficient_episode_duration``）。两条路都退 ``EXIT_QUALITY``。

阈值一个都不许在这里改：所有 ``group_episodes`` 直调都带 ``strict=True``，
只认代码里的下限，免得环境变量把闸门调松了测试还是绿的。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                   # noqa: E402

from test_produce_episodes import (               # noqa: E402
    aligned, deliver, even_spans, harness, read_queue,
)

# 让 flake8/pytest 都看得见这个 fixture 是被用到的
__all__ = ["harness"]


# ── 工具 ────────────────────────────────────────────────────────────────────

def spans_of(length, count, start=0.0, gap=10.0):
    """count 段、每段 length 秒、互不重叠。"""
    return [(start + i * (length + gap), start + i * (length + gap) + length)
            for i in range(count)]


def rejection(capsys):
    """取 stderr 上那一行结构化拒绝 JSON。"""
    err = capsys.readouterr().err.strip().splitlines()
    return json.loads(err[-1])


def assert_nothing_delivered(harness_, slug="munger"):
    """闸门拦住之后，一集成片都不能有，queue.json 也不能写。"""
    assert harness_.calls["assemble"] == 0, "闸门拦住了却还在拼片"
    assert harness_.calls["translate"] == 0, "闸门拦住了却还在花翻译的钱"
    assert harness_.calls["cover_select"] == 0
    assert harness_.calls["cover_render"] == 0
    out = deliver(slug)
    assert not (out / "queue.json").exists()
    assert list(out.rglob("final*.mp4")) == []


# ── 边界：刚好够，必须放行 ──────────────────────────────────────────────────

@pytest.mark.parametrize("episodes", [1, 2, 3, 4])
def test_exactly_enough_segments_passes(harness, episodes):
    """合格段数恰好等于 SEGMENTS×N —— 边界上必须出片，不能多拦一格。"""
    harness.set_candidates(even_spans(produce.SEGMENTS * episodes))
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--episodes", str(episodes)]) == 0
    assert len(read_queue()["episodes"]) == episodes
    assert harness.calls["assemble"] == episodes


def test_exactly_enough_at_the_unit_level_fills_every_group():
    """每组都必须是满 SEGMENTS 段，而且候选一个不剩地用光。"""
    episodes = 3
    groups = produce.group_episodes(
        aligned(even_spans(produce.SEGMENTS * episodes)), episodes=episodes,
        strict=True)
    assert len(groups) == episodes
    assert all(len(g) == produce.SEGMENTS for g in groups)
    assert [q["rank"] for g in groups for q in g] == \
        list(range(1, produce.SEGMENTS * episodes + 1))


def test_one_spare_segment_still_passes(harness):
    """比刚好多一段：闸门不能因为「用不完」就变卡。"""
    harness.set_candidates(even_spans(produce.SEGMENTS * 2 + 1))
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--episodes", "2"]) == 0
    assert len(read_queue()["episodes"]) == 2


# ── 差一段：必须以退 2 拒绝，且什么都不产出 ─────────────────────────────────

@pytest.mark.parametrize("episodes", [2, 3, 4])
def test_one_segment_short_is_refused(harness, capsys, episodes):
    """SEGMENTS×N − 1 段：差一段也不许出片，更不许降级成 N−1 集。"""
    harness.set_candidates(even_spans(produce.SEGMENTS * episodes - 1))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", str(episodes)])
    assert e.value.code == produce.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["stage"] == "highlight"
    assert payload["reason"] == "insufficient_episode_quotes"
    assert payload["episodes_requested"] == episodes
    assert_nothing_delivered(harness)


def test_refusal_names_the_episode_that_could_not_be_filled(harness, capsys):
    """拒绝理由要能直接告诉运维是第几集卡住、差多少段。"""
    harness.set_candidates(even_spans(7))       # 够 2 集，第 3 集只剩 1 段
    with pytest.raises(SystemExit):
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "3"])
    payload = rejection(capsys)
    assert payload["episode"] == 3
    assert payload["episodes_ready"] == 2
    assert payload["actual_count"] == 1
    assert payload["threshold_count"] == produce.SEGMENTS
    assert "不凑数" in payload["detail"]


def test_downgrading_the_episode_count_is_not_an_option(harness):
    """把「不足时自动下调集数」这条静默降级路径钉死为拒绝。

    源只撑得起 1 集，请求 3 集。旧行为是退 0 + 出 1 集；现在必须退 2。
    这条一旦变绿成「出 1 集」，就是降级路径又回来了。
    """
    harness.set_candidates(even_spans(4))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "3"])
    assert e.value.code == produce.EXIT_QUALITY
    assert_nothing_delivered(harness)


def test_group_episodes_never_returns_fewer_groups_than_requested():
    """函数层的契约：要么恰好 N 组，要么抛 SystemExit —— 没有短列表这种返回。"""
    for count in range(0, produce.SEGMENTS * 3 + 1):
        try:
            groups = produce.group_episodes(aligned(even_spans(count)),
                                            episodes=3, strict=True)
        except SystemExit as e:
            assert e.code == produce.EXIT_QUALITY
        else:
            assert len(groups) == 3, f"{count} 段候选返回了 {len(groups)} 组"


# ── episodes=1 与多集一致 ───────────────────────────────────────────────────

def test_single_episode_short_source_is_refused_the_same_way(harness, capsys):
    """请求 1 集时凑不出也退 2、也不出片 —— 和多集同一个退出码。"""
    harness.set_candidates(even_spans(2))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "1"])
    assert e.value.code == produce.EXIT_QUALITY
    assert rejection(capsys)["stage"] == "highlight"
    assert_nothing_delivered(harness)


def test_single_and_multi_episode_refuse_with_the_same_exit_code(harness):
    """同一个不够格的源，--episodes 1 和 --episodes 3 的退出码必须一样。"""
    harness.set_candidates(even_spans(2))
    codes = []
    for n, slug in (("1", "one"), ("3", "three")):
        with pytest.raises(SystemExit) as e:
            produce.main(["--source", "s.mp4", "--slug", slug,
                          "--episodes", n])
        codes.append(e.value.code)
    assert codes == [produce.EXIT_QUALITY, produce.EXIT_QUALITY]


def test_default_episodes_refuses_a_short_source(harness):
    """不带 --episodes（默认 1）也走同一道闸门。"""
    harness.set_candidates(even_spans(2))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger"])
    assert e.value.code == produce.EXIT_QUALITY
    assert_nothing_delivered(harness)


# ── 单段时长不达标 → 合格段数变少 → 触发闸门 ────────────────────────────────

def test_segments_below_min_quote_sec_do_not_count_toward_episodes(harness,
                                                                   capsys):
    """3 段够长 + 3 段 10s 的碎片：碎片不算合格段，第 2 集凑不出来。

    MIN_QUOTE_SEC=15 先筛掉碎片，再数条数。要是碎片被算进去，第 2 集就会拿
    3 段 10s 拼出一条 30s 的成片 —— 正是「拼接不合格片段」。
    """
    assert produce.HL.MIN_QUOTE_SEC == 15
    harness.set_candidates(spans_of(60.0, 3) + spans_of(10.0, 3, start=1000.0))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2"])
    assert e.value.code == produce.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_episode_quotes"
    assert payload["actual_count"] == 0, "10s 的碎片被算成了合格段"
    assert_nothing_delivered(harness)


def test_a_segment_one_second_under_the_floor_is_dropped(capsys):
    """14s（差 1s）就不算合格段：第 2 集一段都凑不出，由段数那条拦。"""
    spans = spans_of(60.0, 3) + spans_of(14.0, 3, start=1000.0)
    with pytest.raises(SystemExit) as e:
        produce.group_episodes(aligned(spans), episodes=2, strict=True)
    assert e.value.code == produce.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_episode_quotes"
    assert payload["actual_count"] == 0


def test_a_segment_exactly_at_the_floor_counts(capsys):
    """15s 刚好达标要算进合格段 —— 边界不能卡死一格。

    第 2 集因此能凑满 3 段（段数闸门放行），但合计 45s 不到 150s，改由时长那条
    拦。换成 ``insufficient_episode_quotes`` 就说明 15s 被误筛掉了。
    """
    spans = spans_of(60.0, 3) + spans_of(15.0, 3, start=1000.0)
    with pytest.raises(SystemExit) as e:
        produce.group_episodes(aligned(spans), episodes=2, strict=True)
    assert e.value.code == produce.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_episode_duration", \
        "15s 刚好达标，不该被段数闸门筛掉"
    assert payload["actual_sec"] == 45.0


def test_short_segments_are_never_padded_into_an_episode(harness):
    """碎片再多也不能凑成一集。"""
    harness.set_candidates(spans_of(60.0, 3) + spans_of(10.0, 12, start=1000.0))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2"])
    assert e.value.code == produce.EXIT_QUALITY
    assert_nothing_delivered(harness)


# ── 总时长不达标 → 触发闸门 ─────────────────────────────────────────────────

def test_group_under_min_total_sec_is_refused(harness, capsys):
    """第 2 集 3 段各 40s、合计 120s，段数够但不到 MIN_TOTAL_SEC=150 → 退 2。

    每段 40s 都过了单段下限，段数闸门放行；拦住它的必须是总时长那条。
    """
    assert produce.HL.MIN_TOTAL_SEC == 150
    harness.set_candidates(spans_of(60.0, 3) + spans_of(40.0, 3, start=1000.0))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2"])
    assert e.value.code == produce.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_episode_duration"
    assert payload["episode"] == 2
    assert payload["actual_sec"] == 120.0
    assert payload["threshold_sec"] == 150
    assert_nothing_delivered(harness)


def test_group_exactly_at_min_total_sec_passes(harness):
    """第 2 集 3 段各 50s、合计刚好 150s —— 边界必须放行。"""
    harness.set_candidates(spans_of(60.0, 3) + spans_of(50.0, 3, start=1000.0))
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--episodes", "2"]) == 0
    assert len(read_queue()["episodes"]) == 2


def test_group_one_second_under_min_total_sec_is_refused(harness):
    """合计 149s，差 1s 也拦。"""
    harness.set_candidates(spans_of(60.0, 3)
                           + [(1000.0, 1050.0), (1060.0, 1110.0),
                              (1120.0, 1169.0)])
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2"])
    assert e.value.code == produce.EXIT_QUALITY
    assert_nothing_delivered(harness)


# ── 不许复用同一段 ──────────────────────────────────────────────────────────

def test_overlapping_candidates_cannot_be_reused_to_fill_an_episode(harness):
    """只有 3 段干净素材，其余候选都和它们重叠 —— 不许靠复用凑出第 2 集。"""
    overlapping = [(5.0, 65.0), (10.0, 70.0), (15.0, 75.0), (20.0, 80.0)]
    harness.set_candidates(even_spans(3) + overlapping)
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2"])
    assert e.value.code == produce.EXIT_QUALITY
    assert_nothing_delivered(harness)


def test_passing_episodes_never_share_a_segment(harness):
    """放行的多集之间，源片区间必须两两不相交。"""
    harness.set_candidates(even_spans(9))
    produce.main(["--source", "s.mp4", "--slug", "munger", "--episodes", "3"])
    spans = [(ep["source_start_sec"], ep["source_end_sec"])
             for ep in read_queue()["episodes"]]
    for i, (a, b) in enumerate(spans):
        for c, d in spans[i + 1:]:
            assert not (b > c and a < d), f"{(a, b)} 与 {(c, d)} 复用了素材"


# ── 闸门在花钱之前，且优先于配置校验 ────────────────────────────────────────

def test_gate_fires_before_any_paid_stage(harness):
    """闸门在 highlight 阶段就退，选帧/翻译/拼片一个都不该跑起来。"""
    harness.set_candidates(even_spans(4))
    with pytest.raises(SystemExit):
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2"])
    assert harness.stages == ["input", "transcribe", "highlight"]


def test_gate_beats_the_cover_time_count_check(harness, capsys):
    """钉帧个数和请求集数对得上，但源撑不起 —— 先撞段数闸门，退 2 不是退 1。"""
    harness.set_candidates(even_spans(4))
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2", "--cover-time-sec", "287,412"])
    assert e.value.code == produce.EXIT_QUALITY
    assert rejection(capsys)["stage"] == "highlight"


def test_no_quotes_json_is_written_when_the_gate_refuses(harness):
    """闸门拦住时连中间产物 quotes.json 都不该落盘，免得重跑时被当成合格缓存。"""
    harness.set_candidates(even_spans(4))
    with pytest.raises(SystemExit):
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "3"])
    assert list((Path("_tmp") / "munger").glob("quotes*.json")) == []


# ── 阈值没有被测试悄悄放松 ──────────────────────────────────────────────────

def test_thresholds_are_unchanged():
    """闸门的守卫值不许为了让测试变绿被改动。"""
    assert produce.SEGMENTS == 3
    assert produce.HL.MIN_QUOTES == 3
    assert produce.HL.MIN_QUOTE_SEC == 15
    assert produce.HL.MIN_TOTAL_SEC == 150
    assert produce.EXIT_QUALITY == produce.HL.EXIT_QUALITY == 2


def test_strict_mode_ignores_env_overrides_on_the_episode_gate(monkeypatch):
    """strict 下环境变量放不松闸门 —— CI 靠这个防有人偷偷调阈值。"""
    monkeypatch.setenv("HIGHLIGHT_MIN_QUOTES", "1")
    monkeypatch.setenv("HIGHLIGHT_MIN_TOTAL_SEC", "10")
    monkeypatch.setenv("HIGHLIGHT_MIN_QUOTE_SEC", "1")
    with pytest.raises(SystemExit) as e:
        produce.group_episodes(aligned(even_spans(4)), episodes=2, strict=True)
    assert e.value.code == produce.EXIT_QUALITY
