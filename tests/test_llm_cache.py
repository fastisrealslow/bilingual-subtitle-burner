"""sf_client 的内容寻址磁盘缓存（全程 mock，不打真实 SiliconFlow）。

命中缓存的定义是**一个请求都不发**，所以这里的 transport 桩一旦被调用就抛
AssertionError —— 用返回值计数是量不出「真的没发」的。
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import sf_client                              # noqa: E402
import sf_transport                           # noqa: E402

URL = "https://api.siliconflow.cn/v1/chat/completions"
BODY = {"model": "Qwen/Qwen3-8B",
        "messages": [{"role": "user", "content": "你好"}],
        "temperature": 0.3}
PAYLOAD = {"choices": [{"message": {"content": "回复"}}]}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(sf_client, "_sleep", lambda s: None)
    sf_client.configure(cache_dir=tmp_path / "cache", cache_enabled=True,
                        max_retries=3)
    yield tmp_path / "cache"
    sf_client.configure(cache_dir=ROOT / sf_client.DEFAULT_CACHE_DIRNAME,
                        cache_enabled=True,
                        max_retries=sf_client.DEFAULT_MAX_RETRIES)


def ok(payload=PAYLOAD, status=200, headers=None):
    return sf_transport.Response(status, json.dumps(payload), headers)


def counting_transport(monkeypatch, responses):
    """按顺序吐出预设响应，并记录实际发出的请求数。"""
    calls = []

    def fake_post(url, headers=None, json=None, data=None, files=None,
                  timeout=120):
        calls.append({"url": url, "json": json, "data": data})
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(sf_transport, "post", fake_post)
    return calls


def exploding_transport(monkeypatch):
    """一被调用就炸 —— 用来锁死「命中缓存不发请求」。"""
    def boom(*a, **k):
        raise AssertionError("命中缓存后仍然发起了 HTTP 请求")

    monkeypatch.setattr(sf_transport, "post", boom)


# ── 命中 / 未命中 ────────────────────────────────────────────────────────────

def test_hit_does_not_issue_any_request(cache, monkeypatch):
    calls = counting_transport(monkeypatch, [ok()])
    first = sf_client.post(URL, json=BODY)
    assert first.json() == PAYLOAD
    assert len(calls) == 1

    exploding_transport(monkeypatch)
    second = sf_client.post(URL, json=BODY)

    assert second.json() == PAYLOAD
    assert sf_client.cache_stats()["hits"] == 1
    assert sf_client.cache_stats()["misses"] == 1


def test_key_is_sensitive_to_request_body(cache, monkeypatch):
    counting_transport(monkeypatch, [ok()])
    sf_client.post(URL, json=BODY)

    changed = dict(BODY, messages=[{"role": "user", "content": "你好吗"}])
    calls = counting_transport(monkeypatch, [ok({"choices": [{"message": {"content": "另一个"}}]})])
    resp = sf_client.post(URL, json=changed)

    assert len(calls) == 1, "请求体变了却命中了缓存"
    assert resp.json()["choices"][0]["message"]["content"] == "另一个"


def test_key_is_stable_under_key_reordering(cache, monkeypatch):
    counting_transport(monkeypatch, [ok()])
    sf_client.post(URL, json=BODY)

    exploding_transport(monkeypatch)
    reordered = {"temperature": 0.3, "messages": BODY["messages"],
                 "model": BODY["model"]}
    assert sf_client.post(URL, json=reordered).json() == PAYLOAD


def test_key_is_sensitive_to_model_and_endpoint(cache, monkeypatch):
    counting_transport(monkeypatch, [ok()])
    sf_client.post(URL, json=BODY)

    calls = counting_transport(monkeypatch, [ok()])
    sf_client.post(URL, json=dict(BODY, model="deepseek-ai/DeepSeek-V3"))
    assert len(calls) == 1

    calls = counting_transport(monkeypatch, [ok()])
    sf_client.post("https://other.example/v1/chat/completions", json=BODY)
    assert len(calls) == 1


def test_data_bytes_payload_is_cached_too(cache, monkeypatch):
    """封面 VLM 走的是 ``data=`` 原始字节，同样要进缓存。"""
    raw = json.dumps(BODY).encode("utf-8")
    calls = counting_transport(monkeypatch, [ok()])
    sf_client.post(URL, data=raw)
    assert len(calls) == 1

    exploding_transport(monkeypatch)
    assert sf_client.post(URL, data=raw).json() == PAYLOAD


# ── 失败绝不写缓存 ───────────────────────────────────────────────────────────

def test_failed_response_is_not_cached(cache, monkeypatch):
    counting_transport(monkeypatch, [ok(status=500, payload={"error": "boom"})])
    with pytest.raises(sf_client.RetriesExhausted):
        sf_client.post(URL, json=BODY)

    assert list(cache.rglob("*.json")) == []

    calls = counting_transport(monkeypatch, [ok()])
    assert sf_client.post(URL, json=BODY).json() == PAYLOAD
    assert len(calls) == 1, "上一次的失败被缓存了"


def test_fatal_response_is_not_cached(cache, monkeypatch):
    counting_transport(monkeypatch, [ok(status=401, payload={"error": "bad key"})])
    with pytest.raises(sf_client.FatalHTTPError):
        sf_client.post(URL, json=BODY)
    assert list(cache.rglob("*.json")) == []


def test_unparseable_success_is_not_cached(cache, monkeypatch):
    counting_transport(monkeypatch, [sf_transport.Response(200, "not json")])
    with pytest.raises(sf_client.RetriesExhausted):
        sf_client.post(URL, json=BODY)
    assert list(cache.rglob("*.json")) == []


def test_retry_then_success_is_cached(cache, monkeypatch):
    counting_transport(monkeypatch, [ok(status=503, payload={}), ok()])
    assert sf_client.post(URL, json=BODY).json() == PAYLOAD

    exploding_transport(monkeypatch)
    assert sf_client.post(URL, json=BODY).json() == PAYLOAD


# ── 缓存文件内容 ─────────────────────────────────────────────────────────────

def test_cache_file_records_written_at_and_model(cache, monkeypatch):
    counting_transport(monkeypatch, [ok()])
    sf_client.post(URL, json=BODY)

    files = list(cache.rglob("*.json"))
    assert len(files) == 1
    envelope = json.loads(files[0].read_text(encoding="utf-8"))

    assert envelope["model"] == "Qwen/Qwen3-8B"
    assert envelope["endpoint"] == URL
    assert envelope["response"] == PAYLOAD
    assert envelope["written_at"].startswith("20")
    assert files[0].stem == envelope["key"]


# ── 开关 ─────────────────────────────────────────────────────────────────────

def test_no_cache_flag_always_issues_the_request(cache, monkeypatch):
    sf_client.configure(cache_enabled=False)
    calls = counting_transport(monkeypatch, [ok(), ok()])

    sf_client.post(URL, json=BODY)
    sf_client.post(URL, json=BODY)

    assert len(calls) == 2
    assert list(cache.rglob("*.json")) == []
    assert sf_client.cache_stats()["hits"] == 0


def test_cache_dir_is_configurable(tmp_path, monkeypatch, cache):
    elsewhere = tmp_path / "somewhere-else"
    sf_client.configure(cache_dir=elsewhere)
    counting_transport(monkeypatch, [ok()])

    sf_client.post(URL, json=BODY)

    assert len(list(elsewhere.rglob("*.json"))) == 1


def test_multipart_upload_bypasses_cache(cache, monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, data=None, files=None,
                  timeout=120):
        calls.append(files)
        return ok({"text": "转写结果"})

    monkeypatch.setattr(sf_transport, "post", fake_post)
    spec = {"file": ("a.mp3", __import__("io").BytesIO(b"x"), "audio/mpeg")}
    sf_client.post(URL, files=spec, data={"model": "whisper"})
    sf_client.post(URL, files=spec, data={"model": "whisper"})

    assert len(calls) == 2
    assert list(cache.rglob("*.json")) == []
