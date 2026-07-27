"""字幕摆位（scripts/clip.make_ass）：英文必须始终在中文之上。"""

import clip as C

W, H = 854, 344

SHORT_ZH = "耐心不是美德。"
LONG_ZH = "认为华尔街贯用的十年美国国债以及两年美国国债的值利率力差不是最好的recession指标。"
EN = "If I cannot understand it, I will not touch it at any price."


def build(zh, tmp_path, **kw):
    entries = [{"start_sec": 0.0, "end_sec": 3.0, "zh": zh, "en": EN}]
    p = tmp_path / "x.ass"
    C.make_ass(entries, str(p), W, H, **kw)
    return p.read_text(encoding="utf-8-sig")


def styles(ass):
    out = {}
    for ln in ass.splitlines():
        if ln.startswith("Style: "):
            f = ln[len("Style: "):].split(",")
            out[f[0]] = {"size": int(f[2]), "margin_v": int(f[21])}
    return out


def dialogues(ass):
    out = {}
    for ln in ass.splitlines():
        if ln.startswith("Dialogue: "):
            f = ln[len("Dialogue: "):].split(",", 9)
            out[f[3]] = {"margin_v": int(f[7]), "text": f[9]}
    return out


def test_three_line_chinese_does_not_push_over_the_english(tmp_path):
    """样式里的英文 MarginV 只能按固定行数算，中文折到 3 行就顶穿它——
    实测把英文盖住，成片上变成中文在上、英文在下。"""
    ass = build(LONG_ZH, tmp_path)
    zh_style, en = styles(ass)["ZH"], dialogues(ass)["EN"]
    assert dialogues(ass)["ZH"]["text"].count(r"\N") == 2      # 确实是 3 行
    # 英文行底边要让出 3 行中文的高度
    assert en["margin_v"] >= zh_style["margin_v"] + 3 * zh_style["size"]


def test_single_line_chinese_does_not_leave_a_blank_row(tmp_path):
    """样式按固定 2 行预留，中文只有 1 行时英文被顶到画面中段骑在讲者脸上。
    逐条 MarginV 必须按实际 1 行算，比样式值低一个行高。"""
    ass = build(SHORT_ZH, tmp_path)
    zh_style, en_style = styles(ass)["ZH"], styles(ass)["EN"]
    en = dialogues(ass)["EN"]
    assert dialogues(ass)["ZH"]["text"].count(r"\N") == 0       # 确实是 1 行
    assert en["margin_v"] < en_style["margin_v"]
    assert en["margin_v"] >= zh_style["margin_v"] + zh_style["size"]


def test_english_margin_never_exceeds_the_half_screen_cap(tmp_path):
    ass = build(LONG_ZH * 2, tmp_path)
    assert dialogues(ass)["EN"]["margin_v"] <= int(H * 0.55)


def test_zh_only_mode_emits_no_english_line(tmp_path):
    ass = build(LONG_ZH, tmp_path, sub_mode="zh_only")
    assert "EN" not in dialogues(ass)


def test_chinese_style_is_noto_cjk(tmp_path):
    ass = build(SHORT_ZH, tmp_path)
    assert "Style: ZH,Noto Sans CJK SC," in ass
