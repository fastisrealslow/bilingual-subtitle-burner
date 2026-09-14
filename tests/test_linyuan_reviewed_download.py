"""A slow/corrupt artifact must refresh its URL within a finite budget."""
from pathlib import Path
import sys
import types
import zipfile
import io

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
        assert int(command[command.index('--max-filesize')+1]) > 630024798
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


def test_size_limit_failure_reports_artifact_and_curl_code_not_signed_url(tmp_path,monkeypatch):
    import requests
    logs=[]
    monkeypatch.setattr(requests,'get',lambda *a,**k:types.SimpleNamespace(
        status_code=302,headers={'Location':'https://example.test/private?sig=secret'}))
    monkeypatch.setattr(fc.subprocess,'run',lambda *a,**k:types.SimpleNamespace(returncode=63))
    monkeypatch.setattr(fc,'log_event',lambda *args:logs.append(args))
    monkeypatch.setattr(fc,'flush_logs',lambda:None)
    with pytest.raises(RuntimeError):fc.download_reviewed_zip(10262245814,tmp_path/'large.zip',attempts=1)
    assert '10262245814' in str(logs) and 'curl exit 63' in str(logs)
    assert 'secret' not in str(logs) and 'https://' not in str(logs)


def test_interrupted_same_artifact_resumes_verified_bytes(tmp_path,monkeypatch):
    import requests
    data=io.BytesIO()
    with zipfile.ZipFile(data,'w') as z:z.writestr('meta.json','{"complete":true}')
    payload=data.getvalue();cut=len(payload)//2;calls=[]
    monkeypatch.setattr(requests,'get',lambda *a,**k:types.SimpleNamespace(
        status_code=302,headers={'Location':'https://example.test/signed'}))
    def transfer(command,**kwargs):
        path=Path(command[command.index('-o')+1]);calls.append(command)
        if len(calls)==1:
            assert '--continue-at' not in command and not path.exists()
            path.write_bytes(payload[:cut])
            return types.SimpleNamespace(returncode=28)
        assert command[command.index('--continue-at')+1]=='-'
        assert path.read_bytes()==payload[:cut]
        with path.open('ab') as f:f.write(payload[cut:])
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(fc.subprocess,'run',transfer)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    monkeypatch.setattr(fc,'flush_logs',lambda:None)
    path=tmp_path/'artifact.zip';path.write_bytes(b'unrelated old file')
    assert fc.download_reviewed_zip(123,path,attempts=2)==len(payload)
    assert path.read_bytes()==payload and len(calls)==2


@pytest.mark.parametrize('failure',['unsupported_range','bad_zip'])
def test_resume_or_integrity_failure_restarts_cleanly(tmp_path,monkeypatch,failure):
    import requests
    calls=[]
    monkeypatch.setattr(requests,'get',lambda *a,**k:types.SimpleNamespace(
        status_code=302,headers={'Location':'https://example.test/signed'}))
    def transfer(command,**kwargs):
        path=Path(command[command.index('-o')+1]);calls.append(command)
        if len(calls)==1:
            path.write_bytes(b'partial');return types.SimpleNamespace(returncode=28)
        if len(calls)==2:
            assert '--continue-at' in command
            if failure=='bad_zip':path.write_bytes(b'not a complete ZIP')
            return types.SimpleNamespace(returncode=33 if failure=='unsupported_range' else 0)
        assert '--continue-at' not in command and not path.exists()
        with zipfile.ZipFile(path,'w') as z:z.writestr('meta.json','{}')
        return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(fc.subprocess,'run',transfer)
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    monkeypatch.setattr(fc,'flush_logs',lambda:None)
    assert fc.download_reviewed_zip(123,tmp_path/'artifact.zip')>0
    assert len(calls)==3


def test_0914_next_invocation_resumes_same_artifact_only(tmp_path,monkeypatch):
    import requests
    payload=io.BytesIO()
    with zipfile.ZipFile(payload,'w') as z:z.writestr('meta.json','{}')
    data=payload.getvalue();cut=len(data)//2;calls=[]
    monkeypatch.setattr(requests,'get',lambda *a,**k:types.SimpleNamespace(
        status_code=302,headers={'Location':'https://example.test/signed'}))
    monkeypatch.setattr(fc,'log_event',lambda *a:None)
    monkeypatch.setattr(fc,'flush_logs',lambda:None)
    def transfer(command,**kwargs):
        path=Path(command[command.index('-o')+1]);calls.append(command)
        if len(calls)==1:
            path.write_bytes(data[:cut]);return types.SimpleNamespace(returncode=28)
        assert '--continue-at' in command and path.read_bytes()==data[:cut]
        path.write_bytes(data);return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(fc.subprocess,'run',transfer)
    path=tmp_path/'artifact.zip'
    with pytest.raises(RuntimeError):
        fc.download_reviewed_zip(123,path,attempts=1,preserve_partial=True)
    assert path.read_bytes()==data[:cut]
    assert fc.download_reviewed_zip(123,path,attempts=1,preserve_partial=True)==len(data)
    def foreign(command,**kwargs):
        assert '--continue-at' not in command and not path.exists()
        path.write_bytes(data);return types.SimpleNamespace(returncode=0)
    monkeypatch.setattr(fc.subprocess,'run',foreign)
    assert fc.download_reviewed_zip(456,path,attempts=1,preserve_partial=True)==len(data)
