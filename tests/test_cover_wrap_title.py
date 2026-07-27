"""封面标题折行（steps/step7_cover.wrap_title）。

measure 用「每字 1.0」的等宽假设，断言只看行数与字数分布，不依赖字体。
"""

import step7_cover as S


def measure(s):
    return float(len(s))


def wrap(t, limit=20.0):
    return S.wrap_title(t, measure, limit)


def test_short_title_stays_on_one_line():
    assert wrap("巴菲特：看不懂就不碰") == ["巴菲特：看不懂就不碰"]


def test_no_tiny_tail_line():
    """实测烧出过 20 字 + 4 字：理论目标宽 总宽/n 落在词中间就多一行，
    旧实现遇到这种情况直接放弃均衡。"""
    out = wrap("股乾爹：「耐心」不是美德，而是这门生意的入场券？")
    assert len(out) == 2
    assert min(len(x) for x in out) >= max(len(x) for x in out) * 0.7


def test_line_count_is_still_minimal():
    t = "芒格：为什么说「护城河」比增长率更重要，而市场先生总在错误的时间给出错误的价格"
    assert len(wrap(t)) == len(S.wrap_title(t, measure, 20.0))
    assert len(wrap(t)) == 2       # 39 字 / 20 宽，两行装得下


def test_never_exceeds_max_width():
    for t in ["股乾爹：「耐心」不是美德，而是这门生意的入场券？",
              "达利欧：债务周期走到这一步，现金反而是最危险的资产",
              "巴菲特：看不懂的生意，再便宜也不碰（这是纪律）"]:
        assert all(measure(ln) <= 20.0 for ln in wrap(t)), t


def test_text_is_preserved():
    t = "股乾爹：「耐心」不是美德，而是这门生意的入场券？"
    assert "".join(wrap(t)) == t


def test_words_are_not_split():
    out = wrap("芒格：真正的风险是本金永久性损失，不是账面上的上下起伏")
    for w in ["本金", "永久", "账面", "起伏"]:
        assert any(w in ln for ln in out), (w, out)
