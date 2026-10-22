"""Transient reads must not abort replenishment or manufacture empty stock."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'linyuan'))
import source_supply


@pytest.mark.parametrize('status', [408, 429, 500, 502, 503, 504])
def test_temporary_http_failure_retries_same_read(monkeypatch, status):
    calls=[]; sleeps=[]
    def call(command, **kwargs):
        calls.append((command, kwargs))
        if len(calls)==1:
            raise subprocess.CalledProcessError(1, command, stderr=f'gh: Server Error (HTTP {status})'.encode())
        return b'{"artifacts":[{"id":7}]}'
    monkeypatch.setattr(source_supply.subprocess, 'check_output', call)
    monkeypatch.setattr(source_supply.time, 'sleep', sleeps.append)
    assert source_supply.api('actions/artifacts?per_page=100&page=3')=={'artifacts':[{'id':7}]}
    assert calls[0]==calls[1]
    assert calls[0][0][1]=='api'
    assert calls[0][1]==dict(stderr=subprocess.PIPE, timeout=60)
    assert sleeps==[2]


@pytest.mark.parametrize('status', [400, 401, 403, 404, 422])
def test_permanent_failure_preserves_original_exception(monkeypatch, status):
    calls=[]
    original=subprocess.CalledProcessError(1,['gh'], output=json.dumps({'status':str(status)}).encode(),
                                         stderr=f'gh: denied (HTTP {status})'.encode())
    def call(*args, **kwargs):calls.append(1);raise original
    monkeypatch.setattr(source_supply.subprocess, 'check_output', call)
    monkeypatch.setattr(source_supply.time, 'sleep', lambda _:pytest.fail('must not retry'))
    if status==404:
        assert source_supply.cached_artifact(source_supply.api, 7) is None
    else:
        with pytest.raises(subprocess.CalledProcessError) as caught:source_supply.api('actions/artifacts/7')
        assert caught.value is original
    assert len(calls)==1


@pytest.mark.parametrize('timeout', [True, False])
def test_temporary_failure_is_bounded_and_raises(monkeypatch, timeout):
    calls=[];sleeps=[]
    original=(subprocess.TimeoutExpired(['gh'],60) if timeout else
              subprocess.CalledProcessError(1,['gh'],stderr=b'gh: error (HTTP 502)'))
    def call(*args, **kwargs):calls.append(1);raise original
    monkeypatch.setattr(source_supply.subprocess,'check_output',call)
    monkeypatch.setattr(source_supply.time,'sleep',sleeps.append)
    with pytest.raises(type(original)) as caught:source_supply.api('actions/artifacts')
    assert caught.value is original
    assert len(calls)==3 and sleeps==[2,4]


def test_invalid_payload_is_not_empty_inventory(monkeypatch):
    monkeypatch.setattr(source_supply.subprocess,'check_output',lambda *a,**k:b'bad json')
    with pytest.raises(json.JSONDecodeError):source_supply.api('actions/artifacts')
