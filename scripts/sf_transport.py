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
    """大小写不敏感的响应头。``Retry-After`` 的大小写各家网关并不统一。"""

    def __init__(self, pairs=None):
        super().__init__()
        for k, v in dict(pairs or {}).items():
            self[k] = v

    def __setitem__(self, key, value):
        super().__setitem__(key.lower(), value)

    def __getitem__(self, key):
        return super().__getitem__(key.lower())

    def __contains__(self, key):
        return super().__contains__(key.lower())

    def get(self, key, default=None):
        return super().get(key.lower(), default)


def parse_header_block(dump: str) -> Headers:
    """解析 curl ``-D`` 落盘的响应头，只取最后一段（跟随重定向后的那次）。"""
    blocks = [b for b in re.split(r"\r?\n\r?\n", dump) if b.strip()]
    headers = Headers()
    if not blocks:
        return headers
    for line in blocks[-1].splitlines()[1:]:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip()] = value.strip()
    return headers


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
    """客户端侧超时。服务端可能已经处理并计费，重试要比普通失败更保守。"""


def _run(cmd, timeout):
    if not shutil.which("curl"):
        raise TransportError("curl 不可用，无法访问 SiliconFlow")
    # 响应头单独落盘：429 的 Retry-After 要拿来算退避，混进 stdout 会污染响应体。
    fd, hdr_path = tempfile.mkstemp(prefix="sf_hdr_", suffix=".txt")
    os.close(fd)
    try:
        try:
            p = subprocess.run(cmd + ["-D", hdr_path], capture_output=True,
                               text=True, timeout=timeout + 30)
        except subprocess.TimeoutExpired as e:
            raise TransportTimeout(f"curl 超时（{timeout}s）") from e
        out = p.stdout
        marker = "\n__SF_HTTP_"
        if marker not in out:
            detail = f"curl rc={p.returncode}: {(p.stderr or '')[:300]}"
            # 28 = curl 自己的 --max-time 到点，语义同子进程超时
            raise (TransportTimeout if p.returncode == 28
                   else TransportError)(detail)
        body, _, tail = out.rpartition(marker)
        with open(hdr_path, encoding="utf-8", errors="replace") as fh:
            headers = parse_header_block(fh.read())
        return Response(int(tail.strip("_ \n")), body, headers)
    finally:
        os.unlink(hdr_path)


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
