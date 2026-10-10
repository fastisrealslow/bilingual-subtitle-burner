import importlib.util
from pathlib import Path
import pytest

spec=importlib.util.spec_from_file_location('fetch_yicai',Path(__file__).resolve().parents[1]/'linyuan/ci_fetch_yicai.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_official_html_media_query_preserved():
    assert module.extract_video_url('<video src="https://media.example/a.mp4?auth=a&amp;t=3">')=='https://media.example/a.mp4?auth=a&t=3'
    assert module.extract_video_url('{"file":"https:\\/\\/media.example/a.mp4"}')=='https://media.example/a.mp4'
    with pytest.raises(ValueError):
        module.extract_video_url('<html>No video</html>')


def test_strong_identity_ignores_rotating_signatures_but_not_asset_path(monkeypatch):
    class Response:
        headers={'ETag':'"asset1"','Content-Length':'2048','Accept-Ranges':'bytes'}
        def __enter__(self):return self
        def __exit__(self,*args):pass
    requests=[]
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda req,**kw:(requests.append(req) or Response()))
    url='https://www.yicai.com/video/103329354.html'
    first=module.media_identity('https://cdn.example/a.mp4?auth=old',url,20)
    assert first==module.media_identity('https://cdn.example/a.mp4?auth=new',url,20)
    assert first!=module.media_identity('https://cdn.example/b.mp4?auth=new',url,20)
    assert requests[0].method=='HEAD' and 'auth' not in str(first)
    Response.headers['ETag']='W/"weak"'
    with pytest.raises(ValueError,match='safe byte-range'):module.media_identity('https://cdn.example/a.mp4',url,20)


def test_partial_resume_preserves_progress_and_never_splices_different_files(tmp_path):
    output=tmp_path/'final.mp4';output.write_bytes(b'existing final is not a partial')
    identity=dict(page_url='official page',asset_sha256='asset',etag='"first"',size=2048)
    partial,proof=module.prepare_partial(output,identity)
    partial.write_bytes(b'partial bytes')
    same,_=module.prepare_partial(output,identity)
    assert same.read_bytes()==b'partial bytes'
    changed={**identity,'etag':'"changed"'}
    module.prepare_partial(output,changed)
    assert not partial.exists() and output.read_bytes()==b'existing final is not a partial'


def test_timeout_keeps_partial_and_next_call_resumes_it(tmp_path,monkeypatch):
    import json
    import subprocess
    page='https://www.yicai.com/video/103329354.html'
    identity=dict(page_url=page,asset_sha256='a',etag='"same"',size=2048)
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self):return b'<video src="https://cdn.example/a.mp4?auth=fresh">'
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *a,**kw:Response())
    monkeypatch.setattr(module,'media_identity',lambda *a:identity)
    output=tmp_path/'source.mp4';commands=[]
    def first(command,**kw):
        commands.append(command);partial=Path(command[command.index('--output')+1])
        partial.write_bytes(b'x'*1024)
        raise subprocess.TimeoutExpired('curl',1)
    monkeypatch.setattr(module.subprocess,'run',first)
    with pytest.raises(subprocess.TimeoutExpired):module.download(page,output,30)
    assert not output.exists() and module.partial_path(output).stat().st_size==1024
    def second(command,**kw):
        commands.append(command);partial=Path(command[command.index('--output')+1])
        assert partial.stat().st_size==1024
        with partial.open('ab') as f:f.write(b'y'*1024)
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(module.subprocess,'run',second)
    assert module.download(page,output,30)==output and output.stat().st_size==2048
    assert all('--continue-at' in cmd and 'If-Range: "same"' in cmd for cmd in commands)
    assert not module.partial_path(output).exists()


def test_nonresumable_cdn_can_still_download_without_appending_unverified_bytes(tmp_path,monkeypatch):
    import subprocess
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def read(self):return b'<video src="https://cdn.example/a.mp4">'
    monkeypatch.setattr(module.urllib.request,'urlopen',lambda *a,**kw:Response())
    def unavailable(*a):raise ValueError('no range validator')
    monkeypatch.setattr(module,'media_identity',unavailable)
    output=tmp_path/'source.mp4'
    module.partial_path(output).write_bytes(b'old unverified partial')
    def fetch(command,**kw):
        assert '--continue-at' not in command and '--header' not in command
        Path(command[command.index('--output')+1]).write_bytes(b'x'*2048)
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(module.subprocess,'run',fetch)
    assert module.download('https://www.yicai.com/video/103329354.html',output,30)==output
    assert output.read_bytes()==b'x'*2048
    assert module.partial_path(output).read_bytes()==b'old unverified partial'
