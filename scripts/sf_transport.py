"""SiliconFlow HTTP transport backed by curl.

Python's ssl module (OpenSSL 3 + Python 3.14) rejects the leaf certificate of
the MITM proxy this pipeline runs behind with "certificate signature failure",
so `requests` cannot reach api.siliconflow.cn at all. curl validates the same
chain against the same CA bundle without complaint, so we keep certificate
verification fully enabled and only swap the HTTP transport underneath.

`post` and `get` return an object exposing the small slice of the requests
Response API the call sites use, so wiring a call site up is a one-word change.
"""
import json as _json
import os
import re
import shutil
import subprocess
import tempfile

DEFAULT_CA = "/usr/local/share/ca-certificates/agent-proxy-ca-2.crt"


def _ca_args():
    ca = os.environ.get("SF_CA_BUNDLE", DEFAULT_CA)
    return ["--cacert", ca] if ca and os.path.exists(ca) else []


class Headers(dict):
    """HTTP 头大小写不敏感。``Retry-After`` / ``retry-after`` 都得取得到。"""

    def __init__(self, pairs=None):
        super().__init__()
        for k, v in (pairs or {}).items() if isinstance(pairs, dict) else (pairs or []):
            self[k] = v

    def __setitem__(self, key, value):
        super().__setitem__(str(key).lower(), value)

    def __getitem__(self, key):
        return super().__getitem__(str(key).lower())

    def __contains__(self, key):
        return super().__contains__(str(key).lower())

    def get(self, key, default=None):
        return super().get(str(key).lower(), default)


class Response:
    def __init__(self, status_code: int, text: str, headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers if isinstance(headers, Headers) else Headers(headers)

    def json(self):
        return _json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:500]}")


class TransportError(RuntimeError):
    pass


class TransportTimeout(TransportError):
    """curl 没在时限内返回。

    单独一个类型是因为超时的重试语义和连接失败不同：服务端很可能已经处理完
    并计了费，只是响应没能回来。上层据此把超时的重试次数压得更保守。
    """


def _parse_headers(raw: str) -> Headers:
    """解析 curl ``-D`` 转储的响应头。重定向会有多个头块，取最后一块。"""
    block = [b for b in re.split(r"\r?\n\r?\n", raw or "") if b.strip()]
    headers = Headers()
    if not block:
        return headers
    for line in block[-1].splitlines():
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip()] = value.strip()
    return headers


def _run(cmd, timeout):
    if not shutil.which("curl"):
        raise TransportError("curl 不可用，无法访问 SiliconFlow")
    hdr_fd, hdr_path = tempfile.mkstemp(prefix="sf_hdr_", suffix=".txt")
    os.close(hdr_fd)
    try:
        try:
            p = subprocess.run(cmd + ["-D", hdr_path], capture_output=True,
                               text=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired as e:
            raise TransportTimeout(f"curl 超时（{timeout}s）") from e
        try:
            with open(hdr_path, "r", encoding="utf-8", errors="replace") as fh:
                headers = _parse_headers(fh.read())
        except OSError:
            headers = Headers()
    finally:
        try:
            os.unlink(hdr_path)
        except OSError:
            pass
    out = p.stdout
    marker = "\n__SF_HTTP_"
    if marker not in out:
        raise TransportError(
            f"curl rc={p.returncode}: {(p.stderr or '')[:300]}")
    body, _, tail = out.rpartition(marker)
    return Response(int(tail.strip("_ \n")), body, headers)


def _base(headers, timeout):
    cmd = ["curl", "-sS", *_ca_args(), "--max-time", str(int(timeout)),
           "-w", "\n__SF_HTTP_%{http_code}__"]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    return cmd


def get(url, headers=None, timeout=60):
    return _run(_base(headers, timeout) + [url], timeout)


def post(url, headers=None, json=None, data=None, files=None, timeout=120):
    """POST JSON, raw bytes, or a multipart upload.

    `files` maps a field name to (filename, fileobj, content_type) and forces
    multipart, with `data` supplying the accompanying form fields.
    """
    hdrs = dict(headers or {})
    if files:
        # curl needs real paths for -F, so spool the stream to a temp file.
        cmd = _base({k: v for k, v in hdrs.items()
                     if k.lower() != "content-type"}, timeout)
        tmpdir = tempfile.mkdtemp(prefix="sf_upload_")
        try:
            for field, spec in files.items():
                filename, fileobj, ctype = spec
                path = os.path.join(tmpdir, os.path.basename(filename))
                with open(path, "wb") as out:
                    out.write(fileobj.read())
                cmd += ["-F", f"{field}=@{path};type={ctype}"]
            for k, v in (data or {}).items():
                cmd += ["-F", f"{k}={v}"]
            return _run(cmd + [url], timeout)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    hdrs.setdefault("Content-Type", "application/json")
    if json is not None:
        body = _json.dumps(json, ensure_ascii=False)
    elif isinstance(data, bytes):
        body = data.decode("utf-8")
    else:
        body = data or ""
    # 封面打分会把 base64 图片塞进请求体，几百 KB 的 argv 直接 E2BIG
    # （OSError: [Errno 7] Argument list too long），所以走临时文件。
    with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8",
                                     delete=False) as fh:
        fh.write(body)
        body_path = fh.name
    try:
        return _run(_base(hdrs, timeout) + ["--data-binary", f"@{body_path}", url],
                    timeout)
    finally:
        os.unlink(body_path)
