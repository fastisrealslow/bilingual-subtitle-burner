"""sf_client 的重试分类与退避（全程 mock，sleep 打桩，不真睡也不打真实接口）。

重点是**分类**：400/401/402/403 一次都不该重试，重试它们只是把真实原因拖到
超时之后才暴露。
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sf_client                              # noqa: E402
import sf_transport                           # noqa: E402

URL = "https://api.siliconflow.cn/v1/chat/completions"
BODY = {"model": "Qwen/Qwen3-8B", "messages": [{"role": "user", "content": "hi"}]}
PAYLOAD = {"choices": [{"message": {"content": "ok"}}]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    """缓存指向空目录、sleep 打桩，返回记录到的每次退避秒数。"""
    slept = []
    monkeypatch.setattr(sf_client, "_sleep", slept.append)
    sf_client.configure(cache_dir=tmp_path / "cache", cache_enabled=True,
                        max_retries=3)
    yield slept
    sf_client.configure(cache_dir=ROOT / sf_client.DEFAULT_CACHE_DIRNAME,
                        cache_enabled=True,
                        max_retries=sf_client.DEFAULT_MAX_RETRIES)


def transport(monkeypatch, *responses):
    calls = []

    def fake_post(url, headers=None, json=None, data=None, files=None,
                  timeout=120):
        calls.append(url)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(sf_transport, "post", fake_post)
    return calls


def resp(status, payload=None, headers=None):
    return sf_transport.Response(status, json.dumps(payload or {}), headers)


# ── 不可重试：逐个状态码锁死 ─────────────────────────────────────────────────

@pytest.mark.parametrize("status,reason", [
    (400, "bad_request"),
    (401, "unauthorized"),
    (402, "insufficient_balance"),
    (403, "forbidden"),
])
def test_fatal_status_is_never_retried(client, monkeypatch, status, reason):
    calls = transport(monkeypatch, resp(status, {"error": "nope"}))

    with pytest.raises(sf_client.FatalHTTPError) as e:
        sf_client.post(URL, json=BODY)

    assert len(calls) == 1, f"HTTP {status} 被重试了 {len(calls)} 次"
    assert client == [], "不可重试的失败不该产生任何退避"
    assert e.value.status_code == status
    assert e.value.reason == reason
    assert e.value.exit_code == 1


def test_unexpected_status_is_not_retried_either(client, monkeypatch):
    calls = transport(monkeypatch, resp(404, {"error": "no such model"}))

    with pytest.raises(sf_client.FatalHTTPError) as e:
        sf_client.post(URL, json=BODY)

    assert len(calls) == 1
    assert e.value.reason == "unexpected_http_status"
    assert e.value.exit_code == 3


# ── 可重试：逐个状态码锁死 ───────────────────────────────────────────────────

@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_status_is_retried_until_success(client, monkeypatch, status):
    calls = transport(monkeypatch, resp(status, {}), resp(200, PAYLOAD))

    assert sf_client.post(URL, json=BODY).json() == PAYLOAD
    assert len(calls) == 2
    assert len(client) == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_status_gives_up_at_the_limit(client, monkeypatch, status):
    calls = transport(monkeypatch, resp(status, {}))

    with pytest.raises(sf_client.RetriesExhausted) as e:
        sf_client.post(URL, json=BODY)

    assert len(calls) == 3, "尝试次数应等于 max_retries"
    assert e.value.status_code == status


def test_connection_failure_is_retried(client, monkeypatch):
    calls = transport(monkeypatch, sf_transport.TransportError("connection reset"),
                      resp(200, PAYLOAD))
    assert sf_client.post(URL, json=BODY).json() == PAYLOAD
    assert len(calls) == 2


def test_empty_body_is_retried(client, monkeypatch):
    calls = transport(monkeypatch, sf_transport.Response(200, ""),
                      resp(200, PAYLOAD))
    assert sf_client.post(URL, json=BODY).json() == PAYLOAD
    assert len(calls) == 2


def test_non_json_body_is_retried(client, monkeypatch):
    calls = transport(monkeypatch, sf_transport.Response(200, "<html>502</html>"),
                      resp(200, PAYLOAD))
    assert sf_client.post(URL, json=BODY).json() == PAYLOAD
    assert len(calls) == 2


# ── 超时更保守 ───────────────────────────────────────────────────────────────

def test_timeout_uses_a_tighter_attempt_cap(monkeypatch, tmp_path, capsys):
    """客户端超时时服务端可能已处理并计费，重试次数要比普通失败更少。"""
    monkeypatch.setattr(sf_client, "_sleep", lambda s: None)
    sf_client.configure(cache_dir=tmp_path / "c", cache_enabled=True,
                        max_retries=6)
    try:
        calls = transport(monkeypatch, sf_transport.TransportTimeout("curl 超时（120s）"))
        with pytest.raises(sf_client.RetriesExhausted):
            sf_client.post(URL, json=BODY)
    finally:
        sf_client.configure(max_retries=sf_client.DEFAULT_MAX_RETRIES)

    assert len(calls) == sf_client.TIMEOUT_MAX_ATTEMPTS
    assert "服务端可能已处理并计费" in capsys.readouterr().err


# ── Retry-After ──────────────────────────────────────────────────────────────

def test_retry_after_seconds_format(client, monkeypatch):
    transport(monkeypatch, resp(429, {}, {"Retry-After": "7"}), resp(200, PAYLOAD))
    sf_client.post(URL, json=BODY)
    assert client == [7.0]


def test_retry_after_is_case_insensitive(client, monkeypatch):
    transport(monkeypatch, resp(429, {}, {"retry-after": "3"}), resp(200, PAYLOAD))
    sf_client.post(URL, json=BODY)
    assert client == [3.0]


def test_retry_after_http_date_format(client, monkeypatch):
    when = datetime.now(timezone.utc) + timedelta(seconds=12)
    stamp = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
    transport(monkeypatch, resp(429, {}, {"Retry-After": stamp}), resp(200, PAYLOAD))

    sf_client.post(URL, json=BODY)

    assert len(client) == 1
    assert 9 <= client[0] <= 13


def test_retry_after_absent_falls_back_to_backoff(client, monkeypatch):
    transport(monkeypatch, resp(429, {}), resp(200, PAYLOAD))
    sf_client.post(URL, json=BODY)
    assert len(client) == 1 and client[0] > 0


def test_retry_after_in_the_past_does_not_go_negative():
    stamp = (datetime.now(timezone.utc) - timedelta(hours=1)) \
        .strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert sf_client.parse_retry_after(stamp) == 0.0


@pytest.mark.parametrize("raw", [None, "", "soon", "not-a-date"])
def test_unparseable_retry_after_is_ignored(raw):
    assert sf_client.parse_retry_after(raw) is None


# ── 退避总时长有上限 ─────────────────────────────────────────────────────────

def test_total_backoff_is_bounded(monkeypatch, tmp_path):
    slept = []
    monkeypatch.setattr(sf_client, "_sleep", slept.append)
    sf_client.configure(cache_dir=tmp_path / "c", cache_enabled=True,
                        max_retries=50)
    try:
        transport(monkeypatch, resp(503, {}))
        with pytest.raises(sf_client.RetriesExhausted):
            sf_client.post(URL, json=BODY)
    finally:
        sf_client.configure(max_retries=sf_client.DEFAULT_MAX_RETRIES)

    assert sum(slept) == pytest.approx(sf_client.MAX_TOTAL_BACKOFF_SEC, abs=1e-6)
    assert max(slept) <= sf_client.BACKOFF_CAP_SEC
    assert len(slept) < 50, "退避预算耗尽后不该继续重试"


def test_retry_after_is_also_capped(monkeypatch, tmp_path):
    slept = []
    monkeypatch.setattr(sf_client, "_sleep", slept.append)
    sf_client.configure(cache_dir=tmp_path / "c", cache_enabled=True,
                        max_retries=2)
    try:
        transport(monkeypatch, resp(429, {}, {"Retry-After": "86400"}))
        with pytest.raises(sf_client.RetriesExhausted):
            sf_client.post(URL, json=BODY)
    finally:
        sf_client.configure(max_retries=sf_client.DEFAULT_MAX_RETRIES)

    assert slept == [sf_client.BACKOFF_CAP_SEC]


def test_backoff_grows_and_stays_under_the_per_attempt_cap():
    assert sf_client.backoff_delay(0) < sf_client.backoff_delay(6)
    assert all(sf_client.backoff_delay(n) <= sf_client.BACKOFF_CAP_SEC * 1.25
               for n in range(12))


# ── 响应头解析 ───────────────────────────────────────────────────────────────

def test_transport_parses_retry_after_header():
    dump = ("HTTP/2 429\r\nContent-Type: application/json\r\n"
            "Retry-After: 42\r\n\r\n")
    headers = sf_transport.parse_header_block(dump)
    assert headers.get("retry-after") == "42"
    assert headers.get("Retry-After") == "42"


def test_transport_keeps_only_the_last_header_block():
    dump = ("HTTP/1.1 301 Moved\r\nRetry-After: 1\r\n\r\n"
            "HTTP/2 200\r\nContent-Type: application/json\r\n\r\n")
    headers = sf_transport.parse_header_block(dump)
    assert headers.get("Retry-After") is None
    assert headers.get("content-type") == "application/json"
