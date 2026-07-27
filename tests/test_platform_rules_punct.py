"""中文标点规范化（scripts/platform_rules.py）。"""

import platform_rules as R

n = R.normalize_cjk_punctuation


# ── 弯引号 → 直角引号 ────────────────────────────────────────────────────────

def test_double_curly_quotes_become_corner_brackets():
    assert n("他说“价值投资”很重要") == "他说「价值投资」很重要"


def test_single_curly_quotes_become_white_corner_brackets():
    assert n("所谓‘安全边际’") == "所谓『安全边际』"


def test_nested_quotes_outer_corner_inner_white():
    assert n("他说“书里写着“复利”这两个字”") == "他说「书里写着『复利』这两个字」"


def test_unpaired_closing_quote_left_alone():
    # 被截断的文案里常见落单引号，强行替换会得到没有下引号的「
    assert n("复利”") == "复利”"


def test_existing_corner_brackets_untouched():
    assert n("「安全边际」和『复利』") == "「安全边际」和『复利』"


# ── 半角 → 全角 ─────────────────────────────────────────────────────────────

def test_halfwidth_punctuation_in_chinese_becomes_fullwidth():
    assert n("这很重要,真的很重要;你懂吗?懂了!") == "这很重要，真的很重要；你懂吗？懂了！"


def test_halfwidth_parens_around_chinese():
    assert n("巴菲特(股神)说过") == "巴菲特（股神）说过"


def test_colon_in_chinese_context():
    assert n("结论:长期持有") == "结论：长期持有"


# ── 受保护片段：URL / 数字 / 英文 ────────────────────────────────────────────

def test_url_punctuation_never_converted():
    src = "原视频出处:https://youtu.be/abc?t=120&x=1"
    assert "https://youtu.be/abc?t=120&x=1" in n(src)
    assert n(src).startswith("原视频出处：")


def test_url_does_not_swallow_following_chinese():
    # 中文和 URL 之间常常不加空格，贪婪匹配会把后半句一起圈进保护区，
    # 那句里的半角标点就转不成全角了
    out = n("完整视频见 https://youtu.be/abc?t=120&list=1,建议配合年报阅读")
    assert out == "完整视频见 https://youtu.be/abc?t=120&list=1，建议配合年报阅读"


def test_punctuation_right_after_url_is_the_sentence_s():
    assert n("见 www.example.com/a?x=1,谢谢") == "见 www.example.com/a?x=1，谢谢"


def test_bare_domain_url_protected():
    assert "www.berkshirehathaway.com" in n("见 www.berkshirehathaway.com 官网")


def test_email_protected():
    assert "hi@example.com" in n("联系 hi@example.com 咨询")


def test_thousands_separator_and_decimal_protected():
    assert n("成本是1,234.56美元") == "成本是1,234.56美元"


def test_time_and_ratio_protected():
    assert n("会议12:30开始") == "会议12:30开始"


def test_english_fragment_punctuation_protected():
    assert "Berkshire, Inc" in n("公司叫 Berkshire, Inc 的那家")


def test_ticker_style_text_protected():
    out = n("代码 BRK.B, AAPL 都涨了")
    assert "BRK.B, AAPL" in out


# ── 重复标点折叠 ─────────────────────────────────────────────────────────────

def test_repeated_full_stops_become_ellipsis():
    assert n("然后呢。。。") == "然后呢……"


def test_repeated_exclamations_collapse():
    assert n("太狠了!!") == "太狠了！"
    assert n("太狠了！！！") == "太狠了！"


def test_repeated_question_marks_collapse():
    assert n("真的吗??") == "真的吗？"


def test_mixed_bang_question_collapses_to_first():
    assert n("什么？！！") == "什么？"


def test_long_ellipsis_normalised():
    assert n("等等………") == "等等……"


def test_repeated_commas_collapse():
    assert n("等等，，然后呢") == "等等，然后呢"


# ── 空格处理 ─────────────────────────────────────────────────────────────────

def test_space_before_fullwidth_punctuation_removed():
    assert n("他说过 ，这很关键") == "他说过，这很关键"


def test_single_space_kept_between_chinese_and_english():
    assert n("买入 Berkshire 的逻辑") == "买入 Berkshire 的逻辑"


def test_multiple_spaces_between_chinese_and_english_collapse_to_one():
    assert n("买入    Berkshire    的逻辑") == "买入 Berkshire 的逻辑"


def test_newlines_in_body_preserved():
    assert n("第一段。\n\n第二段。") == "第一段。\n\n第二段。"


# ── 空输入 ───────────────────────────────────────────────────────────────────

def test_empty_and_none_pass_through():
    assert n("") == ""
    assert n(None) is None


# ── 接入标题 / 简介输出路径 ──────────────────────────────────────────────────

def test_clean_title_applies_normalisation():
    title, _ = R.clean_title("李录:“护城河”到底是什么?")
    assert title == "李录：「护城河」到底是什么？"


def test_build_desc_normalises_body_but_keeps_source_url():
    url = "https://youtu.be/abc?t=1"
    desc, _ = R.build_desc("他说“长期主义”很重要,真的!", source=url)
    assert "他说「长期主义」很重要，真的！" in desc
    assert url in desc


def test_build_desc_keeps_paragraph_breaks():
    desc, _ = R.build_desc("正文", source="https://youtu.be/abc")
    assert "\n\n" in desc
