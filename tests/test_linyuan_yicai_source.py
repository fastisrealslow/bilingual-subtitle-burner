"""第一财经官方访谈源解析、去噪和签名刷新。"""

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MONITOR = _load("monitor_yicai_test", ROOT / "linyuan/monitor_v2.py")
FC = _load("fc_yicai_test", ROOT / "linyuan/fc/index.py")

PAGE = """
<html><head><title>专访林园：科技机会和科技股机会是两回事|第一财经</title></head>
<body>2026-08-22 10:51:47
https://ycalvod.yicai.com/vms-new/interview.mp4?auth_key=abc&amp;ycfrom=yicaiapp
</body></html>
"""


def test_yicai_seed_yields_refreshable_official_video(monkeypatch):
    monkeypatch.setattr(MONITOR, "http_get", lambda *args, **kwargs: PAGE)
    monkeypatch.setattr(MONITOR.time, "sleep", lambda _: None)
    source = MONITOR.YicaiVideoSource(
        {"ids": ["103329354"], "keyword": "林园"}, {})

    items = source.fetch(None)

    assert len(items) == 1
    assert items[0]["author"] == "第一财经"
    extra = json.loads(items[0]["extra"])
    assert extra["mp4_url"].endswith("auth_key=abc&ycfrom=yicaiapp")
    assert items[0]["publish_time"] == "2026-08-22T10:51:47"


def test_fc_refreshes_expired_yicai_signature(monkeypatch, tmp_path):
    candidate = {
        "source": "yicai_video", "video_url": "https://old.yicai.com/a.mp4",
        "page_url": "https://www.yicai.com/video/103329354.html", "extra": {},
    }
    calls = []

    def fake_download(url, dest, referer, user_agent=None):
        calls.append((url, referer))
        if "old.yicai" in url:
            raise RuntimeError("403")
        Path(dest).write_bytes(b"x" * 12000)

    monkeypatch.setattr(FC, "_curl_download", fake_download)
    monkeypatch.setattr(FC, "yicai_refresh_url", lambda _: "https://new.yicai.com/b.mp4")
    monkeypatch.setattr(FC, "mp4_duration", lambda _: 123)

    assert FC._download_inner(candidate, tmp_path / "out.mp4") == 123
    assert calls == [
        ("https://old.yicai.com/a.mp4", "https://www.yicai.com/"),
        ("https://new.yicai.com/b.mp4", "https://www.yicai.com/"),
    ]
