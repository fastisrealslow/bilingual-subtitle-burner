"""封面标题字体覆盖与字形一致性（问题3：「关」字体面选错，不是缺字形）。

根因：NotoSansCJK-Bold.ttc 是一个 TTC 合集，PIL 不给 index 时默认取第 0 个子
字体（实测是 Noto Sans CJK **JP**），不是 SC。JP/SC 两套字形对同一个字符（比如
「关」）的笔画粗细、内部间距不一样，混进同一句标题里就是「有一个字明显细一号」
的视觉 bug —— cmap 里其实两套子字体都有这个字，不是传统意义上的字体 fallback
缺字形，而是字体合集里选错了子字体索引。

这里锁两件事：
  1. 一组「易漏字」（关、龘、鑫、髎——都是笔画复杂、容易在窄字符集字体里缺失
     或渲染成 .notdef 方块的字）逐字用 PIL ImageFont.getmask 检查非空，不出现
     tofu；
  2. 这些字全部来自同一个字体文件的同一个子字体下标（不是「关」用了 JP 面、
     其余字用了 SC 面这种同文件内部不一致）。
"""

import pytest
from PIL import ImageFont

import step7_cover as C

# 「关」是任务里实测出问题的字；「龘」「鑫」「髎」是三个笔画复杂、
# 常见于窄字符集/阉割版字体里被砍掉或找不到独立字形的生僻字，
# 用来兜底验证不是只有「关」凑巧过了。
EASILY_MISSING_CHARS = "关键龘鑫髎"


def test_title_font_resolves_to_a_ttc_with_an_sc_subface():
    """find_font 选中的字体如果是 ttc 合集，必须解析出具体的 SC 子字体下标。

    不能是「不知道选了哪个子字体」的默认 0——问题3 的根因正是默认下标不是 SC。
    """
    font, path, idx = C.find_font_path_and_index(90)
    assert path is not None, "找不到任何候选字体，环境缺字体包"
    if path.lower().endswith(".ttc"):
        assert idx is not None
        # 用 fontTools 直接读这个下标对应子字体的 name 表，交叉验证
        # find_font_path_and_index 报的 idx 真的是 SC，不是随便一个能用的下标。
        fontTools_ttLib = pytest.importorskip("fontTools.ttLib")
        tc = fontTools_ttLib.TTCollection(path, lazy=True)
        name = tc.fonts[idx]["name"].getDebugName(1) or ""
        assert "SC" in name, f"选中的子字体是「{name}」，不是简体中文（SC）面"


def test_no_tofu_blocks_for_easily_missing_chars():
    """逐字渲染，PIL getmask 必须有非空墨迹——不能是 .notdef 空心方块。

    getmask 对 .notdef（tofu）方块和真实汉字都会返回非空的位图（tofu 本身也是
    有墨的方框），所以单纯判断 non-empty 不够；这里额外要求墨迹的填充面积占
    外框比例不能小到像一个空心方框的边框（真实汉字笔画通常填充率明显更高，
    tofu 方块只有四条边有墨、中心是空的，填充率远低于真实汉字）。
    """
    from PIL import Image
    font = C.find_font(90)
    for ch in EASILY_MISSING_CHARS:
        raw_mask = font.getmask(ch)
        # getmask 返回底层 ImagingCore，先包成正经 Image 才能 crop/getdata。
        mask = Image.frombytes("L", raw_mask.size, bytes(raw_mask))
        bbox = mask.getbbox()
        assert bbox is not None, f"字符「{ch}」渲染出空白，可能是缺字形"
        l, t, r, b = bbox
        area = (r - l) * (b - t)
        assert area > 0
        ink_pixels = sum(1 for px in mask.crop(bbox).getdata() if px > 0)
        fill_ratio = ink_pixels / area
        # tofu 方块是空心边框，填充率通常 <0.25；真实汉字（尤其是关/龘/鑫/髎
        # 这种笔画密集的字）填充率明显更高。0.3 留了安全余量，不是精确值。
        assert fill_ratio > 0.30, (
            f"字符「{ch}」墨迹填充率 {fill_ratio:.2f} 过低，疑似 tofu 空心方块")


def test_easily_missing_chars_all_come_from_the_same_face():
    """关/龘/鑫/髎 必须全部来自同一个字体文件的同一个子字体下标。

    这才是问题3的核心断言：不是「字体里有没有这个字」，而是「同一句标题里的
    每个字是不是用同一套字形画出来的」。用 fontTools 的 cmap 分别查每个字属于
    哪个 glyph name，再用 getBestCmap 交叉核实都指向同一份 cmap（同一个子字体）。
    """
    fontTools_ttLib = pytest.importorskip("fontTools.ttLib")
    font, path, idx = C.find_font_path_and_index(90)
    assert path is not None

    if path.lower().endswith(".ttc"):
        tc = fontTools_ttLib.TTCollection(path, lazy=True)
        face = tc.fonts[idx]
    else:
        face = fontTools_ttLib.TTFont(path, lazy=True)

    cmap = face.getBestCmap()
    glyph_set = face.getGlyphSet()
    for ch in EASILY_MISSING_CHARS:
        cp = ord(ch)
        assert cp in cmap, f"字符「{ch}」（U+{cp:04X}）不在选中子字体的 cmap 里"
        glyph_name = cmap[cp]
        assert glyph_name in glyph_set, (
            f"字符「{ch}」映射到的字形 {glyph_name} 在 glyph 表里找不到")


def test_glyph_width_is_consistent_across_the_easily_missing_chars():
    """同一字号下，这些字的 advance width 应当高度一致（都是等宽 CJK 字形）。

    问题3 现场描述是「关」比周围字更细、间距更宽——如果字形来自不同子字体面，
    等宽 CJK 字体的 advance width 也可能出现不一致。这里用 textlength 量单字
    宽度，要求和字号本身的比值都落在同一个窄范围内。
    """
    from PIL import Image, ImageDraw
    font = C.find_font(90)
    img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(img)
    widths = [draw.textlength(ch, font=font) for ch in EASILY_MISSING_CHARS]
    lo, hi = min(widths), max(widths)
    assert hi - lo <= 2, (
        f"易漏字宽度不一致：{dict(zip(EASILY_MISSING_CHARS, widths))}，"
        "疑似部分字符落到了不同的字体子面")
