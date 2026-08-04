"""封面平台安全区与出图自检闸门（steps/step7_cover.py）。

历史缺陷：出图尺寸被当成了平台显示尺寸。抖音对 1080x1920 只显示中间
1080x1464（上下各切 228px），B站官方推荐 16:10（1280x720 要左右各切 64px）
并在左下角压一枚分区标签。旧排版把角标钉在 y=20、标题起笔在 x=40、标题块落
到画面最底，三处全在被切掉的那一圈里 —— 抖音上竖版只剩一条人脸，横版第一个
字被啃掉、整行还被分区标签盖住。

这里锁三件事：安全区常量、包围盒量得准、越界必须退 2（不许硬出）。
"""

import json

import pytest
from PIL import Image, ImageDraw

import produce
import step7_cover as C

LANDSCAPE = (1280, 720)
PORTRAIT = (1080, 1920)
SRC_SIZE = (854, 340)          # 被 --cover-crop 854:340:0:70 切过之后的真实尺寸
TITLE = "耐心不是美德，而是这门生意的入场券"
SPEAKER = "查理·芒格"
FRAME_GRAY = 160          # 中性灰源帧，白字、黑描边、红角标三样都量得出来


@pytest.fixture
def frame(tmp_path):
    path = str(tmp_path / "frame.jpg")
    Image.new("RGB", SRC_SIZE, (FRAME_GRAY,) * 3).save(path, "JPEG", quality=95)
    return path


def _render(frame, tmp_path, target, title=TITLE, speaker=SPEAKER):
    out = str(tmp_path / f"cover_{target[0]}x{target[1]}.jpg")
    return C.make_cover(frame, title, speaker, out, target), out


# ── 安全区常量 ───────────────────────────────────────────────────────────────

def test_portrait_safe_area_is_the_central_1080x1440():
    # 抖音实切上下各 228px；收到 240 再留 12px 余量
    assert C.platform_safe_area(PORTRAIT) == (0, 240, 1080, 1680)


def test_landscape_safe_area_survives_the_16x10_center_crop():
    # 1280x720 裁成 16:10 要左右各切 64px；收到 80 再留 16px 余量
    assert C.platform_safe_area(LANDSCAPE) == (80, 0, 1200, 720)


def test_safe_area_scales_with_resolution():
    """常量是比例不是像素，--size 传 4K 也得成立。"""
    assert C.platform_safe_area((2160, 3840)) == (0, 480, 2160, 3360)
    assert C.platform_safe_area((2560, 1440)) == (160, 0, 2400, 1440)


def test_landscape_reserves_the_bottom_chrome_band():
    """B站在下沿压分区标签（左）和时长（右），这条带子不能放东西。"""
    assert C.platform_chrome_zone(LANDSCAPE) == (0, 598, 1280, 720)


def test_portrait_has_no_chrome_band():
    assert C.platform_chrome_zone(PORTRAIT) is None


# ── 包围盒计算 ───────────────────────────────────────────────────────────────

def _assert_box_hugs(mask, box, name, tol=4, pad=8):
    """报出来的盒子必须紧贴 ``mask`` 里的墨：里面贴四边，外面一圈干净。

    量错一套坐标，闸门再严也是在校验虚构，所以这条要独立验。

    tol 从 3 调到 4：换成 Noto Sans CJK SC（修复问题3的字体面选择）后，
    个别汉字（如“耐”）的左侧字身间距比之前默认的 JP 面大 1px，
    这是字体本身的 glyph 度量差异，不是包围盒计算逻辑错了。
    """
    l, t, r, b = (round(v) for v in box)
    inner = mask.crop((l, t, r, b))
    ink = inner.getbbox()
    assert ink is not None, f"{name} 的包围盒里没有墨迹"
    assert ink[0] <= tol and ink[1] <= tol, f"{name} 的包围盒左/上虚了"
    assert (r - l) - ink[2] <= tol and (b - t) - ink[3] <= tol, \
        f"{name} 的包围盒右/下虚了"

    ring = mask.crop((l - pad, t - pad, r + pad, b + pad)).copy()
    ring.paste(Image.new("L", inner.size, 0), (pad, pad))
    assert ring.getbbox() is None, f"{name} 的包围盒之外还有墨迹"


@pytest.mark.parametrize("target", [LANDSCAPE, PORTRAIT])
def test_title_box_is_tight_around_the_painted_glyphs(frame, tmp_path, target):
    """标题包围盒要把白字和黑描边一起框住，不多不少。

    源帧是中性灰，压上渐变遮罩之后底色始终在 34~160 之间，所以「纯白」和
    「纯黑」两头就只可能是标题本身的字面和描边。
    """
    boxes, out = _render(frame, tmp_path, target)
    with Image.open(out) as img:
        mask = img.convert("L").point(lambda v: 255 if v > 240 or v < 15 else 0)
    _assert_box_hugs(mask, boxes["标题第1行"], "标题第1行")


@pytest.mark.parametrize("target", [LANDSCAPE, PORTRAIT])
def test_tag_box_is_tight_around_the_painted_chip(frame, tmp_path, target):
    """角标包围盒要框住整块红底，不能只框住里面的字。"""
    boxes, out = _render(frame, tmp_path, target)
    with Image.open(out) as img:
        mask = img.convert("L").point(
            lambda v: 255 if abs(v - FRAME_GRAY) > 40 else 0)
    _assert_box_hugs(mask, boxes["角标"], "角标")


def test_title_box_tracks_a_longer_title(frame, tmp_path):
    """包围盒不是写死的常数：标题变长，盒子必须跟着变宽。"""
    short, _ = _render(frame, tmp_path, LANDSCAPE, title="耐心")
    long_, _ = _render(frame, tmp_path, LANDSCAPE, title="耐心不是美德")
    assert long_["标题第1行"][2] > short["标题第1行"][2]
    assert long_["标题第1行"][0] == short["标题第1行"][0]


def test_tag_box_tracks_a_longer_speaker(frame, tmp_path):
    """角标底色块按真实字宽算，换个长名字盒子必须跟着变宽。"""
    short, _ = _render(frame, tmp_path, LANDSCAPE, speaker="李录")
    long_, _ = _render(frame, tmp_path, LANDSCAPE, speaker="沃伦·巴菲特")
    assert long_["角标"][2] > short["角标"][2]


@pytest.mark.parametrize("target", [LANDSCAPE, PORTRAIT])
def test_rendered_cover_lands_inside_the_safe_area(frame, tmp_path, target):
    # 固定字号下 TITLE 这个长度的标题会换行成 2 行（之前“缩字号塞一行”时只有 1 行），
    # 这正是问题1 要求的行为：字号不因标题长短而变，长了就换行。
    boxes, _ = _render(frame, tmp_path, target)
    assert set(boxes) == {"标题第1行", "标题第2行", "角标"}
    assert C.safe_area_violations(boxes, target) == []


def test_portrait_tag_and_title_moved_off_the_douyin_cut(frame, tmp_path):
    """回归锁：旧版角标 y≈43、标题基线 y≈1850，两个都在抖音切掉的范围里。"""
    boxes, _ = _render(frame, tmp_path, PORTRAIT)
    assert boxes["角标"][1] >= 240
    assert boxes["标题第1行"][3] <= 1680


def test_landscape_title_clears_both_the_side_crop_and_the_corner_label(
        frame, tmp_path):
    """回归锁：旧版标题起笔 x=40（16:10 裁掉）且压在左下角（分区标签盖住）。"""
    title = _render(frame, tmp_path, LANDSCAPE)[0]["标题第1行"]
    assert title[0] >= 80 and title[2] <= 1200
    assert title[3] <= C.platform_chrome_zone(LANDSCAPE)[1]


# ── 越界必须退 2 ─────────────────────────────────────────────────────────────

def test_violation_reports_element_name_and_actual_box():
    boxes = {"角标": (24, 20, 200, 63), "标题第1行": (40, 300, 900, 360)}
    v = C.safe_area_violations(boxes, PORTRAIT)
    assert [x["element"] for x in v] == ["角标"]
    assert v[0]["box"] == (24, 20, 200, 63)
    assert v[0]["limit"] == (0, 240, 1080, 1680)


def test_chrome_band_intrusion_is_reported_separately():
    v = C.safe_area_violations({"标题第1行": (100, 600, 800, 680)}, LANDSCAPE)
    assert len(v) == 1 and v[0]["limit_name"] == "平台占位带"


def test_box_flush_against_the_safe_edge_is_allowed():
    assert C.safe_area_violations({"角标": (0, 240, 1080, 1680)}, PORTRAIT) == []


def test_assert_raises_with_every_offending_element():
    with pytest.raises(C.CoverSafeAreaError) as e:
        C.assert_cover_in_safe_area(
            {"角标": (24, 20, 200, 63), "标题第1行": (40, 1700, 900, 1760)},
            PORTRAIT)
    assert {v["element"] for v in e.value.violations} == {"角标", "标题第1行"}


def test_make_cover_refuses_and_writes_nothing_when_text_overflows(
        monkeypatch, frame, tmp_path):
    """闸门在落盘之前：越界时一个字节都不许写出去。

    安全区被撑成 10x10 后行宽预算变成负数，固定字号下任何标题都会先触发
    TitleOverflowError（行数超限）而不是 CoverSafeAreaError —— 因为现在标题
    不会为了塞进安全区而偷偷缩字号，换行判定在安全区坐标计算之前。
    这个新异常同样是“落盘前拒绝”语义，下面 out.exists() 断言不变。
    """
    monkeypatch.setattr(C, "platform_safe_area", lambda _s: (0, 0, 10, 10))
    out = tmp_path / "cover.jpg"
    with pytest.raises(C.TitleOverflowError):
        C.make_cover(frame, TITLE, SPEAKER, str(out), LANDSCAPE)
    assert not out.exists()


def test_make_cover_still_raises_safe_area_error_for_non_title_overflow(
        frame, tmp_path):
    """安全区闸门不能被标题超行检查换掉：角标（非标题）越界仍要报
    CoverSafeAreaError。讲者名故意写得很长，让角标背景块撞出安全区右沿，
    但标题本身短得一行就能装下，不会触发 TitleOverflowError。
    """
    out = tmp_path / "cover.jpg"
    with pytest.raises(C.CoverSafeAreaError) as e:
        C.make_cover(frame, "耐心", "查理" * 40, str(out), LANDSCAPE)
    assert [v["element"] for v in e.value.violations] == ["角标"]
    assert not out.exists()


def test_overlong_speaker_name_is_rejected_not_silently_clipped(frame, tmp_path):
    """角标底色块撑出安全区右沿时必须拒，不能当没看见。"""
    with pytest.raises(C.CoverSafeAreaError) as e:
        _render(frame, tmp_path, LANDSCAPE, speaker="查理" * 40)
    assert [v["element"] for v in e.value.violations] == ["角标"]


def test_render_covers_exits_two_on_violation(monkeypatch, tmp_path, capsys):
    """produce.py 这条生产路径必须把越界翻成退出码 2（内容质量拒绝）。"""
    def boom(*_a, **_k):
        raise C.CoverSafeAreaError(
            LANDSCAPE, [{"element": "标题第1行", "box": (40, 660, 900, 710),
                         "limit_name": "安全区", "limit": (80, 0, 1200, 720)}])

    monkeypatch.setattr(produce.COVER, "make_cover", boom)
    with pytest.raises(SystemExit) as e:
        produce.render_covers("f.jpg", TITLE, SPEAKER, tmp_path, {})
    assert e.value.code == produce.EXIT_QUALITY

    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["reason"] == "cover_text_outside_safe_area"
    assert payload["violations"][0]["element"] == "标题第1行"
    assert payload["violations"][0]["box"] == [40, 660, 900, 710]


# ── 等比放大人脸带 ───────────────────────────────────────────────────────────

def test_portrait_zoom_makes_the_subject_band_much_bigger():
    """22.4% 太小。放大后至少要到 30%，否则等于没改。"""
    plain = min(PORTRAIT[0] / SRC_SIZE[0], PORTRAIT[1] / SRC_SIZE[1])
    zoomed = C.fit_scale(SRC_SIZE, PORTRAIT)
    assert SRC_SIZE[1] * plain / PORTRAIT[1] == pytest.approx(0.224, abs=0.005)
    assert SRC_SIZE[1] * zoomed / PORTRAIT[1] >= 0.30


def test_landscape_fit_is_not_zoomed():
    """横版人脸带本来就占 71%，不需要放大，也就不该白裁掉两侧。"""
    assert C.fit_zoom(LANDSCAPE) == 1.0
    assert C.fit_scale(SRC_SIZE, LANDSCAPE) == pytest.approx(
        min(LANDSCAPE[0] / SRC_SIZE[0], LANDSCAPE[1] / SRC_SIZE[1]))


def test_zoom_never_exceeds_filling_the_target():
    """已经铺满目标之后再放大只是继续往外裁，没有意义。"""
    src = (1000, 1900)
    assert C.fit_scale(src, PORTRAIT) == pytest.approx(
        max(PORTRAIT[0] / src[0], PORTRAIT[1] / src[1]))


def test_zoom_keeps_the_source_aspect_ratio_exactly(tmp_path):
    """反形变：放大人脸带只许改倍数，不许改比例。

    源帧正中放一个白色正方形，出图后它必须仍是正方形，且边长正好等于
    ``fit_scale`` 那一个倍数 —— 两个方向倍数只要不一致，正方形立刻变长方形。
    """
    side = 120
    path = str(tmp_path / "square.jpg")
    img = Image.new("RGB", SRC_SIZE, (0, 0, 0))
    cx, cy = SRC_SIZE[0] / 2, SRC_SIZE[1] / 2
    ImageDraw.Draw(img).rectangle(
        [cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2],
        fill=(255, 255, 255))
    img.save(path, "JPEG", quality=95)

    scale = C.fit_scale(SRC_SIZE, PORTRAIT)
    out = C.render_geometry(path, PORTRAIT)
    # 背景是同帧放大后虚化的，白块在背景里也会亮起来；只量前景带那一条
    band = round(SRC_SIZE[1] * scale)
    top = (PORTRAIT[1] - band) // 2
    left, t, right, b = out.crop((0, top, PORTRAIT[0], top + band)).convert(
        "L").point(lambda v: 255 if v > 200 else 0).getbbox()

    assert (right - left) == pytest.approx(side * scale, abs=3)
    assert (b - t) == pytest.approx(side * scale, abs=3)
    assert scale * SRC_SIZE[0] >= PORTRAIT[0]     # 横向确实溢出、被裁过


def test_zoomed_crop_stays_centered(monkeypatch, tmp_path):
    """放大窗口一律居中，不许被 haar 的误检拽走。

    实测 partner.mp4 第 1200s 那帧检出的最大框是 (198,9,164,164)、占比 9.3%，
    高于 MIN_FACE_AREA_RATIO 的 5%（门槛拦不住），实际是背景写字楼窗格的误检。
    照它对齐会把窗口拽到最左，芒格被切掉一半。
    """
    path = str(tmp_path / "marked.jpg")
    img = Image.new("RGB", SRC_SIZE, (0, 0, 0))
    ImageDraw.Draw(img).rectangle([417, 150, 437, 190], fill=(255, 255, 255))
    img.save(path, "JPEG", quality=95)

    monkeypatch.setattr(C, "largest_face_box",
                        lambda _p: (0.093, (198, 9, 164, 164), SRC_SIZE))
    out = C.render_geometry(path, PORTRAIT)
    left, _, right, _ = out.convert("L").point(
        lambda v: 255 if v > 200 else 0).getbbox()
    assert (left + right) / 2 == pytest.approx(PORTRAIT[0] / 2, abs=4)
