"""林园流水线：480P 画质、三重内容指纹和 14 天主题冷却。"""

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "linyuan"))
import produce_cn as P  # noqa: E402


def _load_fc():
    spec = importlib.util.spec_from_file_location("quality_fc", ROOT / "linyuan/fc/index.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FC = _load_fc()


def test_resolution_gate_checks_short_edge_after_crop(monkeypatch):
    monkeypatch.setattr(P, "video_size", lambda src: (854, 479))
    with pytest.raises(P.VisualQualityError, match="短边 479 < 480"):
        P.ensure_min_short_edge(Path("final.mp4"), label="裁切后成片")

    monkeypatch.setattr(P, "video_size", lambda src: (854, 480))
    assert P.ensure_min_short_edge(Path("final.mp4")) == (854, 480)


def test_workflow_rejects_source_below_480():
    workflow = (ROOT / ".github/workflows/linyuan-produce-cn.yml").read_text()
    assert P.MIN_SHORT_EDGE == 480
    assert "--source-check-only" in workflow
    assert workflow.index("name: 素材质量门禁") < workflow.index("name: 出片")


def test_transcript_fingerprint_survives_light_rewrite():
    a = ("科技股长期来看风险很高，但是人工智能确实可能带来革命性的行业机会。"
         "我们不能因为机会就忽视风险。")
    b = ("科技股长期来看风险很高，但人工智能可能带来革命性行业机会。"
         "我们不能因机会忽视风险。")
    other = "慢性病患者会随着人口老龄化增加，医药企业的需求是长期的。"
    fa = {"transcript_ngrams": P.transcript_ngram_fingerprints(a)}
    fb = {"transcript_ngrams": P.transcript_ngram_fingerprints(b)}
    fc = {"transcript_ngrams": P.transcript_ngram_fingerprints(other)}
    assert "转写片段重合" in FC.fingerprint_duplicate(fa, fb)
    assert FC.fingerprint_duplicate(fa, fc) is None


def test_composite_media_fingerprint_requires_audio_and_video():
    current = {"video_dhash": ["0" * 16] * 8,
               "audio_chromaprint": ["0" * 8] * 6}
    same = {"video_dhash": ["0" * 16] * 8,
            "audio_chromaprint": ["0" * 8] * 6}
    only_same_face = {"video_dhash": ["0" * 16] * 8,
                      "audio_chromaprint": ["f" * 8] * 6}
    assert "音频" in FC.fingerprint_duplicate(current, same)
    assert FC.fingerprint_duplicate(current, only_same_face) is None


def test_topic_cooldown_blocks_rephrased_idea_for_14_days():
    now = time.time()
    state = {"published": {"old": {
        "bvid": "BV-old", "ts": now - 2 * 86400,
        "title": "林园：科技股100%风险但AI革命性机会｜林园",
    }}}
    hit = FC.find_recent_topic(
        "林园：科技股100%风险？过去确实如此｜林园", state, now=now)
    assert hit and hit["bvid"] == "BV-old"
    assert FC.find_recent_topic(
        "林园：医学进步救不了衰老，我只投这些｜林园", state, now=now) is None

    state["published"]["old"]["ts"] = now - 15 * 86400
    assert FC.find_recent_topic(
        "林园：科技股100%风险？过去确实如此｜林园", state, now=now) is None


def test_picker_applies_topic_cooldown_before_dispatch():
    now = time.time()
    state = {
        "dispatched": [], "rejected": [], "pending_retry": [],
        "published": {"old": {
            "bvid": "BV-old", "ts": now - 86400,
            "title": "林园：科技股100%风险但AI革命性机会｜林园",
        }},
    }
    items = [
        {"id": "repeat", "title": "林园：科技股100%风险？过去确实如此",
         "url": "https://example.com/repeat", "video_url": "https://cdn/repeat.mp4",
         "source": "weibo", "extra": {"duration": 180}},
        {"id": "fresh", "title": "林园：医学进步救不了衰老，我只投这些",
         "url": "https://example.com/fresh", "video_url": "https://cdn/fresh.mp4",
         "source": "weibo", "extra": {"duration": 180}},
    ]
    assert [x["key"] for x in FC.pick(items, state, 10)] == ["fresh"]


def test_skipped_duplicate_advances_part_without_joining_history(monkeypatch):
    monkeypatch.setattr(FC.time, "time", lambda: 123456)
    state = {"published": {}}
    dispatched = {"published_parts": 0}
    FC._record_skipped_part(
        state, dispatched, "new", {"name": "part-01.mp4"}, 2, 0,
        "重复主题", "与 BV-old 内容重复")

    assert dispatched["published_parts"] == 1
    assert state["published"]["new"]["parts"][0]["status"] == "skipped"
    assert list(FC.iter_published_parts(state)) == []


def _good_artifact_meta():
    return {
        "quality_gate_version": 3,
        "speaker": "林园",
        "visual_identity": {
            "speaker": "林园", "same_person_frames": [1, 2],
            "confidence": 0.91,
        },
        "resolution": {"width": 854, "height": 480, "short_edge": 480},
        "watermark_verified": True,
        "has_existing_subtitles": False,
        "subtitles_burned": True,
        "fingerprints": {
            "sha256": "a" * 64,
            "video_dhash": ["0" * 16] * 4,
            "audio_chromaprint": ["0" * 8] * 4,
            "transcript_ngrams": ["1" * 16] * 8,
        },
    }


def test_old_artifact_without_new_quality_proof_is_rejected():
    assert "旧成片" in FC.artifact_quality_error({"speaker": "林园"})
    assert FC.artifact_quality_error(_good_artifact_meta()) is None


@pytest.mark.parametrize(("field", "value", "message"), [
    ("resolution", {"short_edge": 479}, "短边 479"),
    ("watermark_verified", False, "角标复检"),
    ("fingerprints", {}, "指纹不完整"),
    ("has_existing_subtitles", True, "内嵌字幕"),
    ("subtitles_burned", False, "统一字幕"),
])
def test_artifact_quality_proof_fails_closed(field, value, message):
    meta = _good_artifact_meta()
    meta[field] = value
    assert message in FC.artifact_quality_error(meta)


def test_ocr_subtitle_band_detects_persistent_double_subtitle_risk(monkeypatch):
    cov = [0.0] * 100
    cov[73:78] = [0.75] * 5
    monkeypatch.setattr(P, "ocr_row_coverage", lambda *args, **kwargs: cov)
    assert P.has_existing_subtitles(Path("blue-band-white-text.mp4")) is True


def test_ocr_subtitle_band_ignores_sporadic_lower_screen_text(monkeypatch):
    cov = [0.0] * 100
    cov[73:78] = [0.25] * 5
    monkeypatch.setattr(P, "ocr_row_coverage", lambda *args, **kwargs: cov)

    class ClosedCapture:
        def isOpened(self):
            return False

    import cv2
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: ClosedCapture())
    assert P.has_existing_subtitles(Path("clean-interview.mp4")) is False


def test_source_gate_rejects_embedded_subtitles_before_identity(monkeypatch,
                                                                 tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"video")
    monkeypatch.setattr(P, "_file_sha256", lambda path: "source-hash")
    monkeypatch.setattr(P, "probe", lambda *args, **kwargs: "120")
    monkeypatch.setattr(P, "ensure_min_short_edge", lambda *args, **kwargs: (854, 480))
    monkeypatch.setattr(P, "has_existing_subtitles", lambda path: True)
    monkeypatch.setattr(
        P, "verify_source_identity",
        lambda *args, **kwargs: pytest.fail("双字幕素材不应再调用人物 VLM"))

    report = P.run_source_quality_gate(
        src, tmp_path / "work", "林园", "sk-test", tmp_path / "report.json")
    assert report["passed"] is False
    assert report["has_existing_subtitles"] is True
    assert "内嵌字幕" in report["reason"]


def test_source_gate_rejects_duration_before_visual_checks(monkeypatch, tmp_path):
    src = tmp_path / "short.mp4"
    src.write_bytes(b"video")
    monkeypatch.setattr(P, "_file_sha256", lambda path: "source-hash")
    monkeypatch.setattr(P, "probe", lambda *args, **kwargs: "43")
    monkeypatch.setattr(
        P, "ensure_min_short_edge",
        lambda *args, **kwargs: pytest.fail("短片应先被时长门禁淘汰"))
    report = P.run_source_quality_gate(src, tmp_path, "林园", "sk-test")
    assert report["passed"] is False
    assert "43s" in report["reason"]


def test_source_report_is_reused_only_for_the_same_media(monkeypatch, tmp_path):
    src = tmp_path / "source.mp4"
    src.write_bytes(b"video")
    report = tmp_path / "source_quality.json"
    report.write_text(json.dumps({
        "quality_gate_version": 3,
        "source_sha256": "right",
        "passed": True,
        "has_existing_subtitles": False,
        "visual_identity": {"speaker": "林园"},
        "resolution": {"width": 854, "height": 480, "short_edge": 480},
    }), encoding="utf-8")
    monkeypatch.setattr(P, "_file_sha256", lambda path: "right")
    assert P.load_source_quality_report(src, report)["passed"] is True
    monkeypatch.setattr(P, "_file_sha256", lambda path: "different")
    with pytest.raises(P.VisualQualityError, match="不匹配"):
        P.load_source_quality_report(src, report)


def test_workflow_runs_source_gate_before_asr_setup_and_uploads_rejection():
    workflow = (ROOT / ".github/workflows/linyuan-produce-cn.yml").read_text()
    assert workflow.index("name: 素材质量门禁") < workflow.index("name: 安装出片依赖")
    assert "--source-check-only" in workflow
    assert "source-reject-${{ inputs.slug }}" in workflow


def test_fc_consumes_source_rejection_artifact(monkeypatch):
    state = {"dispatched": [{
        "slug": "ly-bad", "key": "source:bad", "video_id": "bad",
    }], "rejected": []}

    def fake_gh(method, path, *args, **kwargs):
        if "/runs?status=completed" in path:
            return {"workflow_runs": [{"id": 11}]}
        if path == "/actions/runs/11/artifacts":
            return {"artifacts": [{
                "id": 22, "name": "source-reject-ly-bad",
                "archive_download_url": "https://example.test/reject.zip",
                "expired": False,
            }]}
        if method == "DELETE" and path == "/actions/artifacts/22":
            return {}
        raise AssertionError((method, path))

    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr("source_quality.json", json.dumps({
            "passed": False, "reason": "源视频含持续内嵌字幕",
        }))

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return payload.getvalue()

    monkeypatch.setattr(FC, "gh", fake_gh)
    monkeypatch.setattr(FC.urllib.request, "urlopen", lambda *args, **kwargs: Response())
    monkeypatch.setattr(FC, "log_event", lambda *args, **kwargs: None)
    assert FC._collect_source_rejections(state) == 1
    assert state["dispatched"][0]["failed"] is True
    assert "内嵌字幕" in state["rejected"][0]["error"]


def test_rejection_refills_slot_cleans_temp_and_aggregates_result(monkeypatch,
                                                                  tmp_path):
    monkeypatch.setattr(
        FC, "publish_handler",
        lambda event, context: {"published": 1, "skipped": 1})
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "large.mp4").write_bytes(b"video")
    result = FC._continue_after_rejection(
        {"_attempted_slugs": ["old"]}, None, "bad",
        {"published": 0, "quality_rejected": 1}, extracted)
    assert result == {"published": 1, "skipped": 1, "quality_rejected": 1}
    assert not extracted.exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_reencoded_video_keeps_composite_fingerprint(tmp_path):
    original = tmp_path / "original.mp4"
    encoded = tmp_path / "encoded.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc2=size=640x480:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(original)], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(original),
        "-c:v", "libx264", "-crf", "27", "-c:a", "aac", "-b:a", "96k",
        str(encoded)], check=True)
    a = P.build_content_fingerprints(original, "完全不同的测试文本甲")
    b = P.build_content_fingerprints(encoded, "完全不同的测试文本乙")
    assert "音频" in FC.fingerprint_duplicate(a, b)
