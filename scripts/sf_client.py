"""SiliconFlow 调用的统一外壳：内容寻址磁盘缓存 + 分类重试。

传输层仍然是 ``sf_transport``（curl 子进程），这里只在它外面包一层，
所有 SiliconFlow 调用（挑金句、翻译、封面 VLM）都从这里过。

为什么要这一层
--------------
1. **缓存**：流水线的钱几乎全花在 LLM/VLM 上。一次晚期失败（封面不达标、
   ffmpeg 崩了）之后重跑，前面已经付过钱的调用不该再发一遍。键是请求内容
   本身的 sha256，所以只要请求没变就一定命中，不需要任何失效逻辑。
2. **分类重试**：盲目重试 400/401/402 既浪费时间又掩盖真实原因 —— 密钥错了
   重试三次仍然是密钥错了，只是把失败推迟了几十秒。可重试的只有「再试一次
   可能就好了」的那些：限流、5xx、连接失败、响应体不是合法 JSON。

缓存只写成功响应。失败绝不落盘，否则一次 500 会被永久钉死。
"""
import hashlib
import json as _json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import sf_transport

DEFAULT_CACHE_DIRNAME = ".llm_cache"
DEFAULT_MAX_RETRIES = 3

# 超时的重试次数单独压低：客户端超时不代表服务端没干活，很可能已经生成完
# 并计了费，只是响应没回来。重试一次是止损，重试三次是重复付费三次。
MAX_TIMEOUT_ATTEMPTS = 2

BASE_BACKOFF_SEC = 1.0
MAX_SINGLE_BACKOFF_SEC = 30.0
# 单次调用花在退避上的总时长上限。没有这个上限，一个 Retry-After: 3600
# 就能让无人值守的流水线原地挂满整个 job 时限。
MAX_TOTAL_BACKOFF_SEC = 60.0

# 明确可重试：限流与网关抖动
RETRYABLE_STATUS = (429, 500, 502, 503, 504)
# 明确不可重试：请求本身有问题 / 鉴权 / 余额。重试纯属浪费时间
FATAL_STATUS = (400, 401, 402, 403)

FATAL_REASONS = {
    400: "bad_request",
    401: "unauthorized",
    402: "insufficient_balance",
    403: "forbidden",
}

# 打桩点：测试把它们换掉，别真睡、别真随机
_sleep = time.sleep
_now = lambda: datetime.now(timezone.utc)  # noqa: E731


def _jitter() -> float:
    """退避抖动系数，落在 [1.0, 1.25)。避免并发任务同时重试撞在一起。"""
    return 1.0 + random.random() * 0.25


class SFError(RuntimeError):
    """SiliconFlow 调用失败的共同基类。

    继承 ``RuntimeError`` 是为了让既有的 ``except RuntimeError`` 调用点
    （produce.translate_windows / make_title / make_covers）继续兜得住。
    """

    def __init__(self, message, reason, http_status=None, stage="llm"):
        super().__init__(message)
        self.reason = reason
        self.http_status = http_status
        self.stage = stage

    def fields(self) -> dict:
        return {"reason": self.reason, "http_status": self.http_status,
                "detail": str(self)}


class SFFatalError(SFError):
    """不可重试：请求本身、鉴权或余额有问题，需要人来改，重跑没用。"""


class SFRetryExhausted(SFError):
    """可重试的失败，但重试次数或退避预算已经用完。"""


# ── 配置 ──────────────────────────────────────────────────────────────────────

# 没调用过 configure() 就是纯透传：单独跑 scripts/ 里的脚本时不该凭空
# 在某个当前目录下长出一个缓存目录来
_CONFIG = {
    "cache_dir": None,
    "cache_enabled": False,
    "max_retries": DEFAULT_MAX_RETRIES,
}
_STATS = {"hits": 0, "misses": 0, "stores": 0}


def configure(cache_dir=None, cache_enabled=True,
              max_retries=DEFAULT_MAX_RETRIES) -> None:
    _CONFIG["cache_dir"] = str(cache_dir) if cache_dir else None
    _CONFIG["cache_enabled"] = bool(cache_enabled) and bool(cache_dir)
    _CONFIG["max_retries"] = max(0, int(max_retries))
    _STATS.update(hits=0, misses=0, stores=0)


def cache_stats() -> dict:
    return dict(_STATS)


def log_cache_summary(prefix: str = "llm-cache") -> None:
    if not _CONFIG["cache_enabled"]:
        print(f"[{prefix}] 已禁用（--no-llm-cache）", flush=True)
        return
    s = _STATS
    total = s["hits"] + s["misses"]
    rate = (s["hits"] / total * 100) if total else 0.0
    print(f"[{prefix}] 命中 {s['hits']} / 未命中 {s['misses']}"
          f"（{rate:.0f}%），新写入 {s['stores']} 条 → {_CONFIG['cache_dir']}",
          flush=True)


# ── 缓存 ──────────────────────────────────────────────────────────────────────

def _body_object(json_body=None, data=None):
    """把 ``json=`` 或 ``data=`` 两种传法归一成可规范化的对象。

    封面 VLM 那一处传的是已经 dump 好的 bytes，其余两处传 dict。
    """
    if json_body is not None:
        return json_body
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str):
        try:
            return _json.loads(data)
        except ValueError:
            return {"__raw__": data}
    return data if data is not None else {}


def cache_key(model: str, endpoint: str, body) -> str:
    """键 = sha256(模型名 + 端点 + 请求体的规范化 JSON)。

    ``sort_keys`` 让键只取决于请求的语义内容，不受 dict 顺序影响；请求体里
    任何一个字符变了（改了 prompt、换了图、调了 temperature）键就会变。
    """
    canonical = _json.dumps(
        {"model": model or "", "endpoint": endpoint or "", "body": body},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_path(key: str):
    root = _CONFIG["cache_dir"]
    if not root:
        return None
    return os.path.join(root, key[:2], f"{key}.json")


def cache_read(key: str):
    path = _cache_path(key)
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            record = _json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or "response" not in record:
        return None
    return record


def cache_write(key: str, model: str, endpoint: str, response_json) -> bool:
    path = _cache_path(key)
    if not path:
        return False
    record = {
        "key": key,
        "model": model,
        "endpoint": endpoint,
        # 写入时间和模型名只为排查用：缓存本身按内容寻址，不看这两个字段
        "written_at": _now().isoformat(),
        "status_code": 200,
        "response": response_json,
    }
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            _json.dump(record, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        print(f"[llm-cache] 写缓存失败（不影响本次调用）：{e}", file=sys.stderr)
        return False
    _STATS["stores"] += 1
    return True


# ── 重试 ──────────────────────────────────────────────────────────────────────

def parse_retry_after(value, now=None):
    """解析 ``Retry-After``：秒数和 HTTP 日期两种格式都要认。

    解析不了返回 None，交给调用方退回到指数退避 —— 头解析不出来不该是硬错误。
    """
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - (now or _now())).total_seconds())


def _classify_status(status: int):
    """→ ``("ok" | "retry" | "fatal", reason)``。"""
    if status == 200:
        return "ok", None
    if status in FATAL_STATUS:
        return "fatal", FATAL_REASONS[status]
    if status in RETRYABLE_STATUS:
        return "retry", f"http_{status}"
    # 其余 5xx 当服务端抖动重试；其余 4xx 是请求本身的问题，重试没有意义
    if 500 <= status < 600:
        return "retry", f"http_{status}"
    if 400 <= status < 500:
        return "fatal", f"http_{status}"
    return "retry", f"http_{status}"


def post(url, headers=None, json=None, data=None, timeout=120,
         stage="llm", model=None):
    """带缓存和分类重试的 POST。命中缓存时一个请求都不发。"""
    body = _body_object(json, data)
    if model is None and isinstance(body, dict):
        model = body.get("model")

    key = None
    if _CONFIG["cache_enabled"]:
        key = cache_key(model, url, body)
        record = cache_read(key)
        if record is not None:
            _STATS["hits"] += 1
            print(f"[llm-cache] 命中 {stage} {model or '?'} {key[:12]}"
                  f"（写于 {record.get('written_at', '?')}）", flush=True)
            return sf_transport.Response(
                200, _json.dumps(record["response"], ensure_ascii=False))
        _STATS["misses"] += 1
        print(f"[llm-cache] 未命中 {stage} {model or '?'} {key[:12]}", flush=True)

    resp, payload = _post_with_retry(url, headers, json, data, timeout,
                                     stage, model)
    if key is not None:
        cache_write(key, model, url, payload)
    return resp


def _post_with_retry(url, headers, json_body, data, timeout, stage, model):
    max_attempts = _CONFIG["max_retries"] + 1
    slept = 0.0
    timeout_attempts = 0
    last = None

    for attempt in range(1, max_attempts + 1):
        wait = None
        try:
            resp = sf_transport.post(url, headers=headers, json=json_body,
                                     data=data, timeout=timeout)
        except sf_transport.TransportTimeout as e:
            timeout_attempts += 1
            last = SFRetryExhausted(str(e), "timeout", None, stage)
            if timeout_attempts >= MAX_TIMEOUT_ATTEMPTS:
                print(f"[{stage}] 超时 {timeout_attempts} 次，不再重试："
                      f"客户端超时不代表服务端没执行，很可能已经生成并计费，"
                      f"继续重试就是重复付费", file=sys.stderr, flush=True)
                break
            print(f"[{stage}] 请求超时（{e}）。注意：服务端可能已经处理并计费，"
                  f"超时只保守重试 {MAX_TIMEOUT_ATTEMPTS - 1} 次",
                  file=sys.stderr, flush=True)
        except sf_transport.TransportError as e:
            last = SFRetryExhausted(str(e), "connect_failed", None, stage)
            print(f"[{stage}] 连接失败：{e}", file=sys.stderr, flush=True)
        else:
            verdict, reason = _classify_status(resp.status_code)
            if verdict == "fatal":
                raise SFFatalError(
                    f"HTTP {resp.status_code}: {(resp.text or '')[:300]}",
                    reason, resp.status_code, stage)
            if verdict == "ok":
                try:
                    payload = resp.json()
                except ValueError:
                    last = SFRetryExhausted(
                        f"响应体不是合法 JSON：{(resp.text or '')[:200]!r}",
                        "invalid_json", 200, stage)
                    print(f"[{stage}] 响应体不是合法 JSON，重试",
                          file=sys.stderr, flush=True)
                else:
                    return resp, payload
            else:
                last = SFRetryExhausted(
                    f"HTTP {resp.status_code}: {(resp.text or '')[:300]}",
                    reason, resp.status_code, stage)
                if resp.status_code == 429:
                    wait = parse_retry_after(resp.headers.get("Retry-After"))
                    if wait is not None:
                        print(f"[{stage}] 429 限流，按 Retry-After 等 {wait:.1f}s",
                              file=sys.stderr, flush=True)
                if wait is None:
                    print(f"[{stage}] HTTP {resp.status_code}，退避后重试",
                          file=sys.stderr, flush=True)

        if attempt >= max_attempts:
            break
        if wait is None:
            wait = min(BASE_BACKOFF_SEC * (2 ** (attempt - 1)),
                       MAX_SINGLE_BACKOFF_SEC) * _jitter()
        budget = MAX_TOTAL_BACKOFF_SEC - slept
        if budget <= 0:
            last = SFRetryExhausted(
                f"退避总时长已达上限 {MAX_TOTAL_BACKOFF_SEC:.0f}s",
                "backoff_budget_exhausted",
                getattr(last, "http_status", None), stage)
            break
        wait = min(wait, budget)
        _sleep(wait)
        slept += wait

    if last is None:
        last = SFRetryExhausted("调用失败", "unknown", None, stage)
    raise SFRetryExhausted(
        f"{last} —— 已重试 {_CONFIG['max_retries']} 次仍失败",
        last.reason, last.http_status, stage)
