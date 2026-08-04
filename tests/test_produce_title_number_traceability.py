"""produce.py::make_title 数字溯源校验（禁止编造数字）。

实测复现的问题：PR #35 的新 prompt 要求标题「必须包含具体信息（数字…）」，
原文没有数字时会逼模型自己编一个——真实跑帕伯莱 7 集里，ep03「韩国半导体
投资回报超300%」、ep04「IKEA财务奇迹背后的10%杠杆率」都是编的，原文根本
没有这两个数字。这里的测试覆盖：

  1. 原文完全没有数字，模型编了数字 → 最终标题不能出现任何阿拉伯数字/%；
  2. 原文确实有「40%」，模型标题也用了「40%」→ 必须原样保留，不能被
     矫枉过正删掉；
  3. 中文数字等价性：原文「百分之四十」、标题「40%」→ 判定为匹配、保留；
  4. 重试路径：第一次编数字、第二次干净 → 最终用第二次的结果；
  5. 降级路径：两次都编数字 → 最终标题不含编造数字，且仍含讲者名。

不自证：断言用到的长度/字符串都是独立选定的，不会拿 produce.py 里的
TITLE_* 常量当测试输入去反推期望值。
"""
import produce


def _quotes(text):
    return [{"transcript_zh": text}, {"transcript_zh": text}, {"transcript_zh": text}]


def test_fabricated_number_with_no_source_numbers_is_stripped(monkeypatch):
    """原文完全不含数字，模型编了一个「300%」——最终标题不能带任何数字/%。"""
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱：韩国半导体投资回报超300%"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱：韩国半导体的黄金机会，强调韩国在半导体行业的优势")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)
    assert not produce._title_has_digit_or_percent(title), (
        f"标题「{title}」仍带有阿拉伯数字或百分号，疑似编造数字未被拦下")
    assert "帕伯莱" in title


def test_real_number_from_source_is_preserved_not_over_deleted(monkeypatch):
    """原文确实有「40%」，模型标题里也是「40%」——必须原样保留，不能被误删。

    这条测试专门用来杀掉「数字溯源校验被整体跳过或改成一律删除数字」类
    变异：如果校验逻辑矫枉过正，把所有数字（包括真实的）都删掉，这里就
    会失败。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱：40%的土耳其投资背后的逆向逻辑"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱：40%的土耳其投资，飞机失事联想到的仓位管理")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)
    assert "40%" in title, f"标题「{title}」把原文真实存在的「40%」也删掉了"


def test_chinese_numeral_equivalence_percent_form_is_recognized(monkeypatch):
    """原文写「百分之四十」，标题写「40%」——中文数字规范化后应判定为匹配。

    这条测试专门用来杀掉「中文数字规范化被去掉」类变异：一旦规范化逻辑被
    删掉或破坏，「百分之四十」和「40%」不会被识别为同一个数字，「40%」就
    会被误判为编造并删除，本测试会失败。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱：仓位40%的秘密"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱说，他会把仓位控制在百分之四十左右，这是他的纪律")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)
    assert "40%" in title, (
        f"标题「{title}」把「40%」删掉了，说明没能把它和原文「百分之四十」匹配上")


def test_chinese_numeral_equivalence_multiplier_form_is_recognized(monkeypatch):
    """原文写「三倍」，标题写「3倍」——倍数形式的中文数字也要能匹配。"""
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱：3倍回报的真实案例"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱回忆那笔投资最终拿到了三倍回报，堪称经典案例")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)
    assert "3倍" in title, (
        f"标题「{title}」把「3倍」删掉了，说明没能把它和原文「三倍」匹配上")


def test_retry_path_uses_the_second_clean_result(monkeypatch):
    """第一次生成编了数字，第二次干净——最终必须用第二次的结果，且确实重试过。"""
    calls = {"n": 0}

    def fake_call_llm(messages, api_key, model, base_url):
        calls["n"] += 1
        if calls["n"] == 1:
            return "帕伯莱：IKEA财务奇迹背后的10%杠杆率"
        return "帕伯莱：IKEA财务奇迹背后被忽视的会计逻辑"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱：IKEA的财务奇迹，颠覆认知的案例")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)
    assert calls["n"] >= 2, "编造数字后没有发生任何重试"
    assert title == "帕伯莱：IKEA财务奇迹背后被忽视的会计逻辑", (
        f"最终标题「{title}」不是第二次（干净）那次生成的结果")
    assert "10%" not in title


def test_degrades_deterministically_when_retry_still_fabricates(monkeypatch):
    """重试后仍然编数字——必须确定性降级：不含编造数字，且仍含讲者名。

    不能因为这个问题让整批跑挂掉：函数必须正常返回一个字符串，不能抛出
    异常或触发 exit。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱：IKEA财务奇迹背后的10%杠杆率"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱：IKEA的财务奇迹，颠覆认知的案例")
    quotes[0]["title_suggestion"] = "IKEA的财务奇迹"
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)
    assert isinstance(title, str) and title, "降级后必须正常返回非空标题字符串"
    assert "帕伯莱" in title, f"降级后的标题「{title}」丢失了讲者名"
    assert not produce._title_has_digit_or_percent(title), (
        f"降级后的标题「{title}」仍带有编造数字/百分号")
    assert "10%" not in title


def test_number_that_appears_only_via_unit_suffix_in_source_is_kept(monkeypatch):
    """原文带单位的数字（「23美元」）要能被识别为真实数字并保留，不是编造。

    对应任务里 ep04「曼哈顿23美元的真相」这条真实存在的数字——回归防止
    「数字+单位」这种写法被误判为编造。
    """
    def fake_call_llm(messages, api_key, model, base_url):
        return "帕伯莱：曼哈顿23美元的真相"

    monkeypatch.setattr(produce.HL, "call_llm", fake_call_llm)
    quotes = _quotes("帕伯莱讲述曼哈顿岛当年只卖23美元的历史案例，反常识")
    title = produce.make_title(quotes, "帕伯莱", "key", "http://x", None)
    assert "23美元" in title, f"标题「{title}」把原文真实存在的「23美元」也删掉了"


def test_extract_number_tokens_direct_unit_test():
    """_extract_number_tokens 直接单测：百分数/倍数/纯数字要落进不同的
    canonical 前缀，避免「40%」被错误判定等于「40倍」或裸数字「40」。
    """
    assert produce._extract_number_tokens("投资回报300%") == {"PCT:300.0"}
    assert produce._extract_number_tokens("拿到3倍回报") == {"X:3.0"}
    assert produce._extract_number_tokens("百分之四十的仓位") == {"PCT:40.0"}
    assert produce._extract_number_tokens("完全没有数字的句子") == set()


def test_fabricated_number_tokens_direct_unit_test():
    """_fabricated_number_tokens 直接单测：标题数字不在原文里就该被判定为编造，
    在原文里（含中文数字等价形式）就不该被判定为编造。
    """
    assert produce._fabricated_number_tokens(
        "帕伯莱：回报超300%", "帕伯莱讲的是韩国半导体的机会，没有提到具体数字"
    ) == {"PCT:300.0"}
    assert produce._fabricated_number_tokens(
        "帕伯莱：40%的仓位", "帕伯莱说仓位控制在百分之四十左右"
    ) == set()
