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
import shutil
import subprocess
import tempfile

DEFAULT_CA = "/usr/local/share/ca-certificates/agent-proxy-ca-2.crt"


def _ca_args():
    ca = os.environ.get("SF_CA_BUNDLE", DEFAULT_CA)
    return ["--cacert", ca] if ca and os.path.exists(ca) else []


class Response:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text

    def json(self):
        return _json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text[:500]}")


class TransportError(RuntimeError):
    pass


def _run(cmd, timeout):
    if not shutil.which("curl"):
        raise TransportError("curl 不可用，无法访问 SiliconFlow")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout + 30)
    except subprocess.TimeoutExpired as e:
        raise TransportError(f"curl 超时（{timeout}s）") from e
    out = p.stdout
    marker = "\n__SF_HTTP_"
    if marker not in out:
        raise TransportError(
            f"curl rc={p.returncode}: {(p.stderr or '')[:300]}")
    body, _, tail = out.rpartition(marker)
    return Response(int(tail.strip("_ \n")), body)


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
    return _run(_base(hdrs, timeout) + ["--data-binary", body, url], timeout)
