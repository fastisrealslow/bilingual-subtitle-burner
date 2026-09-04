"""竞品参考池与三级干净画面策略。"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest
import numpy as np


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "linyuan"))
import monitor_v2 as M  # noqa: E402
import produce_cn as P  # noqa: E402


def test_competitor_archive_enters_reference_pool_but_not_dispatch_pool():
    source = M.CompetitorReferenceSource({
        "seeds_file": "up_videos.json",
        "speaker": "林园",
        "author": "园园滚雪球",
    }, {})
    items = source.fetch(None)
    assert len(items) >= 244
    by_bvid = {json.loads(x["extra"])["bvid"]: x for x in items}
    assert "BV1iHdYYAEXe" in by_bvid
    extra = json.loads(by_bvid["BV1iHdYYAEXe"]["extra"])
    assert extra["source_role"] == "reference"
    assert extra["direct_dispatch"] is False


def test_dirty_video_without_safe_crop_uses_clean_audio_card(monkeypatch,
                                                              tmp_path):
    monkeypatch.setattr(P, "detect_corner_logos", lambda *a, **k: [])
    monkeypatch.setattr(P, "safe_crop_plan", lambda *a, **k: None)
    plan = P.build_clean_source_plan(
        Path("dirty.mp4"), tmp_path, 720, 1280, 180, True)
    assert plan["clean_strategy"] == "audio_card"
    assert plan["clean_output_resolution"] == {
        "width": 720, "height": 1280, "short_edge": 720,
    }
    assert plan["clean_video_filter"] == ""


def test_audio_card_ass_uses_yellow_black_outline(tmp_path):
    ass = tmp_path / "card.ass"
    P.make_ass([{
        "start_sec": 0.0, "end_sec": 2.0,
        "zh": "投资要看供需关系", "en": "",
    }], ass, 720, 1280, card_style=True)
    text = ass.read_text(encoding="utf-8-sig")
    style = next(line for line in text.splitlines()
                 if line.startswith("Style: ZH,"))
    assert "&H0000D7FF" in style
    assert ",1,5,1,2," in style


def test_real_video_subtitles_remain_white(tmp_path):
    ass = tmp_path / "direct.ass"
    P.make_ass([{
        "start_sec": 0.0, "end_sec": 2.0,
        "zh": "投资要看供需关系", "en": "",
    }], ass, 1280, 720)
    text = ass.read_text(encoding="utf-8-sig")
    style = next(line for line in text.splitlines()
                 if line.startswith("Style: ZH,"))
    assert "&H00FFFFFF" in style
    assert ",1,3,1,2," in style


def test_audio_card_title_falls_back_to_fixed_width_for_long_words():
    title = "VALUE INVESTING WINS OVER THE VERY LONG TERM"
    lines = P._wrap_audio_card_title(title[:39], 13, max_lines=3)
    assert len(lines) <= 3
    assert "".join(lines) == title[:39]


def test_cleanable_subtitle_band_is_cropped_and_preview_verified(monkeypatch,
                                                                 tmp_path):
    monkeypatch.setattr(P, "detect_corner_logos", lambda *a, **k: [])
    monkeypatch.setattr(P, "safe_crop_plan", lambda *a, **k: (1280, 600, 0, 0))
    preview = tmp_path / "preview.mp4"
    monkeypatch.setattr(P, "_render_clean_preview", lambda *a, **k: preview)
    monkeypatch.setattr(P, "has_existing_subtitles", lambda path: False)
    plan = P.build_clean_source_plan(
        Path("dirty.mp4"), tmp_path, 1280, 720, 180, True)
    assert plan["clean_strategy"] == "crop"
    assert plan["clean_output_resolution"] == {
        "width": 1280, "height": 600, "short_edge": 600,
    }


def test_deep_subtitle_band_is_not_cropped_past_the_safe_limit(monkeypatch):
    coverage = [0.0] * 100
    coverage[70:80] = [1.0] * 10
    monkeypatch.setattr(P, "ocr_row_coverage", lambda *a, **k: coverage)
    monkeypatch.setattr(P, "_face_survives", lambda *a, **k: True)
    assert P.safe_crop_plan(Path("dirty.mp4"), 1280, 720) is None


def test_audio_card_fails_closed_without_chinese_font(monkeypatch, tmp_path):
    real_exists = Path.exists

    def fake_exists(path):
        if str(path).startswith("/usr/share/fonts/"):
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", fake_exists)
    with pytest.raises(P.VisualQualityError, match="缺少中文字体"):
        P.make_audio_card(tmp_path / "audio-card.png", "林园", "投资观点")


def test_audio_card_portrait_comes_from_authority_reference(monkeypatch,
                                                             tmp_path):
    cv2 = pytest.importorskip("cv2")
    reference = tmp_path / "speaker_reference.jpg"
    image = np.zeros((180, 240, 3), dtype=np.uint8)
    image[:, :] = (30, 80, 140)
    assert cv2.imwrite(str(reference), image)

    class FakeCascade:
        def detectMultiScale(self, *args, **kwargs):
            return np.array([[80, 35, 60, 70]])

    monkeypatch.setattr(P, "_cascade", lambda *args: FakeCascade())
    out = P.extract_audio_card_portrait(reference, tmp_path / "portrait.png")
    assert out is not None
    portrait = cv2.imread(str(out))
    assert portrait.shape[0] == portrait.shape[1]


def test_audio_card_does_not_ocr_its_own_template(monkeypatch, tmp_path):
    # 音频卡画布完全由本流程生成；这里固定行为，避免模板标题再次被误报。
    def should_not_run(*args, **kwargs):
        raise AssertionError("audio card must not run external-logo OCR")

    monkeypatch.setattr(P, "detect_corner_logos", should_not_run)
    assert P.detect_external_logos_after_render(
        tmp_path / "card.mp4", "audio_card", 720, 1280) == []


def test_reference_role_never_dispatches_even_if_author_changes():
    item = {
        "id": "competitor_reference:BV-test",
        "source": "competitor_reference",
        "title": "林园：公开发言完整版",
        "url": "https://www.bilibili.com/video/BV-test",
        "author": "名称以后可能变化",
        "extra": {
            "duration": 900,
            "source_role": "reference",
            "direct_dispatch": False,
        },
    }
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "clean_source_fc", ROOT / "linyuan/fc/index.py")
    fc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fc)
    state = {"dispatched": [], "rejected": [], "published": {}}
    assert fc.pick([item], state, 10) == []

    spec = importlib.util.spec_from_file_location(
        "clean_source_local", ROOT / "linyuan/stage_and_dispatch.py")
    local = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(local)
    assert local.pick([item], {"dispatched": [], "rejected": []}, 10) == []


def test_unchanged_reference_does_not_churn_updated_at(monkeypatch, tmp_path):
    db = tmp_path / "monitor.db"
    monkeypatch.setattr(M, "DB_PATH", db)
    M.init_db()
    item = {
        "id": "competitor_reference:BV-stable",
        "source": "competitor_reference",
        "title": "林园：完整访谈",
        "url": "https://www.bilibili.com/video/BV-stable",
        "publish_time": "2026-01-01 00:00:00",
        "author": "园园滚雪球",
        "extra": "{}",
    }
    assert M.upsert_items([item]) == [item]
    with sqlite3.connect(db) as conn:
        first = conn.execute(
            "SELECT updated_at FROM items WHERE id=?", (item["id"],)).fetchone()[0]
    assert M.upsert_items([item]) == []
    with sqlite3.connect(db) as conn:
        second = conn.execute(
            "SELECT updated_at FROM items WHERE id=?", (item["id"],)).fetchone()[0]
    assert first == second
