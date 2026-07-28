"""段数闸门的守卫测试：把「不允许硬出」这条铁律钉在阈值的边界上。

闸门分三段，这个文件按这三段组织：

1. **CLI 闸门**（``produce.main``）—— ``--episodes`` 非正整数退 1
   （``invalid_episodes``），非整数由 argparse 自己拦，都不许静默当成 1。
2. **单集/首组闸门**（``HL.enforce_quote_thresholds``）—— 先按
   ``MIN_QUOTE_SEC`` 丢掉过短的段，再核可用条数 ``MIN_QUOTES``，最后只拿
   真正会拼进成片的前 ``want`` 段核 ``MIN_TOTAL_SEC``。任何一条不过就退 2。
3. **多集闸门**（``produce.group_episodes``）—— 首组走上面那套，后续每组按
   rank 贪心取满 ``SEGMENTS`` 段，跳过与已选片段重叠的候选，每组各自再核一遍
   段数和总时长。

每条阈值都用「恰好等于 / 减一 / 加一」三档参数化覆盖，边界减一必须是拒绝、
恰好等于必须放行 —— 只测拒绝侧的话，把闸门改严一格是测不出来的。

已知偏差见文件末尾 ``铁律：凑不齐请求集数必须整批退 2`` 一节。
"""

import json
from pathlib import Path

import pytest

import highlight as HL
import produce

# 复用 test_produce_episodes.py 里那套「把整条流水线的外部依赖换成计数器」的
# 夹具，不另起一份 —— 两份 mock 迟早会走样。
from test_produce_episodes import harness  # noqa: F401


# ── 造候选 ──────────────────────────────────────────────────────────────────

def quote(rank, start, dur):
    """align_clips 之后的候选形态，闸门只看 clip_* 这几个字段。"""
    return {"rank": rank, "score": 10.0 - rank,
            "clip_start_sec": float(start),
            "clip_end_sec": float(start) + float(dur),
            "clip_duration_sec": float(dur),
            "duration_sec": float(dur)}


def chain(durs, gap=10.0, start=0.0):
    """按给定时长依次排出互不重叠的候选，rank 从 1 递增。"""
    out, cursor = [], float(start)
    for rank, dur in enumerate(durs, 1):
        out.append(quote(rank, cursor, dur))
        cursor += dur + gap
    return out


def spans_of(quotes):
    return [(q["clip_start_sec"], q["clip_end_sec"]) for q in quotes]


def rejection(capsys):
    """读回 die/reject 打在 stderr 上的那行结构化 JSON。"""
    return json.loads(capsys.readouterr().err.strip().splitlines()[-1])


def two_episode_pool(second_durs):
    """首组 3 段 60s（稳过闸门）+ 留给第 2 集的候选，rank 连号排下去。"""
    quotes = chain([60.0] * produce.SEGMENTS) + chain(second_durs, start=1000.0)
    for rank, q in enumerate(quotes, 1):
        q["rank"] = rank
    return quotes


def episodes_produced(quotes, episodes):
    """实际产出的集数；闸门以退 2 拒绝整批时返回 ``None``。

    多集闸门当前是「少出几集再退 0」，铁律要求的是「整批退 2」（见文件末尾那
    节）。下面几条边界用例关心的是「不合格的那一集有没有被产出」，这个问题在两
    种行为下答案一样，所以用这个包装把差异吸收掉 —— 免得铁律修好那天，本该继续
    守住边界的用例反倒要跟着改。
    """
    try:
        return len(produce.group_episodes(quotes, episodes=episodes,
                                          strict=True))
    except SystemExit as e:
        assert e.code == produce.EXIT_QUALITY
        return None


# ── 阈值常量本身不许被调松 ──────────────────────────────────────────────────

def test_gate_constants_are_pinned():
    """下面所有边界用例都是照这几个数算出来的，改常量必须先改这里。"""
    assert produce.SEGMENTS == 3
    assert HL.MIN_QUOTES == 3
    assert HL.MIN_QUOTE_SEC == 15
    assert HL.MIN_TOTAL_SEC == 150
    assert HL.EXIT_QUALITY == produce.EXIT_QUALITY == 2
    assert produce.EXIT_CONFIG == 1


# ── 1. CLI 闸门：--episodes 的非法取值 ──────────────────────────────────────

@pytest.fixture
def no_pipeline(tmp_path, monkeypatch):
    """CLI 闸门必须在取源之前就拦住，走到取源即判失败。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")

    def boom(*a, **k):
        raise AssertionError("闸门没拦住：不该走到取源")

    monkeypatch.setattr(produce, "resolve_source", boom)
    return tmp_path


@pytest.mark.parametrize("raw", ["0", "-1", "-3"])
def test_non_positive_episodes_is_a_config_error(raw, no_pipeline, capsys):
    """0 和负数都得明确报错，不许悄悄兜底成 1 集。"""
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", raw])
    assert e.value.code == produce.EXIT_CONFIG
    payload = rejection(capsys)
    assert payload["reason"] == "invalid_episodes"
    assert payload["episodes"] == int(raw)
    assert not (no_pipeline / "deliver").exists()


@pytest.mark.parametrize("raw", ["2.5", "abc", "", " ", "3x", "1e2", "3,4"])
def test_non_integer_episodes_is_refused_by_the_parser(raw, capsys):
    """非整数由 argparse 拦下 —— 关键是它不能被解析成某个数字继续往下跑。"""
    with pytest.raises(SystemExit) as e:
        produce.parse_args(["--source", "v.mp4", "--slug", "s",
                            "--episodes", raw])
    # argparse 的用法错误固定退 2，和 EXIT_QUALITY 撞号；这里只能核 stderr
    assert e.value.code != 0
    assert "--episodes" in capsys.readouterr().err


@pytest.mark.parametrize("raw,want", [("1", 1), ("2", 2), ("10", 10)])
def test_positive_episodes_values_parse(raw, want):
    episodes = produce.parse_args(["--source", "v.mp4", "--slug", "s",
                                   "--episodes", raw]).episodes
    # 类型也要核：float 化之后 2.5 会被静默收下，而 2.0 == 2 掩盖得住
    assert isinstance(episodes, int) and episodes == want


# ── 2. 单集/首组闸门的三条阈值 ──────────────────────────────────────────────
# 三档取值都写成「相对阈值」的形式，改阈值时这些用例会跟着 pinning 测试一起红。

@pytest.mark.parametrize("count,ok", [
    (HL.MIN_QUOTES - 1, False),
    (HL.MIN_QUOTES, True),
    (HL.MIN_QUOTES + 1, True),
])
def test_usable_candidate_count_boundary(count, ok, capsys):
    """可用候选条数：差一条就退 2，恰好够必须放行。

    每段 60s，条数一到 3 总时长就有 180s，时长闸门不会顺带兜住这一档。
    """
    quotes = chain([60.0] * count)
    if ok:
        got = HL.enforce_quote_thresholds(quotes, want=produce.SEGMENTS,
                                          strict=True)
        assert len(got) == produce.SEGMENTS
        return
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds(quotes, want=produce.SEGMENTS, strict=True)
    assert e.value.code == HL.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_quotes"
    assert payload["actual_count"] == count
    assert payload["threshold_count"] == HL.MIN_QUOTES


@pytest.mark.parametrize("dur,ok", [
    (HL.MIN_QUOTE_SEC - 1, False),
    (HL.MIN_QUOTE_SEC, True),
    (HL.MIN_QUOTE_SEC + 1, True),
])
def test_min_quote_sec_boundary(dur, ok, capsys):
    """单段时长下限：短一秒的段落要被丢掉，丢完不够 3 条就退 2。

    前两段各 70s，第三段卡在阈值上 —— 恰好 15s 时总时长 155s 过线，所以这一档
    只可能被单段时长闸门拦，不会和总时长闸门混在一起。
    """
    quotes = chain([70.0, 70.0, float(dur)])
    if ok:
        got = HL.enforce_quote_thresholds(quotes, want=produce.SEGMENTS,
                                          strict=True)
        assert [q["clip_duration_sec"] for q in got] == [70.0, 70.0, float(dur)]
        return
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds(quotes, want=produce.SEGMENTS, strict=True)
    assert e.value.code == HL.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_quotes"
    # 三条候选里那条短的被丢了，只剩两条可用
    assert payload["actual_count"] == 2
    assert payload["candidates"] == 3


@pytest.mark.parametrize("total,ok", [
    (HL.MIN_TOTAL_SEC - 1, False),
    (HL.MIN_TOTAL_SEC, True),
    (HL.MIN_TOTAL_SEC + 1, True),
])
def test_min_total_sec_boundary(total, ok, capsys):
    """成片总时长下限：差一秒就退 2，恰好等于必须放行。

    固定 3 段、每段都远高于单段下限，只让合计时长在阈值上下挪一秒。
    """
    quotes = chain([50.0, 50.0, float(total) - 100.0])
    if ok:
        got = HL.enforce_quote_thresholds(quotes, want=produce.SEGMENTS,
                                          strict=True)
        assert sum(q["clip_duration_sec"] for q in got) == float(total)
        return
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds(quotes, want=produce.SEGMENTS, strict=True)
    assert e.value.code == HL.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_duration"
    assert payload["actual_sec"] == float(total)
    assert payload["threshold_sec"] == HL.MIN_TOTAL_SEC
    assert payload["selected"] == produce.SEGMENTS


def test_total_duration_counts_only_the_segments_that_ship(capsys):
    """总时长要按真正会拼进成片的前 SEGMENTS 段算，不能拿全部候选凑。

    5 段各 40s 合计 200s 看着够，但成片只拼前 3 段 = 120s，不到 150s。
    把总时长改成对全部候选求和，这组就会被当成合格的一集发出去。
    """
    quotes = chain([40.0] * 5)
    with pytest.raises(SystemExit) as e:
        HL.enforce_quote_thresholds(quotes, want=produce.SEGMENTS, strict=True)
    assert e.value.code == HL.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_duration"
    assert payload["actual_sec"] == 120.0
    assert payload["selected"] == produce.SEGMENTS


def test_first_group_is_trimmed_to_exactly_segments():
    """候选再多，首组也只拿 SEGMENTS 段，多拿等于把别集的素材吃掉。"""
    got = HL.enforce_quote_thresholds(chain([60.0] * 12),
                                      want=produce.SEGMENTS, strict=True)
    assert [q["rank"] for q in got] == [1, 2, 3]


# ── 3. 多集闸门：每集段数、每集各自过阈值、集间不复用 ────────────────────────

def test_every_episode_gets_exactly_segments_quotes():
    """每集恰好 SEGMENTS 段，不多不少。"""
    groups = produce.group_episodes(chain([60.0] * 9), episodes=3, strict=True)
    assert len(groups) == 3
    assert [len(g) for g in groups] == [produce.SEGMENTS] * 3


def test_surplus_candidates_do_not_inflate_an_episode():
    """候选远多于需要时，也不能把多的塞进某一集。"""
    groups = produce.group_episodes(chain([60.0] * 20), episodes=2, strict=True)
    assert [len(g) for g in groups] == [produce.SEGMENTS] * 2


@pytest.mark.parametrize("extra,ok", [
    (produce.SEGMENTS - 1, False),
    (produce.SEGMENTS, True),
    (produce.SEGMENTS + 1, True),
])
def test_second_episode_segment_count_boundary(extra, ok):
    """第 2 集的段数闸门是独立承重的，不能靠时长闸门顺带兜住。

    首组吃掉 3 段，留给第 2 集 ``extra`` 段、每段 80s —— 只剩 2 段时合计 160s
    已经过了 150s 的时长闸门，唯一能拦住它的就是段数闸门。
    """
    quotes = two_episode_pool([80.0] * extra)
    if not ok:
        assert episodes_produced(quotes, 2) in (None, 1)
        return
    groups = produce.group_episodes(quotes, episodes=2, strict=True)
    assert len(groups) == 2
    assert len(groups[1]) == produce.SEGMENTS


@pytest.mark.parametrize("dur,ok", [
    (HL.MIN_QUOTE_SEC - 1, False),
    (HL.MIN_QUOTE_SEC, True),
    (HL.MIN_QUOTE_SEC + 1, True),
])
def test_second_episode_min_quote_sec_boundary(dur, ok):
    """第 2 集也要按单段时长下限筛候选，短段不能被拉来充数。

    第 2 集拿到两段 70s 加一段卡在阈值上的 —— 恰好 15s 时合计 155s 过线。
    """
    quotes = two_episode_pool([70.0, 70.0, float(dur)])
    if not ok:
        assert episodes_produced(quotes, 2) in (None, 1)
        return
    groups = produce.group_episodes(quotes, episodes=2, strict=True)
    assert len(groups) == 2
    assert [q["clip_duration_sec"] for q in groups[1]] == \
        [70.0, 70.0, float(dur)]


@pytest.mark.parametrize("total,ok", [
    (HL.MIN_TOTAL_SEC - 1, False),
    (HL.MIN_TOTAL_SEC, True),
    (HL.MIN_TOTAL_SEC + 1, True),
])
def test_second_episode_min_total_sec_boundary(total, ok):
    """第 2 集的总时长下限：差一秒就不能算一集。"""
    quotes = two_episode_pool([50.0, 50.0, float(total) - 100.0])
    if not ok:
        assert episodes_produced(quotes, 2) in (None, 1)
        return
    groups = produce.group_episodes(quotes, episodes=2, strict=True)
    assert len(groups) == 2
    assert sum(q["clip_duration_sec"] for q in groups[1]) == float(total)


def test_no_segment_is_shared_between_episodes():
    """集与集之间不许复用素材：同一段被两集拿走就是两条片子放同一段画面。"""
    groups = produce.group_episodes(chain([60.0] * 9), episodes=3, strict=True)
    picked = [q for g in groups for q in g]
    spans = spans_of(picked)
    assert len(spans) == len(set(spans))
    assert sorted(q["rank"] for q in picked) == list(range(1, 10))
    # 对象身份也不能重合 —— 同一个 dict 出现在两组里同样是复用
    assert len({id(q) for q in picked}) == len(picked)


def test_episode_spans_are_pairwise_disjoint():
    """候选里混着与已用片段重叠的段落，它们不能被后面的集捡走。

    rank 4 压在第 1 集 rank 1 的区间里。跳过重叠的判定一失效，第 2 集就会拿到
    一段和第 1 集画面重复的素材。
    """
    quotes = chain([60.0] * 3)                       # 0-60 / 70-130 / 140-200
    quotes.append(quote(4, 10.0, 40.0))              # 10-50，压在 rank 1 里
    quotes += [quote(5, 1000.0, 60.0), quote(6, 1070.0, 60.0),
               quote(7, 1140.0, 60.0)]
    groups = produce.group_episodes(quotes, episodes=2, strict=True)

    assert len(groups) == 2
    assert [q["rank"] for q in groups[1]] == [5, 6, 7]
    spans = spans_of([q for g in groups for q in g])
    for i, (a, b) in enumerate(spans):
        for c, d in spans[i + 1:]:
            assert not (b > c and a < d), f"{(a, b)} 与 {(c, d)} 重叠"


def test_single_episode_path_returns_one_group_of_segments():
    """``--episodes 1`` 候选再多也只出一组，且就是首组那 SEGMENTS 段。"""
    groups = produce.group_episodes(chain([60.0] * 9), episodes=1, strict=True)
    assert len(groups) == 1
    assert [q["rank"] for q in groups[0]] == [1, 2, 3]


# ── 端到端：跑完整条流水线，核产出的集数与段数 ──────────────────────────────

def test_enough_material_delivers_exactly_the_requested_episodes(harness):  # noqa: F811
    """素材刚好够 3 集（9 段 × 60s）→ 正常出 3 集，每集 SEGMENTS 段。"""
    harness.set_candidates([(i * 70.0, i * 70.0 + 60.0) for i in range(9)])
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--episodes", "3"]) == 0

    queue = json.loads((Path("deliver") / "munger" / "queue.json")
                       .read_text(encoding="utf-8"))
    assert [ep["id"] for ep in queue["episodes"]] == ["ep01", "ep02", "ep03"]
    for eid in ("ep01", "ep02", "ep03"):
        meta = json.loads((Path("deliver") / "munger" / eid /
                           "meta.json").read_text(encoding="utf-8"))
        assert meta["segment_count"] == produce.SEGMENTS


def test_delivered_episodes_do_not_reuse_source_material(harness):  # noqa: F811
    """端到端核一遍：queue.json 里三集的源区间两两不重叠。"""
    harness.set_candidates([(i * 70.0, i * 70.0 + 60.0) for i in range(9)])
    produce.main(["--source", "s.mp4", "--slug", "munger", "--episodes", "3"])

    queue = json.loads((Path("deliver") / "munger" / "queue.json")
                       .read_text(encoding="utf-8"))
    spans = [(ep["source_start_sec"], ep["source_end_sec"])
             for ep in queue["episodes"]]
    assert len(spans) == len(set(spans)) == 3
    for i, (a, b) in enumerate(spans):
        for c, d in spans[i + 1:]:
            assert not (b > c and a < d), f"{(a, b)} 与 {(c, d)} 重叠"


def test_no_group_at_all_is_a_quality_rejection(harness, capsys):  # noqa: F811
    """一组都凑不出来 → 退 2，且选帧/翻译/拼片一分钱不花。"""
    harness.set_candidates([(0.0, 60.0), (70.0, 130.0)])
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "3"])
    assert e.value.code == produce.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["stage"] == "highlight"
    assert payload["reason"] == "insufficient_quotes"
    assert harness.calls["cover_select"] == 0
    assert harness.calls["translate"] == 0
    assert harness.calls["assemble"] == 0


# ── 铁律：凑不齐请求集数必须整批退 2 ────────────────────────────────────────
# 已知偏差。当前 main 的 group_episodes 在填不满一组时 break 掉，返回 M<N 组
# 再退 0（源码注释写的是「能出几组算几组」），main() 也没有把 len(groups) 和
# args.episodes 核过。下游按 --episodes N 排了 N 天档期，静默回 M<N 集会被当成
# 正常结果收下 —— 按项目铁律这也是一种硬出。
#
# 这几条按铁律要求的行为写，用 strict xfail 挂着：修好之后它们会 XPASS，
# strict=True 会让 CI 变红，提醒把标记删掉。不在这个 PR 里改生产代码。
IRON_RULE = ("已知偏差：group_episodes 凑不齐请求集数时少出几集再退 0，"
             "铁律要求整批退 2（EXIT_QUALITY）")


@pytest.mark.xfail(reason=IRON_RULE, strict=True)
@pytest.mark.parametrize("usable", [
    produce.SEGMENTS * 3 - 1,       # 差一段就够 3 集
    produce.SEGMENTS * 2,           # 只够 2 集
    produce.SEGMENTS,               # 只够 1 集
])
def test_one_short_of_the_boundary_refuses_instead_of_dropping_episodes(usable):
    """素材差一点点 → 应当退 2，而不是少出几集。"""
    with pytest.raises(SystemExit) as e:
        produce.group_episodes(chain([60.0] * usable), episodes=3, strict=True)
    assert e.value.code == produce.EXIT_QUALITY


@pytest.mark.xfail(reason=IRON_RULE, strict=True)
def test_requesting_more_episodes_than_the_source_supports_refuses(harness):  # noqa: F811
    """请求集数超过素材上限 → 应当退 2，且不产出任何一集。"""
    harness.set_candidates([(i * 70.0, i * 70.0 + 60.0) for i in range(4)])
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "3"])
    assert e.value.code == produce.EXIT_QUALITY
    assert harness.calls["assemble"] == 0


@pytest.mark.xfail(reason=IRON_RULE, strict=True)
def test_a_short_episode_is_refused_rather_than_dropped(harness):  # noqa: F811
    """第 2 集只凑得出 2 段（时长够、段数不够）→ 应当退 2，不是只出 1 集。"""
    harness.set_candidates([(0.0, 60.0), (70.0, 130.0), (140.0, 200.0),
                            (1000.0, 1080.0), (1090.0, 1170.0)])
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2"])
    assert e.value.code == produce.EXIT_QUALITY
