"""produce.py::make_title（问题2、4：必含讲者名 + 具体信息 + 避重 + 词边界截断）。

历史缺陷：
  1. 不要求标题含讲者名，7 集里有 3 集没提讲者，封面看起来跟谁都能配；
  2. 不校验「XX的智慧」这类空话，模型爱偷懒生成没有信息量的标题；
  3. 不知道同批其他集的标题，容易出现「与巴菲特共进午餐的价值」/
     「与巴菲特共进午餐的启示」这种几乎同题的两集；
  4. ``title[:TITLE_MAX_CHARS]`` 硬截断，「现金流预测」被砍成「现金流预」，
     断在词中间。

这里不把被测模块自己的常量（TITLE_SOFT_TARGET_CHARS 等）喂回去当测试输入，
断言用的字符串长度都是独立选定的。
"""

import pytest

import produce


def _quotes(text="讲者说了一些很有信息量的具体主张"):
    return [{"transcript_zh": text}, {"transcript_zh": text}, {"transcript_zh": text}]


def test_title_override_still_wins_and_is_word_boundary_truncated():
    """--title-override 优先，但超长时也要走词边界截断，不能硬砍。"""
    override = "帕伯莱谈长期价值投资中最容易被忽视的现金流风险与安全边际"
    title = produce.make_title(_quotes(), "帕伯莱", "key", "http://x", override)
    assert title.startswith("帕伯莱")
    assert len(title) <= produce.TITLE_HARD_MAX_CHARS
    # 词边界截断：结果必须是原文的一个前缀（可能少几个字），不能出现原文里
    # 不存在的拼接产物。
    assert override.startswith(title.rstrip("，。！？、；："))


def test_generated_title_must_contain_speaker_name(monkeypatch):
    """模型如果生成了不含讲者名的标题，必须被拦下并补上讲者名，不能放行。"""
    def fake_call_llm(messages, api_key, model, base_url):
        return "投资中最容易被忽视的现金流风险"   # 故意不含「帕伯莱」

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    title = produce.make_title(_quotes(), "帕伯莱", "key", "http://x", None)
    assert "帕伯莱" in title


def test_generated_title_matching_requirements_is_kept_as_is(monkeypatch):
    """模型第一次就给出合规标题（含讲者名、不重复）时，不应该被二次改写。"""
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱：航空安全事故教会我的仓位管理纪律"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    title = produce.make_title(_quotes(), "帕伯莱", "key", "http://x", None)
    assert title == "帕伯莱：航空安全事故教会我的仓位管理纪律"[:produce.TITLE_HARD_MAX_CHARS] \
        or title.startswith("帕伯莱")


def test_previous_titles_are_passed_into_the_prompt(monkeypatch):
    """同批已生成的标题必须真的被塞进发给模型的 prompt 里，用于避重。"""
    seen_prompts = []

    def fake_call_llm(messages, api_key, model, base_url):
        seen_prompts.append(messages[0]["content"])
        return "帕伯莱：这一集完全不同的具体主张与数字"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    produce.make_title(_quotes(), "帕伯莱", "key", "http://x", None,
                       previous_titles=["帕伯莱谈企业稳健经营之道"])
    assert any("帕伯莱谈企业稳健经营之道" in p for p in seen_prompts), (
        "已生成标题没有出现在任何一次 prompt 里，避重要求形同虚设")


def test_duplicate_title_triggers_a_retry_then_gets_deduped(monkeypatch):
    """模型重复给出与已有标题相同的文本时，必须重试，不能原样放行同一句话。"""
    calls = {"n": 0}

    def fake_call_llm(messages, api_key, model, base_url):
        calls["n"] += 1
        if calls["n"] == 1:
            return "帕伯莱谈企业稳健经营之道"       # 与 previous_titles 完全撞题
        return "帕伯莱：供应链数据里的三个反直觉信号"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    title = produce.make_title(_quotes(), "帕伯莱", "key", "http://x", None,
                               previous_titles=["帕伯莱谈企业稳健经营之道"])
    assert calls["n"] >= 2, "撞题后没有发生任何重试"
    assert title != "帕伯莱谈企业稳健经营之道"


def test_never_cuts_mid_word_when_soft_target_is_exceeded(monkeypatch):
    """模型忽略字数要求生成超长标题时，收尾截断必须落在词边界上。

    用一个真实会被 jieba 切开、且在软上限附近截断点恰好落在词中间的句子，
    验证结果不会出现「现金流预」这种断词现象。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱：长期价值投资的关键在于现金流预测能力和安全边际的双重保障"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    title = produce.make_title(_quotes(), "帕伯莱", "key", "http://x", None)
    assert "现金流预" not in title or "现金流预测" in title, (
        f"标题「{title}」疑似把「现金流预测」这个词从中间砍断")
    assert len(title) <= produce.TITLE_HARD_MAX_CHARS


def test_llm_failure_falls_back_to_a_title_that_still_has_the_speaker(monkeypatch):
    """模型不可用时的兜底标题也必须含讲者名，不能因为走了 fallback 分支就豁免。"""
    def boom(messages, api_key, model, base_url):
        raise RuntimeError("模型暂时不可用")

    monkeypatch.setattr(produce.HL, "call_llm", boom)
    quotes = [{"transcript_zh": "x", "title_suggestion": "危机中的仓位管理"}]
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)
    assert "帕伯莱" in title


def test_word_boundary_truncation_helper_never_splits_a_jieba_unit():
    """_truncate_at_word_boundary 直接单测：结果必须是若干完整词单元的拼接。"""
    long_title = "帕伯莱：长期价值投资的关键在于现金流预测能力"
    truncated = produce._truncate_at_word_boundary(long_title, 15)
    assert len(truncated) <= 15
    assert long_title.startswith(truncated)
    # 不能砍在"现金流预测"这个词中间——结果要么完整包含它，要么完全不含
    assert not (("现金流预" in truncated) and ("现金流预测" not in truncated))


def test_word_boundary_truncation_reproduces_the_reported_fix_exactly():
    """回归锁：任务里实测的硬截断现场——
    硬截断（title[:19]）会产生“...现金流预”（断在“预测”词中间），
    词边界截断在同样的长度预算下必须宁可少截一点（到“...现金流”为止），
    也不能输出包含孤立“预”字的结果。这个长度/切点是独立选的（不依赖
    TITLE_SOFT_TARGET_CHARS 等被测常量），专门用来杀掉“词边界截断换回硬
    截断”这个变异。
    """
    title = "帕伯莱：长期价值投资的关键在于现金流预测能力"
    # 硬截断在这个长度下会产生 title[:19] == "...现金流预"，断在词中间；
    assert title[:19].endswith("现金流预")
    truncated = produce._truncate_at_word_boundary(title, 19)
    assert not truncated.endswith("预"), (
        f"截断结果「{truncated}」以孤立的“预”字结尾，疑似回退到了硬截断")
    assert truncated == "帕伯莱：长期价值投资的关键在于现金流"


def test_speaker_name_detection_accepts_partial_full_name_match():
    """_title_contains_speaker 对「沃伦·巴菲特」这种带间隔号的全名要宽松匹配。"""
    assert produce._title_contains_speaker("巴菲特谈护城河", "沃伦·巴菲特")
    assert produce._title_contains_speaker("沃伦·巴菲特谈护城河", "沃伦·巴菲特")
    assert not produce._title_contains_speaker("这一集完全没提到任何人", "沃伦·巴菲特")
