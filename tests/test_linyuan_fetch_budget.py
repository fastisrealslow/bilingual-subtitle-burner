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
