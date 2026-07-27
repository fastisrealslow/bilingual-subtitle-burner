"""manifest 文案的简繁 + 标点规范化（scripts/copywrite.py）。

manifest.json 的 title 被 step6（切片文件名）和 step7（封面图）直接读走，
两处都绕过 step8 的 clean_title，所以规范化必须发生在 copywrite 阶段。
"""

import copywrite as C


def test_title_punctuation_is_normalized():
    out = C._normalize_copy({"title": '巴菲特:“复利”到底是什么?我认为只有三点!!',
                             "desc": "", "tags": []})
    assert out["title"] == "巴菲特：「复利」到底是什么？我认为只有三点！"


def test_desc_normalized_and_traditional_folded():
    out = C._normalize_copy({"title": "", "desc": '他說:“價值投資”很難?其實不難。',
                             "tags": []})
    assert "「价值投资」" in out["desc"]
    assert "：" in out["desc"] and "？" in out["desc"]


def test_speaker_name_protected_from_simplification():
    out = C._normalize_copy({"title": "股乾爹:护城河很重要!", "desc": "", "tags": ["股乾爹"]},
                            ["股乾爹"])
    assert out["title"].startswith("股乾爹：")
    assert out["tags"] == ["股乾爹"]


def test_normalization_is_idempotent():
    once = C._normalize_copy({"title": '李录:“护城河”是什么?', "desc": "", "tags": []})
    twice = C._normalize_copy(dict(once))
    assert once["title"] == twice["title"]


def test_url_in_desc_survives():
    out = C._normalize_copy(
        {"title": "", "desc": "完整视频见 https://youtu.be/abc?t=120,建议一起看", "tags": []})
    assert "https://youtu.be/abc?t=120" in out["desc"]
    assert "120，建议" in out["desc"]
