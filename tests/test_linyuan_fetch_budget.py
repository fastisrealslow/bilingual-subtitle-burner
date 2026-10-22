"""Download incident #626: large progressing streams must outlive three chunks."""
import io
import hashlib
import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import ci_fetch_bilibili as b


class Response(io.BytesIO):
    def __init__(self,data,status,headers):
        super().__init__(data);self.status=status;self.headers=headers


def test_truncated_prefix_comparison_resumes_only_bytes_already_matched(tmp_path):
    payload=bytes(range(256))*600
    partial=tmp_path/'track.part';partial.write_bytes(payload)
    requests=[]
    class Opener:
        def open(self,request,timeout):
            start,end=map(int,request.headers['Range'].split('=')[1].split('-'))
            requests.append((start,end))
            return Response(payload[start:min(end+1,start+25000)],206,
                {'Content-Range':f'bytes {start}-{end}/{len(payload)+50000}','ETag':'new'})
    assert b.identical_prefix(Opener(),'https://cdn.test/track','source',partial,len(payload)+50000,'new',b.time.monotonic()+30)
    assert [r[0] for r in requests]==list(range(0,len(payload),25000))
    assert partial.read_bytes()==payload


@pytest.mark.parametrize('fault',['different_bytes','wrong_range','wrong_total','changed_etag'])
def test_resumed_prefix_never_accepts_content_or_version_mismatch(tmp_path,fault):
    payload=b'a'*50000;partial=tmp_path/'track.part';partial.write_bytes(payload)
    class Opener:
        def open(self,request,timeout):
            start,end=map(int,request.headers['Range'].split('=')[1].split('-'))
            if start==0:
                return Response(payload[:25000],206,{'Content-Range':f'bytes {start}-{end}/90000','ETag':'new'})
            data=payload[start:] if fault!='different_bytes' else b'b'*25000
            lo=start if fault!='wrong_range' else 0
            total=90000 if fault!='wrong_total' else 90001
            return Response(data,206,{'Content-Range':f'bytes {lo}-{end}/{total}',
                'ETag':'changed' if fault=='changed_etag' else 'new'})
    if fault=='different_bytes':
        assert not b.identical_prefix(Opener(),'https://cdn.test/track','source',partial,90000,'new',b.time.monotonic()+30)
    else:
        with pytest.raises(RuntimeError):
            b.identical_prefix(Opener(),'https://cdn.test/track','source',partial,90000,'new',b.time.monotonic()+30)
    assert partial.read_bytes()==payload


def test_nonprogressing_prefix_is_bounded_and_total_deadline_remains(tmp_path,monkeypatch):
    partial=tmp_path/'track.part';partial.write_bytes(b'a'*50000);calls=[]
    class Opener:
        def open(self,*a,**kw):
            calls.append(1);raise TimeoutError('stalled')
    with pytest.raises(RuntimeError):
        b.identical_prefix(Opener(),'https://cdn.test/track','source',partial,90000,'new',b.time.monotonic()+30)
    assert len(calls)==1
    with pytest.raises(b.FetchBudgetExceeded):
        b.identical_prefix(Opener(),'https://cdn.test/track','source',partial,90000,'new',b.time.monotonic()-1)
    assert len(calls)==1


def test_incomplete_http_read_resumes_after_only_successfully_compared_bytes(tmp_path):
    from http.client import IncompleteRead
    payload=b'a'*50000;partial=tmp_path/'track.part';partial.write_bytes(payload);starts=[]
    class Interrupted(Response):
        def read(self,size=-1):
            if self.tell():raise IncompleteRead(b'unchecked bytes',10000)
            return super().read(min(size,10000))
    class Opener:
        def open(self,request,timeout):
            start,end=map(int,request.headers['Range'].split('=')[1].split('-'))
            starts.append(start)
            return Interrupted(payload[start:end+1],206,{'Content-Range':f'bytes {start}-{end}/90000','ETag':'new'})
    assert b.identical_prefix(Opener(),'https://cdn.test/track','source',partial,90000,'new',b.time.monotonic()+30)
    assert starts==[0,10000,20000,30000,40000]
    with pytest.raises(b.FetchBudgetExceeded):
        b.identical_prefix(Opener(),'https://cdn.test/track','source',partial,90000,'new',b.time.monotonic()-1)
    assert starts==[0,10000,20000,30000,40000]


def test_sha256_file_works_without_python311_file_digest(tmp_path):
    path = tmp_path / 'media.bin'
    payload = b'public-media-check' * 100000
    path.write_bytes(payload)
    with path.open('rb') as handle:
        assert b.sha256_file(handle) == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize('alternatives, expected_deadline', [(True, 45), (False, 120)])
def test_mirror_proof_local_budget_never_changes_the_byte_comparison(monkeypatch, tmp_path, alternatives, expected_deadline):
    partial=tmp_path/'track.part';partial.write_bytes(b'a'*15000)
    tick=[0]
    monkeypatch.setattr(b.time,'monotonic',lambda:tick[0])
    def proof(op,url,ref,path,total,etag,deadline):
        assert deadline==expected_deadline
        assert path.read_bytes()==b'a'*15000 and etag=='new'
        return False  # Actual different bytes must still return false.
    monkeypatch.setattr(b,'identical_prefix',proof)
    assert not b.mirror_prefix_proof(None,'https://cdn.test/track','source',partial,
                                     30000,'new',120,alternatives=alternatives)
    assert partial.read_bytes()==b'a'*15000


@pytest.mark.parametrize('expired', [False, True])
def test_mirror_proof_timeout_only_yields_when_global_budget_remains(monkeypatch, tmp_path, expired):
    partial=tmp_path/'track.part';partial.write_bytes(b'a'*15000)
    tick=[0];monkeypatch.setattr(b.time,'monotonic',lambda:tick[0])
    def proof(*args):
        tick[0]=120 if expired else 46
        raise b.FetchBudgetExceeded('proof timed out')
    monkeypatch.setattr(b,'identical_prefix',proof)
    with pytest.raises(b.FetchBudgetExceeded if expired else RuntimeError) as error:
        b.mirror_prefix_proof(None,'https://cdn.test/track','source',partial,
                              30000,'new',120,alternatives=True)
    if not expired:
        assert not isinstance(error.value,b.FetchBudgetExceeded)
    assert partial.read_bytes()==b'a'*15000


def test_slow_changed_etag_proof_yields_to_fast_fully_verified_mirror(monkeypatch, tmp_path):
    # Real 752 failure spent 23 minutes checking 147MB on one CDN, then
    # four seconds checking the SAME bytes on another. Simulate elapsed time
    # while executing the actual byte comparator and download/resume code.
    payload=bytes(range(256))*20000;offset=2*(1<<20)
    out=tmp_path/'track';partial=out.with_suffix('.part')
    partial.write_bytes(payload[:offset])
    manifest=out.with_suffix('.download.json')
    manifest.write_text(json.dumps(dict(identity=dict(source='source',paths=['/track']),
                                        total=len(payload),etag='old')))
    tick=[0];requests=[];verified=[]
    monkeypatch.setattr(b.time,'monotonic',lambda:tick[0])
    monkeypatch.setattr(b,'rank_mirrors',lambda op,urls,ref,**kw:urls)
    class ProofResponse(Response):
        def __init__(self,data,headers,slow):
            super().__init__(data,206,headers);self.slow=slow
        def read(self,size=-1):
            if self.slow:tick[0]+=46
            data=super().read(size)
            if not self.slow:verified.append(data)
            return data
    class Opener:
        def open(self,req,timeout):
            start,end=req.headers['Range'].split('=')[1].split('-')
            start=int(start);end=int(end) if end else len(payload)-1
            slow='slow.test' in req.full_url
            requests.append((slow,start,end))
            headers={'Content-Range':f'bytes {start}-{end}/{len(payload)}',
                     'ETag':'slow' if slow else 'fast'}
            if start==0:
                assert partial.read_bytes()==payload[:offset]
                return ProofResponse(payload[start:end+1],headers,slow)
            return Response(payload[start:end+1],206,headers)
    b.download_one(Opener(),['https://slow.test/track','https://fast.test/track'],
                   'source',out,deadline=120)
    assert out.read_bytes()==payload
    assert b''.join(verified)==payload[:offset]  # ALL bytes, never sampling.
    assert (True,0,offset-1) in requests and (False,0,offset-1) in requests
    assert json.loads(manifest.read_text())['etag']=='fast'
    assert tick[0]==46  # A local timeout did not spend the whole 120s budget.


def test_all_slow_mirrors_eventually_allow_full_proof_without_discarding_checkpoint(monkeypatch, tmp_path):
    payload=bytes(range(256))*20000;offset=2*(1<<20)
    out=tmp_path/'track';partial=out.with_suffix('.part');partial.write_bytes(payload[:offset])
    out.with_suffix('.download.json').write_text(json.dumps(dict(
        identity=dict(source='source',paths=['/track']),total=len(payload),etag='old')))
    tick=[0];proof_requests=[]
    monkeypatch.setattr(b.time,'monotonic',lambda:tick[0])
    monkeypatch.setattr(b.time,'sleep',lambda _:None)
    monkeypatch.setattr(b,'rank_mirrors',lambda op,urls,ref,**kw:urls)
    class SlowProof(Response):
        def read(self,size=-1):
            tick[0]+=46
            return super().read(size)
    class Opener:
        def open(self,req,timeout):
            start,end=req.headers['Range'].split('=')[1].split('-')
            start=int(start);end=int(end) if end else len(payload)-1
            headers={'Content-Range':f'bytes {start}-{end}/{len(payload)}','ETag':'new'}
            if start==0:
                proof_requests.append(req.full_url)
                assert partial.read_bytes()==payload[:offset]
                return SlowProof(payload[:offset],206,headers)
            return Response(payload[start:end+1],206,headers)
    b.download_one(Opener(),['https://slow1.test/track','https://slow2.test/track'],
                   'source',out,deadline=1000)
    assert out.read_bytes()==payload
    # Two bounded rounds, then ONE full proof under the original global cap.
    assert len(proof_requests)==5 and tick[0]==276


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


def test_mirror_speed_ranking_keeps_range_identity_and_ignores_wrong_responses(monkeypatch):
    import concurrent.futures
    tick=[0.0]
    monkeypatch.setattr(b.time,'monotonic',lambda:tick[0])
    class Sequential:
        def __init__(self,**kw):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def map(self,fn,items):return map(fn,items)
    monkeypatch.setattr(concurrent.futures,'ThreadPoolExecutor',Sequential)
    class Measured(Response):
        def __init__(self,step,status=206,offset=100000):
            super().__init__(b'x'*2097152,status,{'Content-Range':f'bytes {offset}-2197151/3000000'})
            self.step=step
        def read(self,size=-1):
            tick[0]+=self.step
            return super().read(size)
    class Opener:
        def open(self,request,timeout):
            assert request.headers['Range']=='bytes=100000-2197151'
            assert request.headers['Referer']=='same-source' and timeout<=6
            if 'wrong' in request.full_url:return Measured(.001,offset=0)
            if 'ignored' in request.full_url:return Measured(.001,status=200)
            return Measured(.01 if 'fast' in request.full_url else .5)
    urls=['https://cdn/slow','https://cdn/fast','https://cdn/wrong','https://cdn/ignored']
    ranked=b.rank_mirrors(Opener(),urls,'same-source',offset=100000,expected_total=3000000)
    assert ranked[:2]==[urls[1],urls[0]] and set(ranked)==set(urls)


def test_faster_mirror_resumes_the_existing_partial_without_erasing_it(monkeypatch,tmp_path):
    payload=b'a'*15000+b'b'*15000;out=tmp_path/'video.m4s'
    urls=['https://slow/video','https://fast/video']
    out.with_suffix('.m4s.part').write_bytes(payload[:15000])
    manifest=out.with_suffix('.m4s.download.json')
    manifest.write_text(json.dumps(dict(identity=dict(source='same-source',paths=['/video']),total=30000)))
    def choose(op,mirrors,referer,**kw):
        assert kw['offset']==15000 and kw['expected_total']==30000
        return list(reversed(mirrors))
    monkeypatch.setattr(b,'rank_mirrors',choose)
    class Opener:
        def open(self,request,timeout):
            assert request.full_url==urls[1] and request.headers['Range']=='bytes=15000-'
            return Response(payload[15000:],206,{'Content-Range':'bytes 15000-29999/30000','Content-Length':'15000'})
    b.download_one(Opener(),urls,'same-source',out)
    assert out.read_bytes()==payload
    assert json.loads(manifest.read_text())['identity']==dict(source='same-source',paths=['/video'])


@pytest.mark.parametrize('mirror_count', [1,2])
def test_large_checkpoint_uses_complete_bounded_ranges_on_fastest_same_mirror(monkeypatch,tmp_path,mirror_count):
    payload=(bytes(range(256))*100000)
    urls=['https://fast.test/track','https://slow.test/track'][:mirror_count]
    out=tmp_path/'video.m4s';offset=1024*1024
    out.with_suffix('.m4s.part').write_bytes(payload[:offset])
    out.with_suffix('.m4s.download.json').write_text(json.dumps(dict(
        identity=dict(source='source',paths=['/track']),total=len(payload),etag='same')))
    monkeypatch.setattr(b,'rank_mirrors',lambda op,urls,ref,**kw:urls)
    requests=[]
    class Opener:
        def open(self,request,timeout):
            assert request.full_url==urls[0]
            start,end=map(int,request.headers['Range'].split('=')[1].split('-'))
            assert end-start+1<=8*(1<<20)
            requests.append((start,end))
            return Response(payload[start:end+1],206,dict(
                **{'Content-Range':f'bytes {start}-{end}/{len(payload)}'},ETag='same'))
    b.download_one(Opener(),urls,'source',out)
    assert out.read_bytes()==payload
    assert len(requests)==3 and requests[0][0]==offset
    assert all(a[1]+1==z[0] for a,z in zip(requests,requests[1:]))


def test_fresh_large_download_reuses_probe_length_before_its_first_transfer(tmp_path):
    payload=bytes(range(256))*80000
    requests=[]
    class Opener:
        def open(self,request,timeout):
            assert request.headers.get('Range'), 'Cold media must not start a throttled open-ended response'
            start,end=map(int,request.headers['Range'].split('=')[1].split('-'))
            requests.append((start,end))
            assert end-start+1<=8*(1<<20)
            return Response(payload[start:end+1],206,dict(
                **{'Content-Range':f'bytes {start}-{end}/{len(payload)}'},ETag='same'))
    out=tmp_path/'fresh.m4s'
    b.download_one(Opener(),['https://one.test/track','https://two.test/track'],'source',out)
    assert out.read_bytes()==payload
    transfers=[r for r in requests if r[1]-r[0]+1!=2*(1<<20)]
    assert len(transfers)==3 and transfers[0]==(0,8*(1<<20)-1)
    assert all(a[1]+1==z[0] for a,z in zip(transfers,transfers[1:]))
    manifest=json.loads(out.with_suffix('.m4s.download.json').read_text())
    assert manifest['total']==len(payload) and manifest['etag']=='same' and manifest['sha256']


def test_successful_backup_stays_primary_after_a_complete_range(monkeypatch,tmp_path):
    # Actual #87 repeatedly returned to a failing initially-fast mirror. Every
    # switch of CDN ETag then re-downloaded ALL retained bytes for comparison.
    payload=bytes(range(256))*80000
    urls=['https://initial.test/track','https://backup.test/track']
    out=tmp_path/'video.m4s';offset=1024*1024
    out.with_suffix('.m4s.part').write_bytes(payload[:offset])
    out.with_suffix('.m4s.download.json').write_text(json.dumps(dict(
        identity=dict(source='source',paths=['/track']),total=len(payload),etag='initial')))
    monkeypatch.setattr(b,'rank_mirrors',lambda op,urls,ref,**kw:urls)
    requests=[]
    class Opener:
        def open(self,request,timeout):
            requests.append((request.full_url,request.headers['Range']))
            if request.full_url==urls[0]:raise TimeoutError('primary stalled')
            start,end=map(int,request.headers['Range'].split('=')[1].split('-'))
            return Response(payload[start:end+1],206,dict(
                **{'Content-Range':f'bytes {start}-{end}/{len(payload)}'},ETag='backup'))
    b.download_one(Opener(),urls,'source',out)
    assert out.read_bytes()==payload
    assert sum(url==urls[0] for url,_ in requests)==1
    # The first switch must still prove every byte of the retained prefix.
    assert (urls[1],f'bytes=0-{offset-1}') in requests
    assert json.loads(out.with_suffix('.m4s.download.json').read_text())['etag']=='backup'
