"""中文字幕折行（scripts/clip.py）。"""

import clip as C


def lines(t, n=21):
    return C.wrap_text(t, n, is_cjk=True).split(r"\N")


def test_short_line_not_wrapped():
    assert lines("我从来不预测市场。") == ["我从来不预测市场。"]


def test_no_orphan_trailing_period():
    """22 字 / 21 上限：定长硬切会留下只有「。」的末行。"""
    out = lines("复利的力量在于时间，而不在于某一年的收益率。")
    assert len(out) == 2
    assert all(len(x) > 1 for x in out)


def test_lines_are_roughly_balanced():
    """不追求逐字均分——词边界优先，只要别出现「满满一行 + 小尾巴」。"""
    out = lines("大多数人亏钱，是因为把价格波动当成了真正的风险。")
    assert len(out) == 2
    assert min(len(x) for x in out) >= max(len(x) for x in out) * 0.6


def test_words_are_not_split_across_lines():
    """定长硬切会烧出「…是因为把价 / 格波动当成了…」。"""
    out = lines("大多数人亏钱，是因为把价格波动当成了真正的风险。")
    assert "".join(out) == "大多数人亏钱，是因为把价格波动当成了真正的风险。"
    for w in ["价格", "波动", "真正", "风险"]:
        assert any(w in ln for ln in out), (w, out)


def test_latin_words_inside_cjk_line_are_not_split():
    """中英混排整条走 CJK 分支，旧的定长硬切会劈出 'tate Manufacturin'。"""
    t = "这个调查叫做 Empire State Manufacturing Survey，"
    out = lines(t)
    assert "".join(out) == t
    for w in ["Empire", "State", "Manufacturing", "Survey"]:
        assert any(w in ln for ln in out), (w, out)


def test_no_line_exceeds_limit():
    for t in ["大多数人亏钱，是因为把价格波动当成了真正的风险。",
              "5月16日公布的 Empire State Manufacturing Survey Index，",
              "真正的风险是本金永久性损失，不是账面上的上下起伏，这一点非常重要。"]:
        assert all(len(ln) <= 21 for ln in lines(t)), t


def test_no_line_starts_with_forbidden_punct():
    for t in ["复利的力量在于时间，而不在于某一年的收益率。",
              "市场先生每天都报价，但你没有义务每天都成交。",
              "真正的风险是本金永久性损失，不是账面上的上下起伏，这一点非常重要。"]:
        for ln in lines(t):
            assert ln[0] not in C.LINE_START_FORBIDDEN, (t, ln)


def test_line_count_is_minimal():
    t = "真正的风险是本金永久性损失，不是账面上的上下起伏，这一点非常非常重要。"
    assert len(lines(t)) == -(-len(t) // 21)


def test_english_still_wraps_on_words():
    out = C.wrap_text("The power of compounding lies in time, not in any single year",
                      30, is_cjk=False).split(r"\N")
    assert all(not x.startswith(" ") for x in out)
    assert " ".join(out).split() == \
        "The power of compounding lies in time, not in any single year".split()


def test_no_tiny_tail_line():
    """总长/n 落在词中间时旧实现放弃均衡，烧出「满满一行 + 小尾巴」。"""
    out = lines("市场先生每天都给你报价，但你并没有义务在任何一天成交。")
    assert len(out) == 2
    assert min(len(x) for x in out) >= max(len(x) for x in out) * 0.7
