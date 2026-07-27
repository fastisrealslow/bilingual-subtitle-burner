"""封面出图几何（steps/step7_cover.py）。

历史缺陷：make_cover 里直接 `img.resize(target_size)` 是非等比硬拉伸，
裁过的源帧（实测 854x340，比例 2.51）拉到 1080x1920 会把人脸拽成细长条。
这里的核心断言是「源图上的正圆出图后仍是圆」——只要还有任何非等比缩放，
圆就会变成椭圆，长宽比立刻偏离 1。
"""

import pytest

from PIL import Image, ImageDraw

import step7_cover as C

SRC_SIZE = (854, 340)          # 实测被 --cover-crop 切过之后的典型尺寸
LANDSCAPE = (1280, 720)
PORTRAIT = (1080, 1920)
CIRCLE_D = 200                 # 源图上正圆的直径


@pytest.fixture
def circle_frame(tmp_path):
    """深色底 + 居中的白色正圆，用来测量出图后有没有被拉扁。"""
    img = Image.new("RGB", SRC_SIZE, (20, 20, 20))
    cx, cy = SRC_SIZE[0] / 2, SRC_SIZE[1] / 2
    r = CIRCLE_D / 2
    ImageDraw.Draw(img).ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255))
    path = str(tmp_path / "circle.jpg")
    img.save(path, "JPEG", quality=95)
    return path


def _white_bbox_aspect(img):
    """量出图里白色区域的长宽比。"""
    mask = img.convert("L").point(lambda v: 255 if v > 160 else 0)
    bbox = mask.getbbox()
    assert bbox is not None, "出图里找不到白色图形"
    left, top, right, bottom = bbox
    return (right - left) / (bottom - top)


# ── 分支判定 ─────────────────────────────────────────────────────────────────

def test_landscape_uses_cover_branch():
    # 854x340 填满 1280x720 后仍保留约 71% 宽、100% 高，两个方向都过 60%
    assert C.choose_cover_strategy(SRC_SIZE, LANDSCAPE) == "cover"


def test_portrait_uses_fit_branch():
    # 填满 1080x1920 只剩约 22% 宽度，硬裁会切掉主体且把窄条放大到糊
    assert C.choose_cover_strategy(SRC_SIZE, PORTRAIT) == "fit"


def test_same_aspect_uses_cover_branch():
    assert C.choose_cover_strategy((640, 360), LANDSCAPE) == "cover"


def test_retain_ratio_threshold_is_the_boundary():
    # 恰好保留 60% 宽度：854 * 0.6 ≈ 512 宽的窗口 → 源图宽 = 720/0.6 * (16/9)…
    # 直接按定义构造：目标 1:1，源图 100x60 → 窗口 60x60，保留 60% 宽
    assert C.choose_cover_strategy((100, 60), (500, 500)) == "cover"
    # 再窄一点就掉到阈值下方
    assert C.choose_cover_strategy((110, 60), (500, 500)) == "fit"


# ── 输出尺寸 + 反形变 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("target", [LANDSCAPE, PORTRAIT])
def test_output_size_is_exact(circle_frame, tmp_path, target):
    out = str(tmp_path / "cover.jpg")
    C.make_cover(circle_frame, "测试标题：不要拉伸", "李录", out, target)
    with Image.open(out) as img:
        assert img.size == target


def _fit_foreground(img):
    """抠出 fit 分支里等比缩放后的源图区域。

    背景是同一帧放大后虚化的，白色图形在背景里也会亮起来，
    直接量整图的白色包围盒会把背景一起量进去。
    """
    tw, th = img.size
    scale = min(tw / SRC_SIZE[0], th / SRC_SIZE[1])
    fw = min(tw, round(SRC_SIZE[0] * scale))
    fh = min(th, round(SRC_SIZE[1] * scale))
    x, y = (tw - fw) // 2, (th - fh) // 2
    return img.crop((x, y, x + fw, y + fh))


def test_circle_stays_circular_on_cover_branch(circle_frame):
    """反形变断言：正圆出图后长宽比必须仍接近 1。"""
    img = C.render_geometry(circle_frame, LANDSCAPE)
    assert img.size == LANDSCAPE
    assert _white_bbox_aspect(img) == pytest.approx(1.0, abs=0.06)


def test_circle_stays_circular_on_fit_branch(circle_frame):
    """同上，竖版这一档是旧硬拉伸变形最严重的地方（长宽比掉到 0.2 量级）。"""
    img = C.render_geometry(circle_frame, PORTRAIT)
    assert img.size == PORTRAIT
    assert _white_bbox_aspect(_fit_foreground(img)) == pytest.approx(1.0, abs=0.06)


def test_fit_branch_keeps_the_whole_circle(circle_frame):
    """fit 分支不许裁掉主体：圆的直径应按等比缩放比例整体保留。"""
    fg = _fit_foreground(C.render_geometry(circle_frame, PORTRAIT))
    scale = min(PORTRAIT[0] / SRC_SIZE[0], PORTRAIT[1] / SRC_SIZE[1])
    left, _, right, _ = fg.convert("L").point(
        lambda v: 255 if v > 160 else 0).getbbox()
    assert (right - left) == pytest.approx(CIRCLE_D * scale, rel=0.05)


# ── 裁切窗口对准人脸 ─────────────────────────────────────────────────────────

def _face(x, w=120):
    return (0.05, (x, 40, w, w), SRC_SIZE)


def test_crop_follows_face_to_the_left(monkeypatch, circle_frame):
    monkeypatch.setattr(C, "largest_face_box", lambda p: _face(60))
    left, top, right, bottom = C.cover_crop_box(
        SRC_SIZE, LANDSCAPE, C._face_focus_x(circle_frame, SRC_SIZE))
    centered = C.cover_crop_box(SRC_SIZE, LANDSCAPE)
    assert left < centered[0]
    assert left >= 0 and right <= SRC_SIZE[0]


def test_crop_follows_face_to_the_right(monkeypatch, circle_frame):
    monkeypatch.setattr(C, "largest_face_box", lambda p: _face(700))
    left, top, right, bottom = C.cover_crop_box(
        SRC_SIZE, LANDSCAPE, C._face_focus_x(circle_frame, SRC_SIZE))
    centered = C.cover_crop_box(SRC_SIZE, LANDSCAPE)
    assert left > centered[0]
    assert left >= 0 and right <= SRC_SIZE[0]


@pytest.mark.parametrize("face_x", [-200, 0, 840, 2000])
def test_crop_window_stays_inside_the_image(monkeypatch, circle_frame, face_x):
    monkeypatch.setattr(C, "largest_face_box", lambda p: _face(face_x))
    left, top, right, bottom = C.cover_crop_box(
        SRC_SIZE, LANDSCAPE, C._face_focus_x(circle_frame, SRC_SIZE))
    assert 0 <= left < right <= SRC_SIZE[0]
    assert 0 <= top < bottom <= SRC_SIZE[1]


def test_no_face_falls_back_to_center(monkeypatch, circle_frame):
    monkeypatch.setattr(C, "largest_face_box", lambda p: None)
    assert C._face_focus_x(circle_frame, SRC_SIZE) is None
    box = C.cover_crop_box(SRC_SIZE, LANDSCAPE,
                           C._face_focus_x(circle_frame, SRC_SIZE))
    assert box == C.cover_crop_box(SRC_SIZE, LANDSCAPE)


def test_face_aligned_crop_does_not_distort(monkeypatch, circle_frame):
    """对准人脸只挪窗口位置，不许顺带改变比例。"""
    monkeypatch.setattr(C, "largest_face_box", lambda p: _face(700))
    img = C.render_geometry(circle_frame, LANDSCAPE)
    assert img.size == LANDSCAPE
