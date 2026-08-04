"""--title-override 的字数上限校验（问题2）。

历史缺陷：``TITLE_MAX_CHARS`` 只是个孤零零的死常量（只有定义和一行注释，
全仓非测试代码里没有任何引用），``--title-override`` 实际不设上限。填一个
超长覆盖标题不会在参数阶段被拦下，会一路跑到封面渲染阶段才撞
``COVER.TitleOverflowError`` 退 2，此时下载、转写、LLM 开销已经全部付出。

现在的行为：在 ``produce.main`` 里、``parse_args`` 之后、任何外部调用之前，
对 ``--title-override`` 的字符数做校验，超过 ``TITLE_OVERRIDE_MAX_CHARS``
（20）就 fail fast 退 ``EXIT_CONFIG``（1），不是 ``EXIT_QUALITY``（2）——
用户填错参数属于配置错，不是内容质量拒绝。

本文件的用例全部使用字面量长度写的测试字符串（不把 TITLE_OVERRIDE_MAX_CHARS
等被测常量喂回去当输入），避免自证式测试。
"""

import json

import pytest

import produce


# 21 个汉字，比上限 20 多 1 个字，故意选在边界正上方
OVERLONG_21 = "帕伯莱谈长期价值投资中最容易忽视的风险与机会一二三"[:21]
# 恰好 20 个汉字：正好等于上限，必须通过
EXACTLY_20 = "帕伯莱谈长期价值投资中最容易忽视的风险机"[:20]
# 明显小于上限（9 个字）
SHORT_9 = "帕伯莱谈仓位风险管理"[:9]


def _assert_lengths():
    """先自检这几个字面量常量的真实长度，测试意图必须和字符串本身一致。"""
    assert len(OVERLONG_21) == 21
    assert len(EXACTLY_20) == 20
    assert len(SHORT_9) == 9


def test_fixture_strings_have_expected_literal_lengths():
    _assert_lengths()


def test_overlong_title_override_exits_config_not_quality(monkeypatch, capsys):
    """超过上限的 --title-override 必须在参数阶段退 1，不是退 2。"""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "v.mp4", "--slug", "s",
                      "--title-override", OVERLONG_21])

    assert e.value.code == produce.EXIT_CONFIG
    assert e.value.code != produce.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["stage"] == "config"
    assert payload["reason"] == "title_override_too_long"
    # 报错信息必须写清实际字数、上限、以及是哪个参数
    assert "--title-override" in payload["detail"]
    assert "21" in payload["detail"]
    assert str(produce.TITLE_OVERRIDE_MAX_CHARS) in payload["detail"]
    assert payload["length"] == 21
    assert payload["max_chars"] == produce.TITLE_OVERRIDE_MAX_CHARS
    assert payload["arg"] == "--title-override"


def test_overlong_title_override_does_not_touch_network_or_disk(monkeypatch):
    """fail fast 意味着校验必须在任何外部调用之前，不能先付下载/转写的开销。"""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")
    monkeypatch.setattr(
        produce, "resolve_source",
        lambda *a, **k: pytest.fail("闸门没拦住：不该走到取源，说明校验发生得太晚"))

    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "v.mp4", "--slug", "s",
                      "--title-override", OVERLONG_21])
    assert e.value.code == produce.EXIT_CONFIG


def test_title_override_exactly_at_limit_passes_the_gate(monkeypatch,
                                                          capsys):
    """恰好等于上限的 --title-override 必须通过校验，不能被误杀。

    走到 resolve_source 会因为 v.mp4 不存在而以别的方式退出（config/input
    错误），这里只关心「没有在长度校验这一步被拦」——用 monkeypatch 让
    resolve_source 直接抛一个能识别的哨兵异常，校验通过后必然会走到这里。
    """
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")

    class ReachedResolveSource(Exception):
        pass

    def fake_resolve_source(*a, **k):
        raise ReachedResolveSource()

    monkeypatch.setattr(produce, "resolve_source", fake_resolve_source)

    with pytest.raises(ReachedResolveSource):
        produce.main(["--source", "v.mp4", "--slug", "s",
                      "--title-override", EXACTLY_20])
    capsys.readouterr()


def test_title_override_below_limit_passes_the_gate(monkeypatch, capsys):
    """明显小于上限的 --title-override 同样必须通过校验。"""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")

    class ReachedResolveSource(Exception):
        pass

    def fake_resolve_source(*a, **k):
        raise ReachedResolveSource()

    monkeypatch.setattr(produce, "resolve_source", fake_resolve_source)

    with pytest.raises(ReachedResolveSource):
        produce.main(["--source", "v.mp4", "--slug", "s",
                      "--title-override", SHORT_9])
    capsys.readouterr()


def test_empty_title_override_is_not_treated_as_too_long(monkeypatch,
                                                          capsys):
    """不给 --title-override（默认空串）不该触发长度校验。"""
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test")

    class ReachedResolveSource(Exception):
        pass

    def fake_resolve_source(*a, **k):
        raise ReachedResolveSource()

    monkeypatch.setattr(produce, "resolve_source", fake_resolve_source)

    with pytest.raises(ReachedResolveSource):
        produce.main(["--source", "v.mp4", "--slug", "s"])
    capsys.readouterr()


def test_title_override_max_chars_constant_is_twenty():
    """用户的决定是放宽到 20；同时旧名字必须指向新常量，不留两个死常量。"""
    assert produce.TITLE_OVERRIDE_MAX_CHARS == 20
    assert produce.TITLE_MAX_CHARS == produce.TITLE_OVERRIDE_MAX_CHARS
