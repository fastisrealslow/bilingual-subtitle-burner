"""sf_transport 对 curl 进程本身失败的处理（全程打桩 subprocess，不发真请求）。

线上踩过的坑：``-w "\\n__SF_HTTP_%{http_code}__"`` 这个标记是 curl 写在 stdout
尾巴上的，连接被重置时它照样会写出来（http_code 为 000），于是「标记缺失」那条
守卫拦不住，函数返回了一个 ``status_code=0`` 的 Response，被上层判成致命错误，
一次网络抖动就把整个作业作废了。这里把「curl 退出码非零 → 抛异常」锁死。
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sf_transport                            # noqa: E402

URL = "https://api.siliconflow.cn/v1/chat/completions"


class _Completed:
    def __init__(self, returncode, stdout, stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def fake_curl(monkeypatch, returncode, stdout, stderr=""):
    monkeypatch.setattr(sf_transport.shutil, "which", lambda _: "/usr/bin/curl")
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: _Completed(returncode, stdout, stderr))


def marked(body, http_code):
    return f"{body}\n__SF_HTTP_{http_code}__"


def test_connection_reset_with_marker_raises_transport_error(monkeypatch):
    """复现线上故障：rc=56、标记存在、http_code=000。"""
    fake_curl(monkeypatch, 56, marked("", "000"),
              "curl: (56) Recv failure: Connection reset by peer")

    with pytest.raises(sf_transport.TransportError) as e:
        sf_transport.get(URL)

    assert not isinstance(e.value, sf_transport.TransportTimeout)
    assert "rc=56" in str(e.value)
    assert "Connection reset" in str(e.value)


def test_ssl_failure_with_marker_raises_transport_error(monkeypatch):
    fake_curl(monkeypatch, 35, marked("", "000"), "curl: (35) SSL connect error")

    with pytest.raises(sf_transport.TransportError):
        sf_transport.get(URL)


def test_curl_max_time_with_marker_raises_timeout(monkeypatch):
    fake_curl(monkeypatch, 28, marked("", "000"),
              "curl: (28) Operation timed out")

    with pytest.raises(sf_transport.TransportTimeout):
        sf_transport.get(URL)


def test_stderr_detail_is_truncated(monkeypatch):
    fake_curl(monkeypatch, 56, marked("", "000"), "x" * 5000)

    with pytest.raises(sf_transport.TransportError) as e:
        sf_transport.get(URL)

    assert len(str(e.value)) < 500


def test_real_http_error_status_is_still_returned(monkeypatch):
    """防过度修正：curl 正常退出时 500 仍是一个 Response，交给 sf_client 分类。"""
    fake_curl(monkeypatch, 0, marked('{"error":"boom"}', "500"))

    r = sf_transport.get(URL)

    assert r.status_code == 500
    assert r.text == '{"error":"boom"}'


@pytest.mark.parametrize("status", [200, 400, 401, 429, 502])
def test_successful_curl_returns_response_for_any_status(monkeypatch, status):
    fake_curl(monkeypatch, 0, marked("{}", status))
    assert sf_transport.get(URL).status_code == status


def test_missing_marker_still_raises(monkeypatch):
    """原有守卫不变：标记都没写出来时照旧抛异常。"""
    fake_curl(monkeypatch, 7, "", "curl: (7) Failed to connect")

    with pytest.raises(sf_transport.TransportError):
        sf_transport.get(URL)


def test_subprocess_timeout_still_raises_transport_timeout(monkeypatch):
    monkeypatch.setattr(sf_transport.shutil, "which", lambda _: "/usr/bin/curl")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", boom)

    with pytest.raises(sf_transport.TransportTimeout):
        sf_transport.get(URL)
