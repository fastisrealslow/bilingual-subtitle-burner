"""Bounded transport recovery for the existing CPU face models."""
import http.client
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def download(path, url, min_bytes):
    """Publish only a complete attempt; keep model identity and size checks."""
    path = Path(path)
    for attempt in range(3):
        temporary = None
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'linyuan-local-face/1.0'})
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name+'.',
                                             suffix='.part', delete=False) as stream:
                temporary = Path(stream.name)
                with urllib.request.urlopen(request, timeout=180) as response:
                    while True:
                        chunk = response.read(1024*1024)
                        if not chunk:
                            break
                        stream.write(chunk)
            if temporary.stat().st_size < min_bytes:
                raise ValueError('模型文件不完整')
            temporary.replace(path)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                http.client.IncompleteRead) as exc:
            # HTTPError is a URLError too: permission, debt and missing files
            # must fail immediately, rather than triggering transport retries.
            status = exc.code if isinstance(exc, urllib.error.HTTPError) else None
            retryable = status is None or status in (408, 429, 500, 502, 503, 504)
            if not retryable or attempt == 2:
                raise
            print(f'face_model_download_retry attempt={attempt+1}/3 '
                  f'error_type={type(exc).__name__} http_status={status}', flush=True)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        time.sleep((2, 5)[attempt])
