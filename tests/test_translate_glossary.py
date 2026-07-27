"""投资领域术语表（glossary.json → 翻译提示词 → LLM 缓存键）。

背景：同一条成片 t≈173.09s，源片硬字幕是
``NOW, THE OTHER HALF OF THAT QUESTION I LEAVE FOR MR. BUFFETT``，
DeepSeek-V3 译成了「这问题的另一半我留给巴特勒先生回答」—— Buffett 被音译
成了「巴特勒」。频道做的是价值投资内容，人名译错是硬伤。

这里锁三件事：表里有该有的词、词确实进了提示词、提示词变了缓存键跟着变。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import sf_client                              # noqa: E402
import translate as TR                        # noqa: E402

URL = "https://api.siliconflow.cn/v1/chat/completions"

# 需求里点名必须覆盖的词，逐条锁死，避免后续整理术语表时被顺手删掉
REQUIRED_TERMS = {
    "Buffett": "巴菲特",
    "Munger": "芒格",
    "Berkshire": "伯克希尔",
    "Berkshire Hathaway": "伯克希尔·哈撒韦",
    "Graham": "格雷厄姆",
    "Fisher": "费雪",
    "Dalio": "达利欧",
    "Bridgewater": "桥水",
    "Greenblatt": "格林布拉特",
    "Pabrai": "帕伯莱",
    "Lynch": "彼得·林奇",
    "S&P 500": "标普500",
    "opportunity cost": "机会成本",
    "cost of capital": "资本成本",
    "margin of safety": "安全边际",
    "moat": "护城河",
    "float": "浮存金",
    "intrinsic value": "内在价值",
    "compounding": "复利",
    "circle of competence": "能力圈",
}


@pytest.fixture(autouse=True)
def fresh_glossary_cache():
    TR._glossary_cache = None
    yield
    TR._glossary_cache = None


# ── 术语表本身 ──────────────────────────────────────────────────────────────

def test_glossary_file_ships_with_the_repo():
    assert TR.GLOSSARY_PATH.exists(), "glossary.json 必须随仓库发布"
    json.loads(TR.GLOSSARY_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("en,zh", sorted(REQUIRED_TERMS.items()))
def test_required_term_is_present(en, zh):
    assert TR.load_glossary().get(en) == zh


def test_load_glossary_flattens_groups(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps({"terms": {"people": {"Buffett": "巴菲特"},
                                       "concepts": {"moat": "护城河"}}}),
                 encoding="utf-8")
    assert TR.load_glossary(p) == {"Buffett": "巴菲特", "moat": "护城河"}


def test_unreadable_glossary_degrades_instead_of_crashing(tmp_path, capsys):
    # 读不到表只是可能译错人名，不该把整条流水线停掉；但必须留痕
    assert TR.load_glossary(tmp_path / "missing.json") == {}
    assert "术语表" in capsys.readouterr().err


def test_longer_terms_come_first_so_they_are_not_shadowed():
    block = TR.glossary_block("en2zh")
    assert block.index("Berkshire Hathaway") < block.index("\n- Berkshire →")


def test_empty_glossary_injects_nothing(monkeypatch):
    assert TR.glossary_block("en2zh", {}) == ""
    monkeypatch.setattr(TR, "load_glossary", lambda path=None: {})
    assert TR.system_prompt("en2zh") == TR.SYSTEM_EN2ZH


# ── 注入提示词 ──────────────────────────────────────────────────────────────

def test_glossary_is_injected_into_the_en2zh_system_prompt():
    prompt = TR.system_prompt("en2zh")
    assert TR.SYSTEM_EN2ZH in prompt
    assert "Buffett → 巴菲特" in prompt
    assert "margin of safety → 安全边际" in prompt


def test_glossary_is_injected_into_the_zh2en_system_prompt():
    prompt = TR.system_prompt("zh2en")
    assert TR.SYSTEM_ZH2EN in prompt
    assert "巴菲特 → Buffett" in prompt


def test_translate_batch_actually_sends_the_glossary(monkeypatch):
    """真正发出去的 system 消息里必须带术语表，不能只是函数能生成。"""
    sent = {}

    def fake_chat(messages, *a, **k):
        sent["messages"] = messages
        return "1. 这问题的另一半我留给巴菲特先生回答"

    monkeypatch.setattr(TR, "chat", fake_chat)
    out = TR.translate_batch(
        ["NOW, THE OTHER HALF OF THAT QUESTION I LEAVE FOR MR. BUFFETT"],
        "sk", "deepseek-ai/DeepSeek-V3", "https://x/v1", direction="en2zh")

    system = sent["messages"][0]["content"]
    assert sent["messages"][0]["role"] == "system"
    assert "Buffett → 巴菲特" in system
    assert out == ["这问题的另一半我留给巴菲特先生回答"]


def test_single_line_retry_prompt_also_carries_the_glossary(monkeypatch):
    """整批失败后逐条重试那条兜底路径同样要带术语表。"""
    seen = []

    def fake_chat(messages, *a, **k):
        seen.append(messages[0]["content"])
        return "" if len(seen) == 1 else "留给巴菲特先生"

    monkeypatch.setattr(TR, "chat", fake_chat)
    TR.translate_all(["I LEAVE FOR MR. BUFFETT"], "sk", "m", "https://x/v1",
                     batch_size=1, direction="en2zh")
    assert all("Buffett → 巴菲特" in s for s in seen)


# ── 缓存键跟着术语表变 ──────────────────────────────────────────────────────
# 提示词变了而缓存键不变，就会命中改词之前那批错译文 —— 改表等于没改。

def body_with(system: str) -> dict:
    return {"model": "deepseek-ai/DeepSeek-V3",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": "1. MR. BUFFETT"}],
            "temperature": 0.3}


def test_cache_key_changes_when_the_glossary_changes():
    before = sf_client.cache_key(URL, body_with(TR.SYSTEM_EN2ZH))
    after = sf_client.cache_key(URL, body_with(TR.system_prompt("en2zh")))
    assert before != after, "注入术语表后缓存键必须变，否则会命中旧的错译文"


def test_cache_key_changes_when_a_single_term_is_edited():
    base = TR.glossary_block("en2zh", {"Buffett": "巴菲特"})
    edited = TR.glossary_block("en2zh", {"Buffett": "巴菲特", "moat": "护城河"})
    assert (sf_client.cache_key(URL, body_with(base))
            != sf_client.cache_key(URL, body_with(edited)))


def test_cache_key_is_stable_for_an_unchanged_glossary():
    p = TR.system_prompt("en2zh")
    assert sf_client.cache_key(URL, body_with(p)) == \
        sf_client.cache_key(URL, body_with(p))


def test_cache_key_covers_the_whole_prompt_not_just_the_model():
    a = body_with("system A")
    b = body_with("system B")
    assert a["model"] == b["model"]
    assert sf_client.cache_key(URL, a) != sf_client.cache_key(URL, b)
