"""Native legacy dimensions must come from bounded actual container bytes."""
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import native_header_probe as N

URL='https://upos-sz-mirrorcos.bilivideo.com/native.flv?signature=private'


class Response(io.BytesIO):
    status=206
    def __init__(self,data,headers=None):
        super().__init__(data);self.read_sizes=[];self.read_count=0
        self.headers={'Content-Range':f'bytes 0-{min(len(data),N.MAX_BYTES)-1}/{len(data)}',
            'Content-Length':str(min(len(data),N.MAX_BYTES)),**(headers or {})}
    def read(self,n=-1):
        assert n>0;self.read_sizes.append(n);value=super().read(n);self.read_count+=len(value);return value


class Opener:
    def __init__(self,response):self.response=response;self.calls=[]
    def open(self,request,timeout):
        self.calls.append((request,timeout));return self.response


def run(op):return N.probe(op,URL,'https://www.bilibili.com/',N.time.monotonic()+75)


@pytest.fixture
def codec(monkeypatch):
    monkeypatch.setattr(N.shutil,'which',lambda _: '/usr/bin/ffprobe')
    calls=[]
    def ffprobe(args,**kw):
        data=Path(args[-1]).read_bytes();assert len(data)<=N.MAX_BYTES
        assert URL not in args and args[args.index('-protocol_whitelist')+1]=='file,pipe'
        assert kw['timeout']<=5;calls.append(args)
        return SimpleNamespace(returncode=0,stdout=json.dumps(dict(streams=[dict(width=1280,height=720)])))
    monkeypatch.setattr(N.subprocess,'run',ffprobe)
    return calls


def test_one_exact_prefix_is_bounded_and_only_dimensions_are_reported(codec):
    response=Response(b'FLV'+b'x'*(N.MAX_BYTES+100));op=Opener(response)
    result=run(op)
    assert result['width']==1280 and result['height']==720 and result['media_approved'] is False
    assert result['bytes_read']==N.MAX_BYTES and response.read_count==N.MAX_BYTES
    assert max(response.read_sizes)<=65536 and len(op.calls)==len(codec)==1
    assert op.calls[0][0].headers['Range']==f'bytes=0-{N.MAX_BYTES-1}'
    assert 'signature' not in json.dumps(result) and 'url' not in result


@pytest.mark.parametrize('headers',[
    {'Content-Range':'bytes 1-999/2000'}, {'Content-Range':'bytes 0-999999999/2000000000'},
    {'Content-Range':'bytes 0-100/100'},{'Content-Length':'9999'},{'Content-Range':''}])
def test_wrong_range_identity_never_becomes_native_evidence(codec,headers):
    response=Response(b'FLV'+b'x'*100,headers);assert run(Opener(response)) is None
    assert response.read_count==0 and not codec


def test_range_ignoring_server_does_not_trigger_full_download(codec):
    response=Response(b'FLV'+b'x'*100);response.status=200
    assert run(Opener(response)) is None and response.read_count==0 and not codec


@pytest.mark.parametrize('data',[b'#EXTM3U\nhttps://example.test/private',b'<html>blocked</html>',b''])
def test_non_container_prefix_cannot_make_ffprobe_fetch_external_content(codec,data):
    assert run(Opener(Response(data))) is None and not codec


def test_provider_restriction_is_visible_to_original_shared_cooldown(codec):
    calls=[]
    class Restricted:
        def open(self,*a,**kw):calls.append(1);raise HTTPError(URL,403,'restricted',{},None)
    with pytest.raises(HTTPError):run(Restricted())
    assert calls==[1] and not codec


@pytest.mark.parametrize('url',['http://localhost/native.flv','https://bilivideo.com.evil.test/native.mp4',
    'https://user:password@upos-sz-mirrorcos.bilivideo.com/native.flv',
    'https://upos-sz-mirrorcos.bilivideo.com:invalid/native.flv',None])
def test_untrusted_or_invalid_target_never_gets_a_request(codec,url):
    op=Opener(Response(b'FLV'+b'x'*100))
    assert N.probe(op,url,'ref',N.time.monotonic()+75) is None and not op.calls


def test_missing_probe_tool_does_not_spend_a_cdn_request(codec,monkeypatch):
    monkeypatch.setattr(N.shutil,'which',lambda _:None);op=Opener(Response(b'FLV'+b'x'*100))
    assert run(op) is None and not op.calls


@pytest.mark.parametrize('stream',[
    dict(width=0,height=720),dict(width=1280,height=720,side_data_list=[dict(rotation=90)]),
    dict(width=1280,height=720,sample_aspect_ratio='2:1')])
def test_ambiguous_display_dimensions_stay_unknown(codec,monkeypatch,stream):
    monkeypatch.setattr(N.subprocess,'run',lambda *a,**kw:SimpleNamespace(
        returncode=0,stdout=json.dumps(dict(streams=[stream]))))
    assert run(Opener(Response(b'FLV'+b'x'*100))) is None


def test_slow_prefix_stays_inside_the_time_and_byte_budget(codec,monkeypatch):
    now=[0.0];monkeypatch.setattr(N.time,'monotonic',lambda:now[0])
    class Slow(Response):
        def read(self,n=-1):now[0]+=5;return super().read(n)
    response=Slow(b'FLV'+b'x'*N.MAX_BYTES)
    with pytest.raises(TimeoutError):N.probe(Opener(response),URL,'ref',75)
    assert response.read_count<N.MAX_BYTES and not codec


@pytest.mark.skipif(not shutil.which('ffprobe') or not shutil.which('ffmpeg'),reason='Actual container tools unavailable')
@pytest.mark.parametrize('extension',['flv','mp4'])
def test_real_native_container_dimensions_without_full_remote_media(tmp_path,extension):
    path=tmp_path/('sample.'+extension)
    args=['ffmpeg','-y','-v','error','-f','lavfi','-i','color=c=blue:s=1280x720:r=5',
        '-frames:v','2','-c:v','libx264','-preset','ultrafast','-an']
    if extension=='mp4':args+=['-movflags','+faststart']
    subprocess.run(args+[str(path)],check=True,timeout=15)
    op=Opener(Response(path.read_bytes()));result=run(op)
    assert (result['width'],result['height'])==(1280,720)
    assert result['media_approved'] is False and len(op.calls)==1
