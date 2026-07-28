"""分屏访谈侦测（``steps/step1_split_screen.py`` + ``produce.py`` 的 input 接线）。

帕伯莱那条 Zoom 分屏访谈（``NVD-m9seDe4``，1280x720）没传 ``cover_crop``，
封面把「左边采访者 + 右边帕伯莱」整张烧了进去，7 集封面全废。这里钉三件事：

1. **认得出来**：固定机位的左右分屏，主讲人在右半 / 左半都要给出正确的
   ``W:H:X:Y``；单人正面画面和多镜头切换**不能**误触发。
2. **认不出来就退 3**：两半势均力敌、或者主讲人簇中心落在画面正中带时，
   ``produce`` 必须带 ``split_screen_indeterminate`` 退 3。**绝不**默认取右半 ——
   猜错方向就是把采访者的脸配上主讲人的角标发出去，和硬出一样不可撤回。
3. **显式优先**：用户填了 ``cover_crop`` 就一帧都不抽，原样透传。

## 期望值都写死，不引被测模块的常数

阈值一律以字面量出现在用例里（``1280`` 画宽下的 ``512px`` 间距、``64px``
抖动上限、``1.3`` 得分比……）。拿 ``SPLIT_DETECT_*`` 自己当参数喂用例的话，
把阈值改松一格测试照样全绿，等于没测。常数本身另有一条契约用例逐个钉字面值。

## 变异验证

带外跑过下面 20 个变异体，20 个全被杀。改动本文件前先确认它们还杀得死：

* M1 中心间距 ``<`` 改 ``>``（``..._one_pixel_short_...`` / ``..._exactly_at_the_gate_...``）
* M2 半区判定写死 ``"right"``（``..._left_half_speaker_...``）
* M3 拆掉势均力敌闸门（``..._dominance_one_notch_below_the_gate_...``）
* M4 拆掉正中带闸门（``..._center_band_is_indeterminate``）
* M5 双人帧门槛 4 改 1（``..._multi_shot_video_with_a_few_split_...``）
* M6 拆掉帧间漂移闸门（``..._shot_cutting_between_two_speakers_...``）
* M7 margin 写死 130（``..._margin_comes_from_the_measured_letterbox``）
* M8 ``_widest_pair`` 不按 x 排序（``..._follows_x_order_not_area_order``）
* M9 得分只算面积、不乘出现帧数（``..._appearance_count_is_part_of_the_score``）
* M10 显式 ``cover_crop`` 不再短路侦测（``..._explicit_cover_crop_wins_...``）
* M11 判不准时改成静默返回 None（``..._indeterminate_exits_three_...``）
* M12 侦测器缺失时当成「非分屏」（``..._detector_unavailable_exits_three``）
* M13 势均力敌阈值 1.3 放到 1.0（``..._dominance_one_notch_below_the_gate_...``）
* M14 中心间距阈值 0.40 放到 0.05（``..._two_faces_too_close_together_...``）
* M15 帧间漂移阈值 0.05 放到 0.50（``..._shot_cutting_between_two_speakers_...``）
* M16 ``safe_margin`` 不再避让人脸（``..._margin_never_clips_a_detected_face``）
* M17 正中带上界 0.60 放到 0.99（``..._right_half_speaker_...`` 等 16 条）
* M18 crop 的 X 恒为 0（``..._right_half_speaker_...``）
* M19 margin 不再取偶数（``..._margin_is_even_so_the_crop_height_...``）
* M20 零面积簇也照算得分比（``..._zero_area_cluster_is_indeterminate_...``）

M13–M17 是「把阈值放松一格」——它们能被杀，说明界线用例写的是字面量而不是把
``SPLIT_DETECT_*`` 喂回自己。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import produce                                    # noqa: E402
import step1_split_screen as SPLIT                 # noqa: E402

# 复用把整条流水线换成计数器的夹具，接线用例才和多集回归跑在同一套替身上。
from test_produce_episodes import harness          # noqa: E402,F401

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

HAS_FFMPEG = bool(SPLIT._extract.__module__) and \
    subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0

# 源片几何：帕伯莱那条是 720p，Zoom 把两块画面并排放在中间，上下各留黑边。
FRAME_W, FRAME_H = 1280, 720
BAR = 130                       # 实测黑边高度，crop 的 margin 应当等于它
EXPECTED_RIGHT_CROP = "640:460:640:130"
EXPECTED_LEFT_CROP = "640:460:0:130"


# ── 造观测量（不碰磁盘，判定层用）──────────────────────────────────────────

def face(center_x, center_y, width, height=None):
    """以中心点和边长描述一个人脸框，返回 ``(x, y, w, h)``。"""
    height = width if height is None else height
    return (center_x - width // 2, center_y - height // 2, width, height)


def frame(*faces, size=(FRAME_W, FRAME_H), bars=(BAR, BAR)):
    return {"faces": list(faces), "size": size, "bars": bars}


def split_frames(left_w, right_w, count=5, cy=360, bars=(BAR, BAR),
                 left_cx=320, right_cx=960):
    """``count`` 帧固定机位的左右分屏观测量。"""
    return [frame(face(left_cx, cy, left_w), face(right_cx, cy, right_w),
                  bars=bars) for _ in range(count)]


# ── 认得出来：右半 / 左半 ──────────────────────────────────────────────────

def test_right_half_speaker_yields_the_right_half_crop():
    """被访者在右半、镜头更近脸更大 —— 就是帕伯莱那条的形态。"""
    v = SPLIT.split_screen_verdict(split_frames(120, 220))
    assert v["split_screen"] is True
    assert v["speaker_half"] == "right"
    assert v["crop"] == EXPECTED_RIGHT_CROP


def test_left_half_speaker_yields_the_left_half_crop():
    """镜面情形必须给出 X=0 —— 写死「取右半」的实现会死在这条上。"""
    v = SPLIT.split_screen_verdict(split_frames(220, 120))
    assert v["speaker_half"] == "left"
    assert v["crop"] == EXPECTED_LEFT_CROP
    assert v["crop"].split(":")[2] == "0"


def test_crop_is_a_valid_ffmpeg_crop_expression():
    """产出的字符串要能被已有的 --cover-crop 通路原样吃下去。"""
    crop = SPLIT.split_screen_verdict(split_frames(120, 220))["crop"]
    assert produce.COVER_CROP_RE.fullmatch(crop)
    assert re.fullmatch(r"^\d+:\d+:\d+:\d+$", crop)


def test_left_right_follows_x_order_not_area_order():
    """两张最大的脸要按 x 排成（左, 右）。按面积排会把左右判反。"""
    pair = SPLIT._widest_pair([face(960, 360, 120), face(320, 360, 220)])
    assert pair[0][0] < pair[1][0]
    v = SPLIT.split_screen_verdict(split_frames(220, 120))
    assert v["speaker_half"] == "left"


def test_only_the_two_largest_faces_define_the_two_halves():
    """画面中间还有第三个人时，取最大两张脸，不能被中间那张带偏。"""
    frames = [frame(face(320, 360, 220), face(640, 300, 60),
                    face(960, 360, 120)) for _ in range(5)]
    v = SPLIT.split_screen_verdict(frames)
    assert v["speaker_half"] == "left"
    assert v["crop"] == EXPECTED_LEFT_CROP


# ── 不能误触发：单人正面、多镜头切换 ──────────────────────────────────────

def test_single_front_facing_speaker_does_not_trigger():
    """单机位正面特写：一帧一张脸，压根不是分屏，必须保持整帧行为。"""
    frames = [frame(face(640, 320, 260)) for _ in range(5)]
    v = SPLIT.split_screen_verdict(frames)
    assert v["split_screen"] is False
    assert v["crop"] is None
    assert v["checks"]["paired_frames"] == 0


def test_two_faces_too_close_together_is_one_camera_not_a_split():
    """同机位坐一起的两个人：中心只隔 300px（1280 的 23%），不是分屏。"""
    frames = [frame(face(560, 360, 160), face(860, 360, 160))
              for _ in range(5)]
    v = SPLIT.split_screen_verdict(frames)
    assert v["split_screen"] is False


def test_multi_shot_video_with_a_few_split_segments_does_not_trigger():
    """多镜头剪辑里夹了 2 帧分屏：5 帧里不足 4 帧成对，按单机位处理。

    只有 2/5 帧是分屏时把整张封面裁掉一半，剩下 3/5 的单机位画面会被裁成半张脸。
    """
    frames = split_frames(120, 220, count=2) + \
        [frame(face(640, 320, 240)) for _ in range(3)]
    v = SPLIT.split_screen_verdict(frames)
    assert v["split_screen"] is False
    assert v["crop"] is None
    assert v["checks"]["paired_frames"] == 2


def test_shot_cutting_between_two_speakers_is_not_a_fixed_split():
    """每帧都有两张脸，但两个簇的中心在帧间乱跑 —— 那是切镜头，不是固定分屏。"""
    frames = [frame(face(200 + 70 * i, 360, 160),
                    face(900 + 70 * i, 360, 220)) for i in range(5)]
    v = SPLIT.split_screen_verdict(frames)
    assert v["split_screen"] is False
    assert v["checks"]["cluster_jitter_px"] == 280.0


def test_no_usable_frames_is_not_a_split():
    assert SPLIT.split_screen_verdict([])["split_screen"] is False


def test_frames_of_mixed_sizes_are_not_judged():
    frames = split_frames(120, 220, count=4) + \
        [frame(face(320, 200, 120), face(960, 200, 220), size=(854, 480))]
    assert SPLIT.split_screen_verdict(frames)["split_screen"] is False


# ── 界线：中心间距 ────────────────────────────────────────────────────────
# 画宽 1280 → 门槛是 512px。两侧各钉一格，免得闸门被改严或改松还全绿。

def test_center_gap_exactly_at_the_gate_counts_as_split():
    v = SPLIT.split_screen_verdict(
        split_frames(120, 220, left_cx=384, right_cx=896))
    assert v["split_screen"] is True
    assert v["speaker_half"] == "right"


def test_center_gap_one_pixel_short_is_not_split():
    v = SPLIT.split_screen_verdict(
        split_frames(120, 220, left_cx=385, right_cx=896))
    assert v["split_screen"] is False
    assert v["checks"]["paired_frames"] == 0


# ── 界线：人脸簇的帧间漂移 ────────────────────────────────────────────────
# 画宽 1280 → 上限是 64px（不含）。

@pytest.mark.parametrize("drift,is_split", [(62, True), (64, False)])
def test_cluster_jitter_gate_sits_at_sixty_four_pixels(drift, is_split):
    frames = [frame(face(320 + (drift if i else 0), 360, 120),
                    face(960, 360, 220)) for i in range(5)]
    v = SPLIT.split_screen_verdict(frames)
    assert v["split_screen"] is is_split
    assert v["checks"]["cluster_jitter_px"] == float(drift)


# ── 界线：势均力敌 → 退 3 ─────────────────────────────────────────────────
# 得分 = 人脸面积总和 × 出现帧数，两簇之比低于 1.3 就是认不出来。

def _lopsided(right_width, height=100):
    """左右各 5 帧、只有宽度不同，用来把得分比调到小数位。"""
    return [frame(face(320, 360, 100, height),
                  face(960, 360, right_width, height)) for _ in range(5)]


def test_dominance_exactly_at_the_gate_is_decided():
    v = SPLIT.split_screen_verdict(_lopsided(130))
    assert v["checks"]["dominance_ratio"] == 1.3
    assert v["split_screen"] is True
    assert v["speaker_half"] == "right"
    assert v["crop"] == EXPECTED_RIGHT_CROP


def test_dominance_one_notch_below_the_gate_is_indeterminate():
    v = SPLIT.split_screen_verdict(_lopsided(129))
    assert v["checks"]["dominance_ratio"] == 1.29
    assert v["split_screen"] is True, "是分屏，只是认不出哪半"
    assert v["crop"] is None
    assert v["speaker_half"] is None


def test_evenly_matched_halves_are_indeterminate():
    """两边脸一样大：谁是主讲人无从判断，宁可退 3 也不猜右半。"""
    v = SPLIT.split_screen_verdict(split_frames(200, 200))
    assert v["split_screen"] is True
    assert v["crop"] is None
    assert v["checks"]["dominance_ratio"] == 1.0
    assert "势均力敌" in v["detail"]


def test_zero_area_cluster_is_indeterminate_not_a_landslide():
    """一侧检出的框退化成零面积时，不能算成「另一侧压倒性胜出」。

    得分是面积×帧数，除数为 0 —— 不拦就是 ZeroDivisionError 把 input 阶段带崩，
    拦成「另一半赢」更糟：那等于拿一个坏检测结果去钉裁切方向。
    """
    v = SPLIT.split_screen_verdict(split_frames(0, 200))
    assert v["split_screen"] is True
    assert v["crop"] is None
    assert v["checks"]["cluster_score"]["left"] == 0


def test_appearance_count_is_part_of_the_score():
    """只比面积不够：主讲人还会在采访者不出镜的独镜里单独出现。

    这里两边每帧脸一样大，只有右边多出一帧独镜 —— 只按面积算得分比是
    1.25（判不出来），乘上出现帧数才是 1.5625（判得出来，checks 里留三位小数）。
    """
    frames = split_frames(200, 200, count=4) + [frame(face(960, 360, 200))]
    v = SPLIT.split_screen_verdict(frames)
    assert v["checks"]["cluster_frames"] == {"left": 4, "right": 5}
    assert v["checks"]["dominance_ratio"] == 1.562
    assert v["speaker_half"] == "right"


# ── 界线：主讲人簇落在画面正中带 → 退 3 ───────────────────────────────────
# 中心占画宽 [40%, 60%] 说明左右分割线判错了，1280 上就是 [512, 768]。

def test_center_band_is_indeterminate():
    v = SPLIT.split_screen_verdict(
        split_frames(220, 120, left_cx=512, right_cx=1100))
    assert v["checks"]["speaker_center_ratio"] == 0.4
    assert v["split_screen"] is True
    assert v["crop"] is None
    assert "正中带" in v["detail"]


def test_just_outside_the_center_band_is_decided():
    v = SPLIT.split_screen_verdict(
        split_frames(220, 120, left_cx=499, right_cx=1100))
    assert v["checks"]["speaker_center_ratio"] < 0.4
    assert v["speaker_half"] == "left"
    assert v["crop"] == EXPECTED_LEFT_CROP


# ── margin 来自实测黑边，不是写死的数 ─────────────────────────────────────

@pytest.mark.parametrize("bar,expected", [
    (0, "640:720:640:0"),          # 满幅分屏，没有黑边可避
    (60, "640:600:640:60"),
    (130, "640:460:640:130"),      # 帕伯莱那条的实测值
    (180, "640:360:640:180"),      # 两块 16:9 画面居中时的黑边
])
def test_margin_comes_from_the_measured_letterbox(bar, expected):
    """同一批人脸、不同黑边高度 → 不同 margin。写死 130 的实现只能过一档。"""
    v = SPLIT.split_screen_verdict(
        split_frames(120, 220, cy=360, bars=(bar, bar)))
    assert v["crop"] == expected
    assert v["checks"]["margin_px"] == bar


def test_asymmetric_bars_use_the_thinner_side():
    """裁切是上下对称的，按厚的那侧算会从另一侧切进画面内容。"""
    v = SPLIT.split_screen_verdict(split_frames(120, 220, bars=(200, 80)))
    assert v["checks"]["margin_px"] == 80


def test_margin_never_clips_a_detected_face():
    """黑边量得比人脸还高时以人脸为准 —— 切掉半张脸的封面不如不裁。"""
    frames = [frame(face(320, 200, 120), face(960, 200, 200), bars=(300, 300))
              for _ in range(5)]
    v = SPLIT.split_screen_verdict(frames)
    assert v["checks"]["margin_px"] == 100         # 最上那张脸的顶边在 y=100
    assert v["crop"] == "640:520:640:100"


def test_margin_is_capped_so_most_of_the_height_survives():
    """就算量出半屏黑边，上下各裁也不超过画高的 1/4（720 → 180）。"""
    v = SPLIT.split_screen_verdict(
        split_frames(120, 220, cy=360, bars=(320, 320)))
    assert v["checks"]["margin_px"] == 180


def test_margin_is_even_so_the_crop_height_stays_even():
    v = SPLIT.split_screen_verdict(split_frames(120, 220, bars=(131, 131)))
    assert v["checks"]["margin_px"] == 130


# ── 真图：ffmpeg 合成的 1280x720 分屏 ─────────────────────────────────────
# haar 级联真的跑一遍。不下 YouTube，用 ffmpeg 把两块半屏 hstack 起来再 pad 出
# 上下黑边，几何和 NVD-m9seDe4 一致：左边采访者脸小、右边主讲人脸大。

TILE_BG, PATCH_BG, SKIN, EYE = 128, 120, 200, 20


def _face_patch(size):
    """haar 级联认得出的粗糙合成脸（肤色椭圆 + 两只深眼 + 鼻影 + 嘴）。"""
    img = np.full((size, size, 3), PATCH_BG, np.uint8)
    cv2.ellipse(img, (size // 2, size // 2),
                (int(size * .34), int(size * .44)), 0, 0, 360, (SKIN,) * 3, -1)
    for dx in (-.16, .16):
        cv2.ellipse(img, (int(size / 2 + dx * size), int(size * .40)),
                    (int(size * .09), int(size * .055)), 0, 0, 360, (EYE,) * 3, -1)
    cv2.ellipse(img, (size // 2, int(size * .56)),
                (int(size * .05), int(size * .09)), 0, 0, 360, (SKIN - 40,) * 3, -1)
    cv2.ellipse(img, (size // 2, int(size * .70)),
                (int(size * .15), int(size * .05)), 0, 0, 360, (EYE + 30,) * 3, -1)
    return img


def _half_tile(path, face_size):
    """一块 640x460 的半屏画面，脸居中。"""
    img = np.full((460, 640, 3), TILE_BG, np.uint8)
    patch = _face_patch(face_size)
    x0, y0 = (640 - face_size) // 2, (460 - face_size) // 2
    img[y0:y0 + face_size, x0:x0 + face_size] = patch
    cv2.imwrite(str(path), img)
    return path


def _compose(out, *tiles, pad=True):
    """用 ffmpeg 把半屏横向拼起来，再上下 pad 出黑边。"""
    filt = f"{''.join(f'[{i}]' for i in range(len(tiles)))}" \
           f"hstack=inputs={len(tiles)}" if len(tiles) > 1 else "[0]null"
    if pad:
        filt += f",pad={FRAME_W}:{FRAME_H}:0:{BAR}:black"
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    for t in tiles:
        cmd += ["-i", str(t)]
    cmd += ["-filter_complex", filt, str(out)]
    subprocess.run(cmd, check=True)
    return out


@pytest.fixture(scope="module")
def split_image(tmp_path_factory):
    d = tmp_path_factory.mktemp("split")
    return _compose(d / "split.jpg",
                    _half_tile(d / "a.png", 120),      # 采访者，脸小
                    _half_tile(d / "b.png", 220))      # 主讲人，脸大


@pytest.fixture(scope="module")
def single_image(tmp_path_factory):
    d = tmp_path_factory.mktemp("single")
    tile = np.full((FRAME_H, FRAME_W, 3), TILE_BG, np.uint8)
    patch = _face_patch(260)
    tile[160:160 + 260, 510:510 + 260] = patch
    path = d / "single.jpg"
    cv2.imwrite(str(path), tile)
    return path


pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg 合成夹具")


def test_the_fixture_really_shows_two_faces_one_per_half(split_image):
    faces, size = SPLIT.detect_faces(str(split_image))
    assert size == (FRAME_W, FRAME_H)
    assert len(faces) == 2
    centers = sorted(x + w / 2 for x, _, w, _ in faces)
    assert centers[0] < FRAME_W / 2 < centers[1]


def test_letterbox_bars_measure_the_black_bands(split_image):
    assert SPLIT.letterbox_bars(str(split_image)) == (BAR, BAR)


def test_a_frame_without_black_bands_measures_zero(single_image):
    assert SPLIT.letterbox_bars(str(single_image)) == (0, 0)


def test_real_frames_yield_the_pabrai_crop(split_image):
    """整条链跑真图：haar 检测 → 归簇 → 黑边测量 → crop 字符串。"""
    frames = [SPLIT.observe(str(split_image)) for _ in range(5)]
    v = SPLIT.split_screen_verdict(frames)
    assert v["speaker_half"] == "right"
    assert v["crop"] == EXPECTED_RIGHT_CROP


def test_a_real_single_speaker_frame_does_not_trigger(single_image):
    frames = [SPLIT.observe(str(single_image)) for _ in range(5)]
    assert SPLIT.split_screen_verdict(frames)["split_screen"] is False


def test_unreadable_image_is_not_an_observation(tmp_path):
    assert SPLIT.observe(str(tmp_path / "missing.jpg")) is None


# ── 真视频：抽帧 + 判定 ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def split_video(tmp_path_factory, split_image):
    path = tmp_path_factory.mktemp("vid") / "split.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-loop", "1",
                    "-i", str(split_image), "-t", "12", "-r", "5",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18",
                    str(path)], check=True)
    return path


def test_detect_from_video_reads_the_split_interview(split_video, tmp_path):
    v = SPLIT.detect_from_video(str(split_video), str(tmp_path / "probe"), 12.0)
    assert v["crop"] == EXPECTED_RIGHT_CROP
    assert v["checks"]["paired_frames"] == 5


def test_sampled_times_stay_inside_the_clip():
    times = SPLIT.sample_times(60.0)
    assert len(times) == 5
    assert times == [10.0, 20.0, 30.0, 40.0, 50.0]


def test_unreadable_video_is_skipped_not_misjudged(tmp_path):
    """片子读不出来不是「侦测失败」：选帧那一步同样截不出候选，会自己退。"""
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"not a video")
    v = SPLIT.detect_from_video(str(bad), str(tmp_path / "probe"), 12.0)
    assert v["split_screen"] is False
    assert v["crop"] is None


def test_missing_cascade_is_raised_not_swallowed(split_video, tmp_path,
                                                 monkeypatch):
    """片子是好的、只是侦测器没了 —— 这时候当成「非分屏」会静默出废封面。"""
    monkeypatch.setattr(SPLIT, "_get_cascade", lambda: None)
    with pytest.raises(SPLIT.DetectorUnavailable):
        SPLIT.detect_from_video(str(split_video), str(tmp_path / "p"), 12.0)


# ── 纯本地：不许打外部 API ────────────────────────────────────────────────

def test_detection_never_touches_the_network():
    src = (ROOT / "steps" / "step1_split_screen.py").read_text(encoding="utf-8")
    for banned in ("requests", "aiohttp", "urllib", "sf_client",
                   "siliconflow", "SILICONFLOW"):
        assert banned not in src, f"分屏侦测里出现了 {banned}，必须纯本地"


# ── produce.py 的接线 ────────────────────────────────────────────────────

@pytest.fixture
def crops(harness, monkeypatch):
    """记录 select_cover_frame 实际收到的 cover_crop。"""
    seen = []

    def recorder(video, quotes, speaker, work, api_key, no_vlm,
                 cover_time_sec=None, candidates=None, cover_crop=None,
                 allow_unverified=False):
        seen.append(cover_crop)
        return "frame.jpg", {"files": {}, "cover_source": "auto",
                             "cover_crop": cover_crop}

    monkeypatch.setattr(produce, "select_cover_frame", recorder)
    return seen


def fake_detection(monkeypatch, verdict):
    calls = []

    def detect(video, tmp_dir, duration_sec):
        calls.append(video)
        return verdict
    monkeypatch.setattr(produce.SPLIT, "detect_from_video", detect)
    return calls


SPLIT_VERDICT = {"split_screen": True, "crop": EXPECTED_RIGHT_CROP,
                 "speaker_half": "right", "detail": "分屏，主讲人在右半区",
                 "checks": {"paired_frames": 5}}
INDETERMINATE_VERDICT = {"split_screen": True, "crop": None,
                         "speaker_half": None, "detail": "左右两半势均力敌",
                         "checks": {"dominance_ratio": 1.02}}
NO_SPLIT_VERDICT = {"split_screen": False, "crop": None, "speaker_half": None,
                    "detail": "单机位", "checks": {"paired_frames": 0}}


def test_auto_crop_reaches_cover_select(crops, monkeypatch):
    fake_detection(monkeypatch, SPLIT_VERDICT)
    assert produce.main(["--source", "s.mp4", "--slug", "munger"]) == 0
    assert crops == [EXPECTED_RIGHT_CROP]


def test_non_split_source_keeps_the_full_frame(crops, monkeypatch):
    fake_detection(monkeypatch, NO_SPLIT_VERDICT)
    assert produce.main(["--source", "s.mp4", "--slug", "munger"]) == 0
    assert crops == [None]


def test_explicit_cover_crop_wins_and_skips_detection(crops, monkeypatch):
    """用户填了就以用户为准，而且一帧都不抽。"""
    calls = fake_detection(monkeypatch, SPLIT_VERDICT)
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--cover-crop", "854:396:0:0"]) == 0
    assert crops == ["854:396:0:0"]
    assert calls == [], "显式给了 cover_crop 还去跑侦测"


def test_auto_crop_applies_to_every_episode(crops, harness, monkeypatch):
    fake_detection(monkeypatch, SPLIT_VERDICT)
    harness.set_candidates([(i * 70.0, i * 70.0 + 60.0) for i in range(9)])
    assert produce.main(["--source", "s.mp4", "--slug", "munger",
                         "--episodes", "3"]) == 0
    assert crops == [EXPECTED_RIGHT_CROP] * 3


def test_indeterminate_exits_three_with_a_diagnosis(crops, monkeypatch, capsys):
    """认不出主讲人半区就退 3，绝不默认取右半、也不静默不裁。"""
    fake_detection(monkeypatch, INDETERMINATE_VERDICT)
    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger"])

    assert e.value.code == 3
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["stage"] == "input"
    assert payload["reason"] == "split_screen_indeterminate"
    assert payload["detail"] == "左右两半势均力敌"
    assert payload["checks"] == {"dominance_ratio": 1.02}
    assert "cover_crop" in payload["hint"]
    assert crops == [], "判不准还继续往下走到了选帧"


def test_detector_unavailable_exits_three(crops, monkeypatch, capsys):
    def boom(*a, **k):
        raise produce.SPLIT.DetectorUnavailable("haar 级联不可用")
    monkeypatch.setattr(produce.SPLIT, "detect_from_video", boom)

    with pytest.raises(SystemExit) as e:
        produce.main(["--source", "s.mp4", "--slug", "munger"])

    assert e.value.code == 3
    payload = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert payload["reason"] == "split_screen_detector_unavailable"
    assert crops == []


def test_input_meta_records_the_auto_crop(crops, monkeypatch):
    fake_detection(monkeypatch, SPLIT_VERDICT)
    produce.main(["--source", "s.mp4", "--slug", "munger"])
    meta = json.loads((Path("_tmp") / "munger" / "input.meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["auto_cover_crop"] == EXPECTED_RIGHT_CROP
    assert meta["cover_crop"] is None
    assert meta["cover_crop_source"] == "auto_split_screen"
    assert meta["split_screen_detection"]["detail"] == "分屏，主讲人在右半区"


def test_input_meta_records_the_explicit_override(crops, monkeypatch):
    fake_detection(monkeypatch, SPLIT_VERDICT)
    produce.main(["--source", "s.mp4", "--slug", "munger",
                  "--cover-crop", "854:396:0:0"])
    meta = json.loads((Path("_tmp") / "munger" / "input.meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["auto_cover_crop"] is None
    assert meta["cover_crop"] == "854:396:0:0"
    assert meta["cover_crop_source"] == "explicit"
    assert meta["split_screen_detection"]["skipped"] == "explicit_cover_crop"


@pytest.mark.parametrize("argv,crop,source", [
    ([], EXPECTED_RIGHT_CROP, "auto_split_screen"),
    (["--cover-crop", "854:396:0:0"], "854:396:0:0", "explicit"),
])
def test_deliverable_meta_records_where_the_crop_came_from(
        crops, monkeypatch, argv, crop, source):
    fake_detection(monkeypatch, SPLIT_VERDICT)
    produce.main(["--source", "s.mp4", "--slug", "munger", *argv])
    meta = json.loads((Path("deliver") / "munger" / "meta.json")
                      .read_text(encoding="utf-8"))
    assert meta["cover_crop"] == crop
    assert meta["cover_crop_source"] == source


# ── 常数契约：阈值不许为了让测试变绿被改动 ────────────────────────────────

def test_split_detect_thresholds_are_pinned():
    assert SPLIT.SPLIT_DETECT_SAMPLE_FRAMES == 5
    assert SPLIT.SPLIT_DETECT_MIN_PAIRED_FRAMES == 4
    assert SPLIT.SPLIT_DETECT_MIN_FACES_PER_FRAME == 2
    assert SPLIT.SPLIT_DETECT_MIN_CENTER_GAP_RATIO == 0.40
    assert SPLIT.SPLIT_DETECT_MAX_CLUSTER_JITTER_RATIO == 0.05
    assert SPLIT.SPLIT_DETECT_MIN_DOMINANCE_RATIO == 1.3
    assert SPLIT.SPLIT_DETECT_HALF_MIN_RATIO == 0.40
    assert SPLIT.SPLIT_DETECT_HALF_MAX_RATIO == 0.60
    assert SPLIT.SPLIT_DETECT_MAX_MARGIN_RATIO == 0.25


def test_every_new_threshold_carries_the_split_detect_prefix():
    """新阈值一律带前缀，和封面那套出片闸门井水不犯河水。"""
    src = (ROOT / "steps" / "step1_split_screen.py").read_text(encoding="utf-8")
    names = re.findall(r"(?m)^([A-Z][A-Z0-9_]*) = ", src)
    assert names, "一个模块级常数都没找到，正则失效了"
    assert [n for n in names if not n.startswith("SPLIT_DETECT_")] == []


def test_cover_gates_are_untouched_by_this_feature():
    """这个特性只产出一个默认 cover_crop，封面侧的闸门一格都不动。"""
    assert produce.COVER.MIN_FACE_AREA_RATIO == 0.05
    assert produce.COVER.MIN_VLM_PASS_SCORE == 6
    assert produce.COVER.FACE_TOP_RATIO == 0.6
    assert produce.COVER.DEFAULT_COVER_CANDIDATES == 24
