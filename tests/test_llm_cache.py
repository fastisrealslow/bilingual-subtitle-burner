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


@pytest.mark.parametrize("corrupt", [
    "{ 截断的",                                  # 不是合法 JSON
    "",                                          # 空文件（磁盘写到一半断电）
    '["顶层是数组"]',                            # 合法 JSON 但结构不对
    '{"key": "x", "endpoint": "y"}',             # 是对象但没有 response
])
def test_corrupt_cache_file_falls_back_to_a_real_call(cache, monkeypatch,
                                                      capsys, corrupt):
    """缓存文件坏了要回落去实发一次，不能把整条流水线炸掉。

    但也不能静默兜底：必须记日志说明是哪个文件坏了，并且拿到成功响应后覆写它，
    否则同一个坏文件会在后续每次重跑里反复触发实发请求。
    """
    counting_transport(monkeypatch, [ok()])
    sf_client.post(URL, json=BODY)
    path = next(iter(cache.rglob("*.json")))
    path.write_text(corrupt, encoding="utf-8")

    calls = counting_transport(monkeypatch, [ok()])
    resp = sf_client.post(URL, json=BODY)

    assert resp.json() == PAYLOAD
    assert len(calls) == 1, "缓存文件损坏时没有回落到真实请求"

    err = capsys.readouterr().err
    assert "损坏" in err and str(path) in err, "缓存损坏必须记日志，不允许静默兜底"

    # 坏文件被这次的成功响应覆写，下一次就该正常命中
    assert json.loads(path.read_text(encoding="utf-8"))["response"] == PAYLOAD
    exploding_transport(monkeypatch)
    assert sf_client.post(URL, json=BODY).json() == PAYLOAD


# ── 三个调用点都走缓存层 ─────────────────────────────────────────────────────

def test_all_three_siliconflow_call_sites_share_the_cache(cache, tmp_path,
                                                          monkeypatch):
    """highlight、translate、封面 VLM 三处都必须经过 sf_client，谁绕过去直连
    ``sf_transport`` 这条就红 —— 将来新加调用点忘了走缓存，靠这条拦住。

    判据不是「有没有发请求」（transport 打了桩，绕过去也一样能发），而是缓存
    统计：三次都该记成 miss+write，第二轮三次都该命中且传输层一次都不被碰。
    """
    import highlight
    import step7_cover
    import translate

    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg bytes")
    vlm_payload = {
        "choices": [{"message": {"content":
                     '[{"frame": 1, "person": "主讲人", "cover_score": 8}]'}}],
        "usage": {"prompt_tokens": 10},
    }

    calls = []

    def fake_post(url, headers=None, json=None, data=None, files=None,
                  timeout=120):
        calls.append(url)
        # 封面 VLM 走 data= 原始字节，另外两处走 json=
        return ok(vlm_payload if data is not None else PAYLOAD)

    monkeypatch.setattr(sf_transport, "post", fake_post)

    def run_all():
        return (
            highlight.call_llm([{"role": "user", "content": "挑金句"}],
                               "sk-test", "Qwen/Qwen3-8B", "https://x/v1"),
            translate.chat([{"role": "user", "content": "翻译"}],
                           "sk-test", "deepseek-ai/DeepSeek-V3", "https://x/v1"),
            step7_cover.call_vision_llm("sk-test", "Qwen/Qwen3-VL-8B-Instruct",
                                        [str(frame)], "芒格"),
        )

    first = run_all()
    assert first[2] == [{"frame": 1, "person": "主讲人", "cover_score": 8}]
    assert len(calls) == 3
    stats = sf_client.cache_stats()
    assert stats["misses"] == 3, "有调用点绕过了缓存层（没记 miss）"
    assert stats["writes"] == 3, "有调用点的响应没有写进缓存"

    # 第二轮必须全部命中：传输层换成「一被调用就炸」
    exploding_transport(monkeypatch)
    assert run_all() == first
    assert sf_client.cache_stats()["hits"] == 3, "有调用点第二轮没命中缓存"


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
