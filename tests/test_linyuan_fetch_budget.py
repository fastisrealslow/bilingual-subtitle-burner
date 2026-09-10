"""Download incident #626: large progressing streams must outlive three chunks."""
import io
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import ci_fetch_bilibili as b


class Response(io.BytesIO):
    def __init__(self,data,status,headers):
        super().__init__(data);self.status=status;self.headers=headers


def test_more_than_three_partial_responses_resume_to_exact_complete_file(tmp_path):
    payload=b'x'*150000
    starts=[]
    class Opener:
        def open(self,request,timeout):
            value=request.headers.get('Range')
            start=int(value.split('=')[1].split('-')[0]) if value else 0
            starts.append(start)
            headers={'Content-Length':str(len(payload)-start)}
            if start:headers['Content-Range']=f'bytes {start}-{len(payload)-1}/{len(payload)}'
            return Response(payload[start:start+25000],206 if start else 200,headers)
    out=tmp_path/'video.m4s'
    b.download_one(Opener(),['https://cdn.test/video'],'https://bilibili.com',out)
    assert out.read_bytes()==payload
    assert starts==[0,25000,50000,75000,100000,125000]


def test_nonprogressing_mirror_remains_bounded(monkeypatch,tmp_path):
    calls=[]
    class Opener:
        def open(self,*a,**kw):
            calls.append(1);raise TimeoutError('socket stalled')
    monkeypatch.setattr(b.time,'sleep',lambda _:None)
    with pytest.raises(RuntimeError,match='CDN'):
        b.download_one(Opener(),['https://cdn.test/video'],'ref',tmp_path/'video')
    assert len(calls)==3


def test_progress_does_not_disable_total_budget(monkeypatch,tmp_path):
    tick=[0]
    monkeypatch.setattr(b.time,'monotonic',lambda:tick[0])
    class Opener:
        def open(self,*a,**kw):
            tick[0]=20
            return Response(b'x'*25000,200,{'Content-Length':'999999'})
    with pytest.raises(b.FetchBudgetExceeded):
        b.download_one(Opener(),['https://cdn.test/video'],'ref',tmp_path/'video',deadline=10)


def test_fetch_deadline_writes_retry_evidence_for_inventory(monkeypatch,tmp_path):
    report=tmp_path/'source_quality.json'
    monkeypatch.setattr(b,'opener',lambda:object())
    monkeypatch.setattr(b,'via_view',lambda *a:123)
    monkeypatch.setattr(b,'playurl',lambda *a:{'video':['cdn'],'audio':[],'height':1080})
    def fail(*a,**kw):raise b.FetchBudgetExceeded('download budget')
    monkeypatch.setattr(b,'download',fail)
    monkeypatch.setattr(sys,'argv',['fetch','--url','https://www.bilibili.com/video/BV1EV411g7LU',
        '--out',str(tmp_path/'video.mp4'),'--failure-report',str(report)])
    with pytest.raises(SystemExit):b.main()
    row=json.loads(report.read_text())
    assert row['retryable'] is True and row['passed'] is False
    assert row['failure_stage']=='source-fetch'


def test_partial_download_survives_new_invocation_and_signed_url_rotation(tmp_path):
    payload = b'a' * 30000
    out = tmp_path / 'video.m4s'
    class Interrupted:
        def open(self, request, timeout):
            if request.headers.get('Range'):
                raise b.FetchBudgetExceeded('interrupted')
            return Response(payload[:15000], 200, {'Content-Length': '30000'})
    with pytest.raises(b.FetchBudgetExceeded):
        b.download_one(Interrupted(), ['https://cdn.test/video?token=old'], 'same-source', out)
    class Resumed:
        def open(self, request, timeout):
            assert request.headers['Range'] == 'bytes=15000-'
            return Response(payload[15000:], 206,
                {'Content-Range': 'bytes 15000-29999/30000', 'Content-Length': '15000'})
    b.download_one(Resumed(), ['https://cdn.test/video?token=new'], 'same-source', out)
    assert out.read_bytes() == payload


def test_changed_representation_never_appends_to_old_partial(tmp_path):
    out = tmp_path / 'video.m4s'
    partial = out.with_suffix('.m4s.part')
    partial.write_bytes(b'old' * 5000)
    out.with_suffix('.m4s.download.json').write_text(json.dumps(
        dict(identity=dict(source='same-source', paths=['/old']), total=30000)))
    class NewStream:
        def open(self, request, timeout):
            assert not request.headers.get('Range')
            return Response(b'n' * 30000, 200, {'Content-Length': '30000'})
    b.download_one(NewStream(), ['https://cdn.test/new'], 'same-source', out)
    assert out.read_bytes() == b'n' * 30000


def test_audio_failure_retains_completed_video_track(monkeypatch, tmp_path):
    out = tmp_path / 'video.mp4'
    def download(op, urls, referer, dest, **kwargs):
        if dest.name.endswith('.audio.m4s'):
            raise b.FetchBudgetExceeded('audio interrupted')
        dest.write_bytes(b'v' * 30000)
    monkeypatch.setattr(b, 'download_one', download)
    with pytest.raises(b.FetchBudgetExceeded):
        b.download(None, dict(video=['video'], audio=['audio']), 'source', out)
    assert out.with_suffix('.video.m4s').stat().st_size == 30000


def test_full_partial_checkpoint_does_not_request_unsatisfiable_range(tmp_path):
    out = tmp_path / 'video.m4s'
    out.with_suffix('.m4s.part').write_bytes(b'v' * 30000)
    out.with_suffix('.m4s.download.json').write_text(json.dumps(
        dict(identity=dict(source='source', paths=['/video']), total=30000)))
    class NoRequest:
        def open(self, *args, **kwargs):
            raise AssertionError('full checkpoint must not be downloaded again')
    b.download_one(NoRequest(), ['https://cdn.test/video'], 'source', out)
    assert out.read_bytes() == b'v' * 30000


def test_completed_mux_is_reused_and_changed_source_or_damage_redownloads(monkeypatch,tmp_path):
    import subprocess
    import shutil
    source=tmp_path/'fixture.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i',
        'testsrc2=size=160x120:rate=10:duration=3','-f','lavfi','-i',
        'sine=duration=3','-c:v','libx264','-c:a','aac','-shortest',str(source)],check=True)
    calls=[]
    def fetch(op,urls,referer,dest,**kwargs):
        calls.append((referer,urls));shutil.copyfile(source,dest)
    monkeypatch.setattr(b,'download_one',fetch)
    out=tmp_path/'nested'/'video.mp4'
    streams=dict(video=['https://cdn.test/video?sig=old'],audio=['https://cdn.test/audio'],height=1080)
    b.download(None,streams,'source-a',out)
    assert len(calls)==2 and not out.with_suffix('.video.m4s').exists()
    rotated={**streams,'video':['https://cdn.test/video?sig=new']}
    b.download(None,rotated,'source-a',out)
    assert len(calls)==2
    b.download(None,rotated,'source-b',out)
    assert len(calls)==4
    out.write_bytes(b'damaged')
    b.download(None,rotated,'source-b',out)
    assert len(calls)==6 and b.validate_media(out)['audio_streams']==1
    b.download(None,{**rotated,'height':720},'source-b',out)
    assert len(calls)==8
