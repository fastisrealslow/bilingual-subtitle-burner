"""``scripts/sf_client``：内容寻址缓存 + 分类重试。

全部用 mock，不打真实 SiliconFlow。两条最重要的断言：命中缓存时传输层**一次都
不会被调用**（用「一被调用就抛异常」的替身锁死），以及 400/401/402/403 一次都
不重试。
"""

import email.utils
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
PAYLOAD = {"model": "Qwen/Qwen3-8B",
           "messages": [{"role": "user", "content": "挑金句"}]}
OK_JSON = {"choices": [{"message": {"content": "结果"}}]}
OK_BODY = json.dumps(OK_JSON, ensure_ascii=False)


def response(status, body="", headers=None):
    return sf_transport.Response(status, body, headers)


def always(status, body="", headers=None):
    """返回一个「记录每次调用」的传输层替身和它的调用记录。"""
    calls = []

    def fake(url, headers=None, json=None, data=None, timeout=120):
        calls.append(url)
        return response(status, body, headers=None)

    fake.calls = calls
    return fake


def refuse(*a, **k):
    raise AssertionError("命中缓存时不该发出任何请求")


@pytest.fixture(autouse=True)
def reset_client():
    """每个用例都从干净配置开始，跑完恢复成「未配置」的透传状态。"""
    sf_client.configure(cache_dir=None, cache_enabled=False)
    yield
    sf_client.configure(cache_dir=None, cache_enabled=False)


@pytest.fixture
def slept(monkeypatch):
    """把退避的 sleep 打桩：测试里不真睡，只记账。抖动固定成 1.0 便于断言。"""
    rec = []
    monkeypatch.setattr(sf_client, "_sleep", rec.append)
    monkeypatch.setattr(sf_client, "_jitter", lambda: 1.0)
    return rec


# ── 缓存 ──────────────────────────────────────────────────────────────────────

def test_cache_hit_never_touches_the_transport(tmp_path, monkeypatch, slept):
    sf_client.configure(cache_dir=tmp_path)
    fake = always(200, OK_BODY)
    monkeypatch.setattr(sf_transport, "post", fake)

    first = sf_client.post(URL, json=PAYLOAD)
    assert len(fake.calls) == 1

    # 传输层换成「一被调用就炸」，第二次必须完全靠缓存
    monkeypatch.setattr(sf_transport, "post", refuse)
    second = sf_client.post(URL, json=PAYLOAD)

    assert second.json() == first.json() == OK_JSON
    stats = sf_client.cache_stats()
    assert (stats["hits"], stats["misses"], stats["stores"]) == (1, 1, 1)


def test_failed_calls_are_never_cached(tmp_path, monkeypatch, slept):
    """一次 500 要是被写进缓存，就等于把这条请求永久钉死在失败上。"""
    sf_client.configure(cache_dir=tmp_path, max_retries=1)
    monkeypatch.setattr(sf_transport, "post", always(500, "boom"))

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)

    assert list(tmp_path.rglob("*.json")) == []
    assert sf_client.cache_stats()["stores"] == 0

    # 失败没留下痕迹，恢复之后照样能正常写入
    monkeypatch.setattr(sf_transport, "post", always(200, OK_BODY))
    assert sf_client.post(URL, json=PAYLOAD).json() == OK_JSON
    assert len(list(tmp_path.rglob("*.json"))) == 1


def test_fatal_calls_are_never_cached(tmp_path, monkeypatch, slept):
    sf_client.configure(cache_dir=tmp_path)
    monkeypatch.setattr(sf_transport, "post", always(401, "bad key"))

    with pytest.raises(sf_client.SFFatalError):
        sf_client.post(URL, json=PAYLOAD)

    assert list(tmp_path.rglob("*.json")) == []


@pytest.mark.parametrize("changed", [
    {"model": "Qwen/Qwen3-14B"},
    {"messages": [{"role": "user", "content": "换了个 prompt"}]},
    {"temperature": 0.9},
])
def test_cache_key_is_sensitive_to_the_request_body(tmp_path, monkeypatch,
                                                    slept, changed):
    sf_client.configure(cache_dir=tmp_path)
    fake = always(200, OK_BODY)
    monkeypatch.setattr(sf_transport, "post", fake)

    sf_client.post(URL, json=PAYLOAD)
    sf_client.post(URL, json={**PAYLOAD, **changed})

    assert len(fake.calls) == 2
    assert sf_client.cache_stats()["hits"] == 0


def test_cache_key_ignores_dict_ordering(tmp_path, monkeypatch, slept):
    """键只取决于请求的语义内容，不该被 dict 的书写顺序影响。"""
    sf_client.configure(cache_dir=tmp_path)
    a = {"model": "m", "messages": [{"role": "user", "content": "x"}],
         "temperature": 0.3}
    b = {"temperature": 0.3, "messages": [{"role": "user", "content": "x"}],
         "model": "m"}
    assert sf_client.cache_key("m", URL, a) == sf_client.cache_key("m", URL, b)


def test_endpoint_is_part_of_the_key(tmp_path):
    assert (sf_client.cache_key("m", URL, PAYLOAD)
            != sf_client.cache_key("m", "https://other/v1/chat", PAYLOAD))


def test_no_llm_cache_disables_both_read_and_write(tmp_path, monkeypatch, slept):
    sf_client.configure(cache_dir=tmp_path, cache_enabled=False)
    fake = always(200, OK_BODY)
    monkeypatch.setattr(sf_transport, "post", fake)

    sf_client.post(URL, json=PAYLOAD)
    sf_client.post(URL, json=PAYLOAD)

    assert len(fake.calls) == 2
    assert list(tmp_path.rglob("*.json")) == []
    assert sf_client.cache_stats()["hits"] == 0


def test_cache_record_carries_written_at_and_model(tmp_path, monkeypatch, slept):
    sf_client.configure(cache_dir=tmp_path)
    monkeypatch.setattr(sf_transport, "post", always(200, OK_BODY))
    sf_client.post(URL, json=PAYLOAD)

    record = json.loads(next(tmp_path.rglob("*.json")).read_text(encoding="utf-8"))
    assert record["model"] == "Qwen/Qwen3-8B"
    assert record["endpoint"] == URL
    assert record["response"] == OK_JSON
    datetime.fromisoformat(record["written_at"])       # 能解析就行


def test_bytes_body_is_keyed_like_the_equivalent_dict(tmp_path):
    """封面 VLM 传的是 dump 好的 bytes，键必须和等价的 dict 一致。"""
    raw = json.dumps(PAYLOAD, ensure_ascii=False).encode("utf-8")
    assert (sf_client._body_object(None, raw)
            == sf_client._body_object(PAYLOAD, None))


def test_corrupt_cache_file_falls_back_to_a_real_call(tmp_path, monkeypatch,
                                                      slept):
    sf_client.configure(cache_dir=tmp_path)
    fake = always(200, OK_BODY)
    monkeypatch.setattr(sf_transport, "post", fake)
    sf_client.post(URL, json=PAYLOAD)

    next(tmp_path.rglob("*.json")).write_text("{ 截断的", encoding="utf-8")
    assert sf_client.post(URL, json=PAYLOAD).json() == OK_JSON
    assert len(fake.calls) == 2


# ── 错误分类：不可重试 ────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [400, 401, 402, 403])
def test_fatal_status_is_never_retried(status, monkeypatch, slept):
    """400/401/402/403 重试纯属浪费时间，还会把真实原因埋进重试日志里。"""
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=5)
    fake = always(status, "nope")
    monkeypatch.setattr(sf_transport, "post", fake)

    with pytest.raises(sf_client.SFFatalError) as e:
        sf_client.post(URL, json=PAYLOAD)

    assert len(fake.calls) == 1, f"HTTP {status} 一次都不该重试"
    assert slept == []
    assert e.value.http_status == status
    assert e.value.reason == sf_client.FATAL_REASONS[status]


def test_fatal_error_carries_the_fields_the_json_needs(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False)
    monkeypatch.setattr(sf_transport, "post", always(402, "余额不足"))

    with pytest.raises(sf_client.SFFatalError) as e:
        sf_client.post(URL, json=PAYLOAD, stage="translate")

    fields = e.value.fields()
    assert fields["http_status"] == 402
    assert fields["reason"] == "insufficient_balance"
    assert e.value.stage == "translate"


def test_unknown_4xx_is_treated_as_fatal(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=3)
    fake = always(418, "teapot")
    monkeypatch.setattr(sf_transport, "post", fake)

    with pytest.raises(sf_client.SFFatalError):
        sf_client.post(URL, json=PAYLOAD)
    assert len(fake.calls) == 1


# ── 错误分类：可重试 ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_retryable_status_is_retried(status, monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=3)
    fake = always(status, "busy")
    monkeypatch.setattr(sf_transport, "post", fake)

    with pytest.raises(sf_client.SFRetryExhausted) as e:
        sf_client.post(URL, json=PAYLOAD)

    assert len(fake.calls) == 4, f"HTTP {status} 应重试 3 次（共 4 次调用）"
    assert len(slept) == 3
    assert e.value.http_status == status


def test_retry_gives_up_and_returns_the_success(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=3)
    seq = [response(503, "busy"), response(503, "busy"), response(200, OK_BODY)]
    monkeypatch.setattr(sf_transport, "post",
                        lambda *a, **k: seq.pop(0))

    assert sf_client.post(URL, json=PAYLOAD).json() == OK_JSON
    assert len(slept) == 2


def test_empty_or_invalid_json_body_is_retried(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=2)
    fake = always(200, "")
    monkeypatch.setattr(sf_transport, "post", fake)

    with pytest.raises(sf_client.SFRetryExhausted) as e:
        sf_client.post(URL, json=PAYLOAD)

    assert len(fake.calls) == 3
    assert e.value.reason == "invalid_json"


def test_connection_failure_is_retried(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=3)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise sf_transport.TransportError("curl rc=7: connection refused")

    monkeypatch.setattr(sf_transport, "post", boom)
    with pytest.raises(sf_client.SFRetryExhausted) as e:
        sf_client.post(URL, json=PAYLOAD)

    assert len(calls) == 4
    assert e.value.reason == "connect_failed"


def test_zero_max_retries_means_a_single_attempt(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=0)
    fake = always(503, "busy")
    monkeypatch.setattr(sf_transport, "post", fake)

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)
    assert len(fake.calls) == 1
    assert slept == []


# ── 超时：保守重试 ────────────────────────────────────────────────────────────

def test_timeout_retries_are_capped_conservatively(monkeypatch, slept, capsys):
    """客户端超时时服务端可能已经处理并计费了，不能按普通错误反复重试。"""
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=5)
    calls = []

    def timeout(*a, **k):
        calls.append(1)
        raise sf_transport.TransportTimeout("curl 超时（120s）")

    monkeypatch.setattr(sf_transport, "post", timeout)
    with pytest.raises(sf_client.SFRetryExhausted) as e:
        sf_client.post(URL, json=PAYLOAD)

    assert len(calls) == sf_client.MAX_TIMEOUT_ATTEMPTS == 2
    assert e.value.reason == "timeout"
    assert "计费" in capsys.readouterr().err


# ── Retry-After ───────────────────────────────────────────────────────────────

def test_retry_after_seconds_is_honoured(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=1)
    monkeypatch.setattr(
        sf_transport, "post",
        lambda *a, **k: response(429, "slow down", {"Retry-After": "7"}))

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)
    assert slept == [7.0]


def test_retry_after_http_date_is_honoured(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=1)
    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(sf_client, "_now", lambda: now)
    when = email.utils.format_datetime(now + timedelta(seconds=30))
    monkeypatch.setattr(
        sf_transport, "post",
        lambda *a, **k: response(429, "slow down", {"Retry-After": when}))

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)
    assert slept == [30.0]


def test_retry_after_is_case_insensitive(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=1)
    monkeypatch.setattr(
        sf_transport, "post",
        lambda *a, **k: response(429, "", {"retry-after": "5"}))

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)
    assert slept == [5.0]


def test_missing_retry_after_falls_back_to_backoff(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=2)
    monkeypatch.setattr(sf_transport, "post",
                        lambda *a, **k: response(429, "slow down"))

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)
    assert slept == [1.0, 2.0]          # 指数退避，抖动被打桩成 1.0


@pytest.mark.parametrize("raw,expected", [
    ("12", 12.0),
    ("0", 0.0),
    ("2.5", 2.5),
    ("  9  ", 9.0),
    ("", None),
    (None, None),
    ("不是数字也不是日期", None),
])
def test_parse_retry_after_formats(raw, expected):
    assert sf_client.parse_retry_after(raw) == expected


def test_parse_retry_after_past_date_is_zero():
    now = datetime(2026, 7, 27, 12, 0, 0, tzinfo=timezone.utc)
    past = email.utils.format_datetime(now - timedelta(seconds=60))
    assert sf_client.parse_retry_after(past, now=now) == 0.0


# ── 退避预算 ──────────────────────────────────────────────────────────────────

def test_total_backoff_is_capped(monkeypatch, slept):
    """没有总上限，一个 Retry-After: 3600 就能把整个 job 挂死。"""
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=50)
    monkeypatch.setattr(sf_transport, "post",
                        lambda *a, **k: response(503, "busy"))

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)

    assert sum(slept) <= sf_client.MAX_TOTAL_BACKOFF_SEC


def test_absurd_retry_after_cannot_exceed_the_budget(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=5)
    monkeypatch.setattr(
        sf_transport, "post",
        lambda *a, **k: response(429, "", {"Retry-After": "3600"}))

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)

    assert sum(slept) <= sf_client.MAX_TOTAL_BACKOFF_SEC
    assert slept[0] == sf_client.MAX_TOTAL_BACKOFF_SEC


def test_single_backoff_is_capped(monkeypatch, slept):
    sf_client.configure(cache_dir=None, cache_enabled=False, max_retries=10)
    monkeypatch.setattr(sf_transport, "post",
                        lambda *a, **k: response(500, "boom"))

    with pytest.raises(sf_client.SFRetryExhausted):
        sf_client.post(URL, json=PAYLOAD)
    assert max(slept) <= sf_client.MAX_SINGLE_BACKOFF_SEC


# ── 三处调用点都走这一层 ──────────────────────────────────────────────────────

def test_all_three_siliconflow_call_sites_share_the_cache(tmp_path, monkeypatch,
                                                          slept):
    """highlight、translate、封面 VLM 三处都必须经过 sf_client。"""
    import highlight as HL
    import step7_cover as COVER
    import translate as TR

    sf_client.configure(cache_dir=tmp_path)
    vlm_body = json.dumps(
        {"choices": [{"message": {"content":
                                  '[{"frame":1,"person":"主讲人","cover_score":8}]'}}],
         "usage": {"prompt_tokens": 10}})
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg bytes")

    calls = []

    def fake(url, headers=None, json=None, data=None, timeout=120):
        calls.append(url)
        body = vlm_body if data is not None else OK_BODY
        return response(200, body)

    monkeypatch.setattr(sf_transport, "post", fake)

    def run_all():
        HL.call_llm([{"role": "user", "content": "挑金句"}],
                    "sk-test", "Qwen/Qwen3-8B", "https://x/v1")
        TR.chat([{"role": "user", "content": "翻译"}],
                "sk-test", "deepseek-ai/DeepSeek-V3", "https://x/v1")
        COVER.call_vision_llm("sk-test", "Qwen/Qwen3-VL-8B-Instruct",
                              [str(frame)], "芒格")

    run_all()
    assert len(calls) == 3
    assert sf_client.cache_stats()["stores"] == 3

    # 第二轮全部命中，传输层一次都不该再被碰
    monkeypatch.setattr(sf_transport, "post", refuse)
    run_all()
    assert sf_client.cache_stats()["hits"] == 3
