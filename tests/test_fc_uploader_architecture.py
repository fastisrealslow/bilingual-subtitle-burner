"""阿里 FC 投稿器的成本与幂等架构测试；全部离线，不会真实投稿。"""

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "fc_uploader_architecture", ROOT / "linyuan/fc/index.py")
FC = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FC)


def _utc_timestamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def test_regular_publish_windows_are_beijing_hours():
    # 01:15 UTC = 09:15 北京；02:15 UTC = 10:15 北京。
    assert FC.is_regular_publish_hour(_utc_timestamp("2026-09-05T01:15:00"))
    assert not FC.is_regular_publish_hour(_utc_timestamp("2026-09-05T02:15:00"))


def test_recent_upload_lease_blocks_competing_invocation():
    now = _utc_timestamp("2026-09-05T01:15:00")
    candidate = {"uploading": True, "uploading_ts": now - 30}
    assert FC.has_active_upload_lease(candidate, now)
    candidate["uploading_ts"] = now - FC.UPLOAD_LEASE_SECONDS - 1
    assert not FC.has_active_upload_lease(candidate, now)


def test_delivery_release_url_is_direct_and_predictable():
    asset = FC.delivery_release_asset("ly-test.final_2.mp4")
    assert asset["browser_download_url"].endswith(
        "/releases/download/deliver/ly-test.final_2.mp4")


def test_download_release_part_fetches_only_selected_video(monkeypatch, tmp_path):
    slug = "ly-test"
    downloaded = []

    def fake_download(asset, dest, max_time=1620):
        dest = Path(dest)
        downloaded.append(dest.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.name == "meta.json":
            dest.write_text(json.dumps([
                {"final": "final_1.mp4", "cover": "cover_1.jpg"},
                {"final": "final_2.mp4", "cover": "cover_2.jpg"},
            ]), encoding="utf-8")
        else:
            dest.write_bytes(b"media")
        return True

    monkeypatch.setattr(FC, "download_release_asset", fake_download)
    assert FC.download_release_part(slug, 1, tmp_path)
    assert downloaded == ["meta.json", "cover_2.jpg", "final_2.mp4"]
    assert not (tmp_path / "final_1.mp4").exists()


def test_workflows_publish_one_part_and_release_covers():
    batch = (ROOT / ".github/workflows/linyuan-publish-batch.yml").read_text()
    produce = (ROOT / ".github/workflows/linyuan-produce-cn.yml").read_text()
    assert '"batch_remaining": 1' in batch
    assert "take = min(3, remaining)" not in batch
    assert "为旧批次补齐逐条封面" in batch
    assert "config.read_timeout = 1800000" in batch
    assert "for f in cover*.jpg" in produce
    assert 'cp "$f" "${{ inputs.slug }}.$f"' in produce
