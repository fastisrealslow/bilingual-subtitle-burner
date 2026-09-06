"""A slow/corrupt artifact must refresh its URL within a finite budget."""
from pathlib import Path
import sys
import types
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan/fc'))
import index as fc


def test_failed_download_refreshes_redirect_and_validates_zip(tmp_path,monkeypatch):
    import requests
    calls=[]
    def redirect(*args,**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(status_code=302,headers={'Location':f'https://example.test/{len(calls)}'})
    def run(command,**kwargs):
        assert command[command.index('--max-time')+1]=='120'
        assert command[command.index('--speed-time')+1]=='20'
        target=Path(command[command.index('-o')+1])
        if len(calls)==1:
            target.write_bytes(b'partial zip');return types.SimpleNamespace(returncode=28)
        with zipfile.ZipFile(target,'w') as z:z.writestr('meta.json','{}')
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(requests,'get',redirect)
    monkeypatch.setattr(fc.subprocess,'run',run)
    monkeypatch.setattr(fc,'log_event',lambda *args:None)
    monkeypatch.setattr(fc,'flush_logs',lambda:None)
    assert fc.download_reviewed_zip(1,tmp_path/'part.zip')>0
    assert len(calls)==2 and calls[0]['params']!=calls[1]['params']


def test_download_stops_after_three_failed_attempts(tmp_path,monkeypatch):
    import requests
    calls=[]
    def fail(*args,**kwargs):
        calls.append(1);raise RuntimeError('unavailable')
    monkeypatch.setattr(requests,'get',fail)
    monkeypatch.setattr(fc,'log_event',lambda *args:None)
    monkeypatch.setattr(fc,'flush_logs',lambda:None)
    with pytest.raises(RuntimeError,match='exhausted 3 bounded attempts'):
        fc.download_reviewed_zip(1,tmp_path/'part.zip')
    assert len(calls)==3
