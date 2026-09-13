"""Bounded retries for read-only receipts; never replay a publication request."""
import http.client
import json
import time
import urllib.error
import urllib.request


def read_json_get(request, timeout=30):
    if isinstance(request, urllib.request.Request) and request.get_method() != 'GET':
        raise ValueError('Receipt retries only support GET')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (urllib.error.URLError, ConnectionError, TimeoutError,
                http.client.IncompleteRead) as exc:
            if attempt == 2:
                raise
        # Do not print request headers or exception URLs: callers may authenticate.
        print(f'Receipt GET interrupted; retry {attempt + 1}/2', flush=True)
        time.sleep(2 ** attempt)
