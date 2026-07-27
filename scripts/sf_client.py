"""SiliconFlow 调用的缓存 + 重试层，包在 ``sf_transport``（curl 子进程）外面。

两件事：

1. **内容寻址磁盘缓存**。键是 ``sha256(模型名 + 端点 + 规范化请求体)``，值是完整
   响应 JSON。一次晚期失败之后重跑，前面已经付过钱的调用一个都不用重发。
   只缓存成功响应 —— 把失败缓存下来，重跑会永远卡在同一个错误上。

2. **带分类的重试**。盲目重试 400/401/402 只是把真实原因拖到超时之后才暴露，
   所以请求本身有问题（400）、鉴权（401/403）、余额不足（402）一次都不重试，
   直接抛 :class:`FatalHTTPError`；限流和 5xx 才退避重试。

调用方只要把 ``sf_transport.post`` 换成 ``sf_client.post``，签名完全一致。
"""

import email.utils
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import sf_transport

DEFAULT_CACHE_DIRNAME = ".llm_cache"
DEFAULT_MAX_RETRIES = 3

# 超时的重试次数单独压低：客户端超时时服务端很可能已经算完并且计了费，
# 再发一遍就是花两份钱买一份结果。
TIMEOUT_MAX_ATTEMPTS = 2

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# 重试这几个只会浪费时间并掩盖真实原因
FATAL_STATUS = frozenset({400, 401, 402, 403})

BACKOFF_BASE_SEC = 1.0
BACKOFF_CAP_SEC = 30.0          # 单次退避上限
MAX_TOTAL_BACKOFF_SEC = 60.0    # 一次请求所有退避加起来的上限

_sleep = time.sleep    # 测试里打桩，别真睡


class FatalHTTPError(RuntimeError):
    """不可重试的 HTTP 失败。``exit_code`` 按仓库退出码约定给到调用方。"""

    def __init__(self, status_code: int, reason: str, detail: str,
                 exit_code: int = 1):
        super().__init__(f"HTTP {status_code} {reason}: {detail}")
        self.status_code = status_code
        self.reason = reason
        self.detail = detail
        self.exit_code = exit_code


class RetriesExhausted(RuntimeError):
    """可重试的失败重试到上限仍未成功。"""

    def __init__(self, detail: str, attempts: int, status_code=None):
        super().__init__(detail)
        self.detail = detail
        self.attempts = attempts
        self.status_code = status_code


FATAL_REASONS = {
    400: ("bad_request", "请求体不合法，重试无意义 —— 先看请求本身"),
    401: ("unauthorized", "SILICONFLOW_API_KEY 无效或已过期"),
    402: ("insufficient_balance", "SiliconFlow 账户余额不足，请充值后重跑"),
    403: ("forbidden", "当前 key 无权访问该模型或端点"),
}


# ── 配置 ──────────────────────────────────────────────────────────────────────

class _Config:
    def __init__(self):
        self.cache_enabled = True
        self.cache_dir = Path(__file__).resolve().parent.parent / DEFAULT_CACHE_DIRNAME
        self.max_retries = DEFAULT_MAX_RETRIES


_cfg = _Config()
_stats = {"hits": 0, "misses": 0, "writes": 0}


def configure(cache_dir=None, cache_enabled=None, max_retries=None) -> None:
    if cache_dir is not None:
        _cfg.cache_dir = Path(cache_dir).expanduser().resolve()
    if cache_enabled is not None:
        _cfg.cache_enabled = bool(cache_enabled)
    if max_retries is not None:
        _cfg.max_retries = max(1, int(max_retries))
    _stats.update(hits=0, misses=0, writes=0)


def cache_stats() -> dict:
    return dict(_stats)


def log_cache_stats(prefix: str = "llm-cache") -> None:
    total = _stats["hits"] + _stats["misses"]
    if not _cfg.cache_enabled:
        print(f"[{prefix}] 已禁用（--no-llm-cache），{total} 次调用全部实发",
              flush=True)
        return
    print(f"[{prefix}] 命中 {_stats['hits']} / 未命中 {_stats['misses']}"
          f"（共 {total} 次调用，新写入 {_stats['writes']} 条）→ {_cfg.cache_dir}",
          flush=True)


# ── 缓存键 ────────────────────────────────────────────────────────────────────

def _request_body(json_body, data):
    """把 ``json=`` / ``data=`` 两种传法统一成 dict，拿不到就返回 None。"""
    if json_body is not None:
        return json_body
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="replace")
    if isinstance(data, str) and data.strip():
        try:
            return json.loads(data)
        except ValueError:
            return None
    return None


def cache_key(url: str, body: dict) -> str:
    """``sha256(模型名 + 端点 + 规范化请求体)``。

    规范化用 ``sort_keys`` + 紧凑分隔符，键序抖动不该造成缓存穿透；反过来，
    prompt、temperature 这些只要动一个字符，键就必须变。
    """
    model = str(body.get("model", "")) if isinstance(body, dict) else ""
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    raw = "\n".join([model, url, canonical])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    # 分两级目录，免得单个目录堆几万个文件
    return _cfg.cache_dir / key[:2] / f"{key}.json"


def _warn_corrupt(path: Path, why: str) -> None:
    # 坏缓存回落成实发请求是对的，但必须留痕：静默兜底会让「缓存目录整个坏掉」
    # 表现成一次莫名其妙变贵的重跑，没人查得出来。
    print(f"[llm-cache] 缓存文件损坏（{why}）：{path}；改为实发一次请求，成功后覆写",
          file=sys.stderr, flush=True)


def _read_cache(key: str):
    path = _cache_path(key)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None                       # 未命中，不是损坏
    except OSError as e:
        _warn_corrupt(path, f"读不出来：{e}")
        return None
    try:
        envelope = json.loads(raw)
    except ValueError as e:
        _warn_corrupt(path, f"不是合法 JSON：{e}")
        return None
    if not isinstance(envelope, dict):
        _warn_corrupt(path, "顶层不是对象")
        return None
    response = envelope.get("response")
    if response is None:
        _warn_corrupt(path, "缺少 response 字段")
        return None
    return sf_transport.Response(
        200, json.dumps(response, ensure_ascii=False),
        envelope.get("response_headers") or {})


def _write_cache(key: str, url: str, body: dict, payload: dict) -> None:
    path = _cache_path(key)
    envelope = {
        "key": key,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": body.get("model") if isinstance(body, dict) else None,
        "endpoint": url,
        "response": payload,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(envelope, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)
        _stats["writes"] += 1
    except OSError as e:
        # 缓存写不进去不该让整条流水线倒下，但必须看得见
        print(f"[llm-cache] 写入失败 {path}: {e}", file=sys.stderr, flush=True)


# ── 重试 ──────────────────────────────────────────────────────────────────────

def parse_retry_after(value, now=None):
    """``Retry-After`` 支持秒数和 HTTP 日期两种写法，都要能解析。"""
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (when - now).total_seconds())


def backoff_delay(attempt: int) -> float:
    """指数退避 + 抖动。``attempt`` 从 0 起。"""
    base = min(BACKOFF_BASE_SEC * (2 ** attempt), BACKOFF_CAP_SEC)
    return base + random.uniform(0, base * 0.25)


def _fatal(status_code: int, text: str) -> FatalHTTPError:
    reason, hint = FATAL_REASONS[status_code]
    # 这几类重跑多少次都一样，归配置错（退 1）而不是外部依赖失败（退 3）
    return FatalHTTPError(status_code, reason, f"{hint}；响应：{text[:300]}",
                          exit_code=1)


def _post_with_retries(url, headers, json_body, data, files, timeout, tag):
    budget = MAX_TOTAL_BACKOFF_SEC
    attempts_allowed = _cfg.max_retries
    attempt = 0
    last = "未知失败"
    last_status = None

    while attempt < attempts_allowed:
        try:
            resp = sf_transport.post(url, headers=headers, json=json_body,
                                     data=data, files=files, timeout=timeout)
        except sf_transport.TransportTimeout as e:
            last, last_status = str(e), None
            # 超时的服务端可能已经算完并计费，所以额度比普通失败更紧
            attempts_allowed = min(attempts_allowed, TIMEOUT_MAX_ATTEMPTS)
            print(f"[{tag}] 客户端超时：{e}。服务端可能已处理并计费，"
                  f"超时重试上限压到 {TIMEOUT_MAX_ATTEMPTS} 次",
                  file=sys.stderr, flush=True)
            wait = backoff_delay(attempt)
        except sf_transport.TransportError as e:
            last, last_status = str(e), None
            print(f"[{tag}] 连接失败：{e}", file=sys.stderr, flush=True)
            wait = backoff_delay(attempt)
        else:
            status = resp.status_code
            if status in FATAL_STATUS:
                raise _fatal(status, resp.text)
            if status == 200:
                try:
                    return resp, resp.json()
                except ValueError:
                    last, last_status = "200 但响应体不是合法 JSON", status
                    wait = backoff_delay(attempt)
            elif status in RETRYABLE_STATUS:
                last, last_status = f"HTTP {status}: {resp.text[:200]}", status
                wait = None
                if status == 429:
                    wait = parse_retry_after(resp.headers.get("Retry-After"))
                    if wait is not None:
                        wait = min(wait, BACKOFF_CAP_SEC)
                        print(f"[{tag}] 429，按 Retry-After 等 {wait:.1f}s",
                              file=sys.stderr, flush=True)
                if wait is None:
                    wait = backoff_delay(attempt)
            else:
                # 既不在可重试名单也不在致命名单（404 之类），重试没有意义
                raise FatalHTTPError(status, "unexpected_http_status",
                                     resp.text[:300], exit_code=3)

        attempt += 1
        if attempt >= attempts_allowed:
            break
        wait = min(wait, budget)
        if wait <= 0:
            break
        budget -= wait
        print(f"[{tag}] 第 {attempt}/{attempts_allowed} 次重试，{wait:.1f}s 后"
              f"（{last}）", file=sys.stderr, flush=True)
        _sleep(wait)
        if budget <= 0:
            attempt = attempts_allowed
            print(f"[{tag}] 累计退避已达上限 {MAX_TOTAL_BACKOFF_SEC:.0f}s，不再重试",
                  file=sys.stderr, flush=True)
            break

    raise RetriesExhausted(last, attempt, last_status)


# ── 对外接口 ──────────────────────────────────────────────────────────────────

def post(url, headers=None, json=None, data=None, files=None, timeout=120,
         tag="llm"):
    """签名与 ``sf_transport.post`` 一致，外加缓存与分类重试。

    命中缓存时一个请求都不发。``files`` 上传（转写音频）不参与缓存 —— 请求体
    是二进制流，内容寻址的成本高于收益。
    """
    body = _request_body(json, data)
    cacheable = _cfg.cache_enabled and files is None and isinstance(body, dict)

    key = cache_key(url, body) if cacheable else None
    if key:
        hit = _read_cache(key)
        if hit is not None:
            _stats["hits"] += 1
            print(f"[llm-cache] 命中 {key[:12]}（{body.get('model')}），不发请求",
                  flush=True)
            return hit
        _stats["misses"] += 1

    resp, payload = _post_with_retries(url, headers, json, data, files,
                                       timeout, tag)
    if key:
        _write_cache(key, url, body, payload)
    return resp
