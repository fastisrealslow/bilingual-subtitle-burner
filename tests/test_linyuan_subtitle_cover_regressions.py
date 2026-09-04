"""2026-09-04 真实 14 片段回归：ASR 音近错与封面标题截断。"""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "linyuan_produce_subtitle_cover", ROOT / "linyuan/produce_cn.py")
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


def test_finance_asr_regressions_are_corrected_as_phrases():
    raw = "林元说高抛低息，不要选骨，夕洋行业很难划解这种风险，达人堂也不错"
    assert P.fix_terms(raw) == "林园说高抛低吸，不要选股，夕阳行业很难化解这种风险，达仁堂也不错"


def test_36_character_cover_title_is_never_silently_truncated():
    title = "林园最新访谈：投资拼的从来不是短期热度而是看透本质后的长期坚守"
    lines = P.wrap_cover_title(title, chars_per_line=15, max_lines=3)
    assert "".join(lines) == title
    assert len(lines) == 3
    assert all(len(line) <= 15 for line in lines)


def test_cover_layout_rejects_instead_of_dropping_unrendered_text():
    try:
        P.wrap_cover_title("长" * 46, chars_per_line=15, max_lines=3)
    except P.VisualQualityError as exc:
        assert "无法在 3 行内完整排版" in str(exc)
    else:
        raise AssertionError("超出封面容量的标题不应被静默截断")
