"""段数闸门的**边界守卫**：把「不允许硬出」这条铁律钉在阈值的正负一格上。

## 这个文件负责什么（和隔壁三个文件的分工）

段数闸门有四个测试文件，分工按「问什么问题」切，不按「测哪个函数」切：

* ``test_highlight_thresholds.py`` —— 单集闸门 ``HL.enforce_quote_thresholds``
  的基本行为，以及环境变量放松 / ``strict`` 收紧那套开关。
* ``test_produce_episodes.py`` —— 多集出片的**功能回归**：目录布局、阶段顺序、
  每集各自的标题与 meta、``queue.json`` 的 schema、``--cover-time-sec`` 的
  逐集映射。闸门在这里只是顺带被撞到。
* ``test_episode_gate.py`` —— 闸门的**行为契约**：走 ``produce.main`` 的端到端
  拒绝语义、拒绝理由 JSON 里的字段、「凑不齐就整批退 2、一集都不出」、闸门必须
  在花钱的阶段之前触发。回答「拒绝的时候会发生什么」。
* **本文件** —— 闸门的**边界**：每条阈值的「减一 / 恰好等于 / 加一」三档参数化，
  ``--episodes`` 这个 CLI 入参本身的校验，以及函数层的不变量（每组恰好
  ``SEGMENTS`` 段、集与集之间不复用素材）。回答「界线画在哪一格」。

**新增用例前先按这个分工挑文件。**只测拒绝侧是不够的 —— 那样把闸门改严一格
（``>=`` 改成 ``>``）跑出来还是绿的，所以这里的每条边界都必须同时有「减一拒绝」
和「恰好等于放行」两侧。反过来，端到端只跑一遍拒绝就够了，不必在这里重跑。

## 闸门本身分三段，下面的小节按这三段排

1. **CLI 闸门**（``produce.main`` / ``produce.parse_args``）—— ``--episodes``
   非正整数退 1（``invalid_episodes``），非整数由 argparse 自己拦，都不许静默
   当成 1 集。
2. **单集 / 首组闸门**（``HL.enforce_quote_thresholds``）—— 先按
   ``MIN_QUOTE_SEC`` 丢掉过短的段，再核可用条数 ``MIN_QUOTES``，最后只拿
   真正会拼进成片的前 ``want`` 段核 ``MIN_TOTAL_SEC``。任何一条不过就退 2。
3. **多集闸门**（``produce.group_episodes``）—— 首组走上面那套，后续每组按
   rank 贪心取满 ``SEGMENTS`` 段，跳过与已选片段重叠的候选，每组各自再核一遍
   段数和总时长；凑不齐请求的集数就整批退 2。
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


def refuses(quotes, episodes):
    """凑不齐就整批退 2 —— 断言拒绝，并把拒绝理由交给调用方核。"""
    with pytest.raises(SystemExit) as e:
        produce.group_episodes(quotes, episodes=episodes, strict=True)
    assert e.value.code == produce.EXIT_QUALITY


# ── 退出码常量 ──────────────────────────────────────────────────────────────
# 四条阈值和 EXIT_QUALITY 由 test_episode_gate.py::test_thresholds_are_unchanged
# 钉死，这里不重复。EXIT_CONFIG 只有下面的 CLI 小节用得上，就放在这。

def test_exit_config_is_one():
    """``--episodes`` 的入参错误算配置错，退 1，不能和内容质量拒绝混成一码。"""
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


# 首组「候选再多也只拿 SEGMENTS 段」由
# test_highlight_thresholds.py::test_passing_set_is_returned_trimmed_to_want 覆盖。


# ── 3. 多集闸门：每集段数、每集各自过阈值、集间不复用 ────────────────────────
# 「恰好 SEGMENTS×N 段时每组都填满」由
# test_episode_gate.py::test_exactly_enough_at_the_unit_level_fills_every_group
# 覆盖（那条还额外钉死了 rank 的排布），这里只测它没覆盖的「候选有富余」一侧。

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
        refuses(quotes, 2)
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
        refuses(quotes, 2)
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
        refuses(quotes, 2)
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


# ``episodes=1`` 只出一组，由
# test_produce_episodes.py::test_group_episodes_default_returns_exactly_one_group
# 覆盖（默认值就是 1，走的是同一条 ``episodes <= 1`` 分支）。


# ── 端到端：只留其它文件没在成品层面核过的那两条 ────────────────────────────
# 「素材够就出满 N 集」「集间不复用素材」由 test_episode_gate.py 的
# test_exactly_enough_segments_passes / test_passing_episodes_never_share_a_segment
# 端到端核过了，这里不重跑。

def test_every_delivered_episode_carries_exactly_segments(harness):  # noqa: F811
    """每集的 meta.json 都要写着 SEGMENTS 段 —— 在成品层面核「不多不少」。

    上面那些用例核的是 ``group_episodes`` 的返回值；这条核的是真的落到
    ``deliver/<slug>/epNN/meta.json`` 里的段数。分组之后到写 meta 之间还有一段
    路，某一集被悄悄削掉一段的话只有这里看得见。
    """
    harness.set_candidates([(i * 70.0, i * 70.0 + 60.0) for i in range(9)])
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--episodes", "3"]) == 0

    for eid in ("ep01", "ep02", "ep03"):
        meta = json.loads((Path("deliver") / "munger" / eid /
                           "meta.json").read_text(encoding="utf-8"))
        assert meta["segment_count"] == produce.SEGMENTS, f"{eid} 段数不对"


def test_first_group_failure_keeps_the_single_episode_reason(harness, capsys):  # noqa: F811
    """连首组都凑不出时，拒绝理由必须还是单集那套 ``insufficient_quotes``。

    首组是直接交给 ``HL.enforce_quote_thresholds`` 的，多集路径不能把它的拒绝
    理由改写成 ``insufficient_episode_quotes`` —— 运维靠这个词区分「源片整体不
    行」和「只是撑不起这么多集」。退 2 本身由 test_episode_gate.py 覆盖，这里
    只钉理由。
    """
    harness.set_candidates([(0.0, 60.0), (70.0, 130.0)])
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "3"])
    assert e.value.code == produce.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["stage"] == "highlight"
    assert payload["reason"] == "insufficient_quotes"


# ── 铁律：凑不齐请求集数必须整批退 2 ────────────────────────────────────────
# 这一节原先是 5 条 strict xfail —— 写的时候 group_episodes 还是「填不满就 break
# 掉、返回 M<N 组再退 0」，所以只能把铁律要求的行为挂起来等修复。PR #21 已经把
# 它改成整批退 2，标记随之摘掉。
#
# 摘标记时顺带做了去重：「差一段 / 只够 2 集 / 只够 1 集」这三档和请求集数超过
# 素材上限那条，都被 test_episode_gate.py 里更严的版本覆盖了（那边连拒绝理由的
# 字段和「一集都没落盘」都核了）。剩下这条是那批里唯一独有的场景。

def test_a_short_episode_is_refused_rather_than_dropped(harness, capsys):  # noqa: F811
    """第 2 集**时长够、段数不够** → 退 2，不是只出 1 集。

    后两段各 80s，合计 160s 已经过了 150s 的时长闸门 —— 唯一能拦住它的是段数
    闸门。test_episode_gate.py 那几条端到端用的是 60s 等长段，两条闸门会同时
    触发，兜不住「段数闸门被摘掉」这种改动。
    """
    harness.set_candidates([(0.0, 60.0), (70.0, 130.0), (140.0, 200.0),
                            (1000.0, 1080.0), (1090.0, 1170.0)])
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger",
                      "--episodes", "2"])
    assert e.value.code == produce.EXIT_QUALITY
    payload = rejection(capsys)
    assert payload["reason"] == "insufficient_episode_quotes"
    assert payload["actual_count"] == 2 and payload["episode"] == 2
    assert harness.calls["assemble"] == 0
