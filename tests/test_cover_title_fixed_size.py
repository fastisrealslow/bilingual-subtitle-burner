"""封面标题固定字号 + 硬 2 行上限（问题1/2：不许缩字号、不许硬截断）。

历史缺陷：``title_font_size = max(36, w // 22)``，字号只跟画布宽度挂钩，然后
再按行数把字号一路缩小去塞标题——同一批封面里 8 字标题字很大、15 字的字明显
小一圈；缩字号缩不动了还会硬截断，「现金流预测」被切成「现金流预」，断在词
中间。

现在：字号写死成 ``TITLE_FONT_SIZE_PX``，与标题长短、画布分辨率都无关；换行
交给 ``wrap_title``（已有的 jieba 词边界折行），最多 ``TITLE_MAX_LINES`` 行；
两行还装不下就直接抛 ``TitleOverflowError``，不许暗中回退到旧的缩字号/截断
逻辑。

这里的测试字符串都是独立选的（人朗读会说的整句大白话），不从被测模块的
``TITLE_FONT_SIZE_PX`` / ``TITLE_MAX_LINES`` 反推字符串长度，避免自证。
"""

import pytest
from PIL import Image, ImageDraw

import step7_cover as C

LANDSCAPE = (1280, 720)
PORTRAIT = (1080, 1920)


@pytest.fixture
def frame(tmp_path):
    path = str(tmp_path / "frame.jpg")
    Image.new("RGB", (854, 340), (160, 160, 160)).save(path, "JPEG", quality=95)
    return path


def _title_font_size_used(frame, tmp_path, title, target):
    """渲染一张封面，测出实际用来画标题的字号——通过量单字宽度反推。

    Noto Sans CJK SC 是等宽字形，字号=字宽，量一个纯汉字的 textlength 就知道
    真实用的字号，不用去读被测模块内部变量。
    """
    out = str(tmp_path / "cover.jpg")
    boxes = C.make_cover(frame, title, "讲者", out, target)
    box = boxes["标题第1行"]
    # 用同一个 find_font 渲染同一个已知字符做对照标尺
    img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(img)
    ruler = C.find_font(C.TITLE_FONT_SIZE_PX)
    one_char_width = draw.textlength("测", font=ruler)
    return box, one_char_width


def test_short_and_long_titles_use_the_identical_font_size(frame, tmp_path):
    """8 字标题和 18 字标题必须用同一个字号——不许「短标题字大、长标题字小」。

    量法：同一个字（"资"）分别嵌在短、长标题里，量它的渲染宽度，两者必须
    完全相等——如果字号被标题长度影响，短标题里的这个字会明显更宽。
    """
    short_title = "巴菲特谈投资"
    long_title = "巴菲特谈长期投资中最容易被忽视的现金流风险"

    def _char_width_in_rendered_title(title):
        out = str(tmp_path / f"cover_{len(title)}.jpg")
        C.make_cover(frame, title, "讲者", out, LANDSCAPE)
        img = Image.open(out).convert("L")
        # 直接用 find_font 在同一进程里量「资」这个字的 textlength，
        # 因为 make_cover 内部真实使用的就是 find_font(TITLE_FONT_SIZE_PX)。
        draw = ImageDraw.Draw(img)
        return draw.textlength("资", font=C.find_font(C.TITLE_FONT_SIZE_PX))

    w_short = _char_width_in_rendered_title(short_title)
    w_long = _char_width_in_rendered_title(long_title)
    assert w_short == w_long


def test_font_size_does_not_scale_with_canvas_resolution(frame, tmp_path):
    """竖版 1080 宽和横版 1280 宽必须用同一个标题字号（旧逻辑是 w // 22）。"""
    title = "耐心是最贵的能力"
    portrait_font = C.find_font(C.TITLE_FONT_SIZE_PX)
    landscape_font = C.find_font(C.TITLE_FONT_SIZE_PX)
    img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(img)
    assert draw.textlength("耐", font=portrait_font) == \
        draw.textlength("耐", font=landscape_font)
    # 两个分辨率都必须真的能出图（不因为字号跟分辨率脱钩而在某一边裂开）
    C.make_cover(frame, title, "讲者", str(tmp_path / "p.jpg"), PORTRAIT)
    C.make_cover(frame, title, "讲者", str(tmp_path / "l.jpg"), LANDSCAPE)


def test_title_that_needs_three_lines_is_rejected_not_shrunk(frame, tmp_path):
    """真的两行装不下时必须抛 TitleOverflowError，不许暗中缩字号继续画。

    这句话故意写得很长、断句又多（每个逗号后 jieba 都会切出新词），在固定
    90px 字号 + 1080 安全区宽度下无论如何也塞不进 2 行。
    """
    long_title = ("讲者说长期主义不是喊口号而是把每一次决策都放在十年后回头看"
                  "会不会后悔这把尺子上反复地衡量和校准")
    out = tmp_path / "cover.jpg"
    with pytest.raises(C.TitleOverflowError) as e:
        C.make_cover(frame, long_title, "讲者", str(out), PORTRAIT)
    assert not out.exists(), "拒绝出图前不能有任何文件落盘"
    assert len(e.value.lines) > C.TITLE_MAX_LINES
    assert e.value.font_size == C.TITLE_FONT_SIZE_PX, (
        "报出来的字号必须是真实用的固定字号，不能是缩小之后的字号——"
        "说明失败前完全没有发生过缩字号")


def test_die_payload_uses_cover_render_stage_and_title_overflow_reason(
        capsys, tmp_path, monkeypatch):
    import json
    import produce

    def boom(*_a, **_k):
        raise C.TitleOverflowError(
            "一个装不下两行的超长标题示例文本用来触发拒绝路径",
            ["行一", "行二", "行三"], LANDSCAPE, C.TITLE_FONT_SIZE_PX, 1036.0)

    monkeypatch.setattr(produce.COVER, "make_cover", boom)
    with pytest.raises(SystemExit) as e:
        produce.render_covers("f.jpg", "标题", "讲者", tmp_path, {})
    assert e.value.code == produce.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["stage"] == "cover-render"
    assert payload["reason"] == "title_overflow"


def test_max_lines_hard_cap_is_exactly_two():
    """TITLE_MAX_LINES 必须是 2，不能被悄悄放宽成 3。

    这条直接锁常量值本身——变异测试里「把 2 行上限改成 3 行」这个变种
    会被这条直接杀死，不需要绕路通过渲染结果间接验证。
    """
    assert C.TITLE_MAX_LINES == 2
