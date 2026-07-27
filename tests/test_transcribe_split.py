"""中文长段落拆分（scripts/transcribe.py）。

带 ZH_PUNCT_PROMPT 后 whisper 会吐出 100 字级别的整段长句，不拆开会占满
整个画面。拆分必须保住标点（highlight 的句末吸附靠它）和时间轴单调性。
"""

import transcribe as T

LONG = ("因为,巴菲特的投资主要是穿越周期的,人们日常生活必不可少的、"
        "有很深护城河的龙头品牌企业,而且通常一旦持有,就远远跨越宏观经济周期,"
        "所以,短期经济政策变化和市场波动影响不大。有所不为,才能有所为。")


def test_short_chinese_untouched():
    assert T.split_segment("我从来不预测市场。", 1.0, 3.0) == [(1.0, 3.0, "我从来不预测市场。")]


def test_english_untouched_even_when_long():
    t = "The power of compounding lies in time, not in any single year's return, " \
        "and most people never get to see it work."
    assert T.split_segment(t, 0.0, 9.0) == [(0.0, 9.0, t)]


def test_long_chinese_is_split_within_limit():
    parts = T.split_segment(LONG, 0.0, 12.0)
    assert len(parts) > 1
    assert max(len(p[2]) for p in parts) <= T.MAX_CJK_SUB_CHARS


def test_split_preserves_text_exactly():
    parts = T.split_segment(LONG, 0.0, 12.0)
    assert "".join(p[2] for p in parts) == LONG


def test_timeline_is_monotonic_and_bounded():
    parts = T.split_segment(LONG, 5.0, 17.0)
    assert parts[0][0] == 5.0
    assert parts[-1][1] <= 17.0 + 1e-9
    for (s1, e1, _), (s2, _, _) in zip(parts, parts[1:]):
        assert s1 < e1 <= s2 + 1e-9


def test_sentence_punctuation_is_kept_at_piece_end():
    parts = T.split_segment("巴菲特语录三十八。我不研究宏观问题。投资中最紧要的是弄清什么事是重要的。",
                            0.0, 9.0)
    assert any(p[2].endswith("。") for p in parts)


def test_unsplittable_clause_is_returned_whole():
    """没有任何标点的超长中文只能整条留下，不硬切断词。"""
    t = "复利" * 30
    parts = T.split_segment(t, 0.0, 5.0)
    assert parts == [(0.0, 5.0, t)]
