"""produce.py::make_title 剥离结果的残留物合法性校验（真实 CI 缺陷修复）。

真实 CI run 30908623000 日志第 2288 行复现的缺陷：

    [title] 标题含溯源不到的数字，已确定性降级为「帕伯莱谈投资中的「」与亏损」

PR #36 的 ``_strip_fabricated_numbers()`` 只删掉溯源不到的数字片段本身，没有
清理留下的空引号壳——模型给「帕伯莱谈投资中的『40%』与亏损」，「40%」溯源
不到被剥空，留下一对空引号「」，被当成合法标题放行，最终烧进了 ep01 封面。

这里覆盖：
  1. 精确复现线上缺陷的场景：数字被引号包裹、原文不含该数字、重试也返回
     同样的坏标题，断言最终标题不含任何空成对符号，也不含孤立单引号；
  2. 剥离后残留不成对引号的场景 → 断言退到安全标题；
  3. 剥离后干净自然的场景 → 断言采用剥离结果（不矫枉过正）；
  4. 任何情况下最终标题都含讲者名；
  5. 任何情况下最终标题不含溯源不到的数字（PR #36 的既有保证不能被破坏）。

不自证：断言用到的字符串/长度都是独立选定的字面量，不会拿 produce.py 里的
TITLE_* 常量或本次新增的校验逻辑当测试输入去反推期望值。
"""
import produce


def _quotes(text):
    return [{"transcript_zh": text}, {"transcript_zh": text}, {"transcript_zh": text}]


def _no_empty_paired_symbols(text):
    """字面量检查：不含任何空成对符号，也不含孤立的单个引号。"""
    empty_pairs = ["「」", "『』", "（）", "()", "《》", "【】", '""', "''"]
    for pair in empty_pairs:
        if pair in text:
            return False
    # 孤立单引号：左右符号数量不一致
    for left, right in [("「", "」"), ("『", "』"), ("（", "）"), ("(", ")"),
                         ("《", "》"), ("【", "】")]:
        if text.count(left) != text.count(right):
            return False
    for ch in ('"', "'"):
        if text.count(ch) % 2 != 0:
            return False
    return True


def test_ci_regression_number_wrapped_in_quotes_leaves_no_empty_shell(monkeypatch):
    """精确复现 CI run 30908623000：数字被『』包裹、原文没有这个数字、重试
    （TITLE_NUMBER_RETRY 次）仍返回同一个坏标题——强制走降级路径。

    最终标题不能出现任何空成对符号（「」『』等），也不能有孤立的单个引号。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱谈投资中的『40%』与亏损"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱谈投资中的亏损，反思仓位管理的教训，通篇没有出现任何具体数字")
    quotes[0]["title_suggestion"] = "投资中的亏损教训"
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)

    assert "「」" not in title and "『』" not in title, (
        f"标题「{title}」里出现了空成对引号壳，线上缺陷未被修复")
    assert _no_empty_paired_symbols(title), (
        f"标题「{title}」里含空壳或孤立单引号")
    assert "帕伯莱" in title
    assert not produce._title_has_digit_or_percent(title), (
        f"标题「{title}」仍带有阿拉伯数字或百分号")


def test_ci_regression_corner_quote_form_also_leaves_no_empty_shell(monkeypatch):
    """同一缺陷的外层「」引号变体（模型偏好不一致时也可能直接用外层符号）。"""
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱谈投资中的「40%」与亏损"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱谈投资中的亏损，通篇没有出现任何具体数字")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)

    assert "「」" not in title and "『』" not in title
    assert _no_empty_paired_symbols(title), f"标题「{title}」里含空壳或孤立单引号"
    assert "帕伯莱" in title
    assert not produce._title_has_digit_or_percent(title)


def test_residue_with_unbalanced_quote_falls_back_to_safe_title(monkeypatch):
    """剥离只吃掉数字，留下一个孤立的单个「（没有配对的右引号）——

    这种残留不能靠"反正字数够"之类的巧合放行，必须整体退回安全标题。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱谈投资中的「40%回报"   # 引号只在数字前，删完留下孤立「

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱谈投资中的回报，反思长期持有的耐心，通篇没有出现任何具体数字")
    quotes[0]["title_suggestion"] = "投资中的长期耐心"
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)

    assert "「" not in title and "」" not in title, (
        f"标题「{title}」仍残留孤立引号，没有退回安全标题")
    assert "帕伯莱" in title
    assert not produce._title_has_digit_or_percent(title)


def test_clean_strip_result_without_quotes_is_kept_not_over_corrected(monkeypatch):
    """编造数字没有被引号包裹、剥完干净自然——应该采用剥离结果，保留模型
    原本的措辞，不应该矫枉过正地整体退回安全标题。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱谈300%的杠杆神话与信号"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱谈的是杠杆使用的纪律问题，通篇没有出现任何具体数字")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)

    assert title == "帕伯莱谈的杠杆神话与信号", (
        f"标题「{title}」不是剥离后的干净结果，疑似被矫枉过正退回了安全标题")
    assert "300%" not in title
    assert "帕伯莱" in title


def test_final_title_always_contains_speaker_name_across_all_degrade_paths(monkeypatch):
    """无论走哪条降级路径（干净剥离/退安全标题/最终兜底），最终标题都必须
    含讲者名——这是硬性要求，不能被本次改动破坏。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "谈投资中的「40%」与亏损"   # 故意不带讲者名，且带引号包裹的编造数字

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("投资中的亏损，通篇没有出现任何具体数字")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)

    assert "帕伯莱" in title, f"最终标题「{title}」缺失讲者名"
    assert _no_empty_paired_symbols(title), f"标题「{title}」里含空壳或孤立单引号"


def test_safe_fallback_itself_never_leaks_empty_quote_shell(monkeypatch):
    """title_suggestion 本身就带编造数字且被引号包裹——安全标题路径也要过
    同一道合法性校验，不能因为走的是"安全标题"分支就绕开检查。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱谈投资中的『40%』与亏损"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱谈投资中的亏损，通篇没有出现任何具体数字")
    # title_suggestion 本身也带一个编造数字且被引号包裹，逼安全标题分支
    # 也要走一遍剥离 + 合法性校验。
    quotes[0]["title_suggestion"] = "投资中『40%』的亏损教训"
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)

    assert _no_empty_paired_symbols(title), (
        f"安全标题路径产出的「{title}」里含空壳或孤立单引号")
    assert "帕伯莱" in title
    assert not produce._title_has_digit_or_percent(title)


def test_final_deterministic_fallback_is_used_when_safe_title_itself_invalid():
    """连安全标题本身都不合法（这里直接构造一个带孤立引号的 base）时，必须
    落到最终确定性兜底短语，不能返回空字符串或继续往下传不合法标题。

    直接单测 make_title 内部同款校验逻辑对应的公开函数
    ``_title_residue_is_valid``，并断言 produce.py 暴露的最终兜底短语常量
    本身满足校验（这是"任何情况都合法"承诺的地基）。
    """
    assert not produce._title_residue_is_valid("帕伯莱谈投资中的「亏损", "帕伯莱"), (
        "孤立引号的残留没有被判定为不合法，合法性校验形同虚设")
    fallback = "帕伯莱" + produce._FINAL_SAFE_TITLE_PHRASE
    assert produce._title_residue_is_valid(fallback, "帕伯莱"), (
        f"最终确定性兜底短语拼出的标题「{fallback}」本身都无法通过合法性校验")
    assert fallback, "最终兜底不能是空字符串"


def test_title_residue_is_valid_rejects_empty_paired_symbols_directly():
    """_title_residue_is_valid 直接单测：各种空壳符号都要被拒绝。"""
    speaker = "帕伯莱"
    for empty_pair in ("「」", "『』", "（）", "()", "《》", "【】", '""', "''"):
        title = f"{speaker}谈投资中的{empty_pair}与亏损"
        assert not produce._title_residue_is_valid(title, speaker), (
            f"标题「{title}」含空壳「{empty_pair}」应被判定为不合法")


def test_title_residue_is_valid_rejects_unbalanced_brackets_directly():
    """_title_residue_is_valid 直接单测：孤立的单个引号/括号要被拒绝。"""
    speaker = "帕伯莱"
    for bad in ("帕伯莱谈投资中的「亏损", "帕伯莱谈投资中的」亏损",
                "帕伯莱谈投资（中的亏损", "帕伯莱谈投资中的亏损）"):
        assert not produce._title_residue_is_valid(bad, speaker), (
            f"标题「{bad}」含孤立引号/括号应被判定为不合法")


def test_title_residue_is_valid_rejects_dangling_and_repeated_punctuation():
    """_title_residue_is_valid 直接单测：结尾悬挂连接词/标点、标点连续
    重复，都要被拒绝。
    """
    speaker = "帕伯莱"
    for bad in ("帕伯莱谈投资中的与", "帕伯莱谈投资中的亏损：",
                "帕伯莱谈投资中的、", "帕伯莱谈投资，，中的亏损"):
        assert not produce._title_residue_is_valid(bad, speaker), (
            f"标题「{bad}」应因悬挂/连续标点被判定为不合法")


def test_title_residue_is_valid_accepts_clean_natural_title_directly():
    """_title_residue_is_valid 直接单测：干净自然的标题必须通过，不能
    矫枉过正地连正常标题也一起拒绝。
    """
    speaker = "帕伯莱"
    for good in ("帕伯莱谈投资中的杠杆神话与信号", "帕伯莱：仓位管理的纪律",
                 "帕伯莱谈投资中真实的『40%』案例"):
        assert produce._title_residue_is_valid(good, speaker), (
            f"标题「{good}」本身干净自然，不应被判定为不合法")


def test_title_residue_is_valid_rejects_too_short_residue_after_speaker_removed():
    """去掉讲者名和标点后剩余有效内容过短（几乎没剩信息量）要被拒绝。"""
    speaker = "帕伯莱"
    assert not produce._title_residue_is_valid("帕伯莱：", speaker)
    assert not produce._title_residue_is_valid("帕伯莱。", speaker)
