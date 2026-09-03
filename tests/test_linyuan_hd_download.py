"""高清取源和可恢复下载的回归测试。"""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BILI = _load("ci_fetch_bilibili_hd", ROOT / "linyuan/ci_fetch_bilibili.py")
FC = _load("fc_hd_download", ROOT / "linyuan/fc/index.py")


def test_bilibili_selects_highest_compatible_stream_up_to_1080p():
    info = {"data": {"quality": 80, "dash": {
        "video": [
            {"height": 2160, "codecid": 7, "bandwidth": 999, "baseUrl": "4k"},
            {"height": 1080, "codecid": 13, "bandwidth": 900, "baseUrl": "av1"},
            {"height": 1080, "codecid": 7, "bandwidth": 700,
             "baseUrl": "avc", "backupUrl": ["avc-backup"]},
            {"height": 720, "codecid": 7, "bandwidth": 800, "baseUrl": "720"},
        ],
        "audio": [
            {"bandwidth": 64, "baseUrl": "audio-low"},
            {"bandwidth": 192, "baseUrl": "audio-high"},
        ],
    }}}

    selected = BILI.select_streams(info)

    assert selected["height"] == 1080
    assert selected["video"] == ["avc", "avc-backup"]
    assert selected["audio"] == ["audio-high"]


def test_bilibili_falls_back_to_single_file_stream():
    selected = BILI.select_streams({"data": {
        "quality": 32,
        "durl": [{"url": "main", "backup_url": ["backup"]}],
    }})
    assert selected["video"] == ["main", "backup"]
    assert selected["audio"] == []


def test_fc_direct_download_is_resumable_and_retried(monkeypatch, tmp_path):
    dest = tmp_path / "source.mp4"
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        Path(cmd[cmd.index("-o") + 1]).write_bytes(b"x" * 12000)

    monkeypatch.setattr(FC.subprocess, "run", fake_run)
    FC._curl_download("https://cdn.example/video.mp4", dest,
                      "https://example.com/", "test-agent")

    assert dest.stat().st_size == 12000
    assert "--continue-at" in seen["cmd"]
    assert seen["cmd"][seen["cmd"].index("--continue-at") + 1] == "-"
    assert seen["cmd"][seen["cmd"].index("--retry") + 1] == "5"
    assert seen["cmd"][seen["cmd"].index("--max-time") + 1] == "900"
    assert seen["kwargs"]["timeout"] == 960


def test_netease_prefers_highest_hls_and_keeps_mp4_fallback(monkeypatch,
                                                            tmp_path):
    dest = tmp_path / "netease.mp4"
    calls = []

    def fake_hls(url, output, referer, user_agent):
        calls.append(("hls", url))
        Path(output).write_bytes(b"x" * 12000)

    monkeypatch.setattr(FC, "_hls_download", fake_hls)
    monkeypatch.setattr(
        FC, "_curl_download",
        lambda *args: calls.append(("mp4", args[0])))
    monkeypatch.setattr(FC, "mp4_duration", lambda path: 120)

    duration = FC._download_inner({
        "source": "netease_video",
        "video_url": "https://cdn.163.com/SD/video-mobile.mp4",
        "extra": {"m3u8_url": "https://cdn.163.com/master.m3u8"},
    }, dest)

    assert duration == 120
    assert calls == [("hls", "https://cdn.163.com/master.m3u8")]


def test_workflow_and_downloaders_request_hd_without_relaxing_duration():
    workflow = (ROOT / ".github/workflows/linyuan-produce-cn.yml").read_text()
    ci_fetch = (ROOT / "linyuan/ci_fetch_bilibili.py").read_text()
    local_fetch = (ROOT / "linyuan/fetch_bilibili.py").read_text()
    fc_source = (ROOT / "linyuan/fc/index.py").read_text()

    assert "qn=80" in ci_fetch and "fnval=4048" in ci_fetch
    assert "qn=80" in local_fetch and "fnval=4048" in local_fetch
    assert "qn=32" not in ci_fetch + local_fetch + fc_source
    assert "BILIBILI_COOKIES: ${{ secrets.BILIBILI_COOKIES }}" in workflow
    assert "--fragment-retries 10" in workflow
    assert "defn=shd" in fc_source
    assert FC.MIN_DUR == 90
