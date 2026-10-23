import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.request
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import ci_fetch_bilibili as D
import ci_fetch_ifeng as F


@pytest.mark.parametrize('denied',[False,True])
def test_actual_transfer_preserves_prefix_and_stops_upstream_refusal(tmp_path,denied):
    content=b'original public media bytes '*4096
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            interval=self.headers.get('Range');requests.append(interval)
            if len(requests)>1 and denied:
                self.send_error(567);return
            start=int(interval.split('=')[1].split('-')[0]) if interval else 0
            self.send_response(206 if interval else 200)
            self.send_header('Content-Length',str(len(content)-start))
            self.send_header('ETag','"same-exact-public-representation"')
            if interval:self.send_header('Content-Range',f'bytes {start}-{len(content)-1}/{len(content)}')
            self.end_headers()
            # Reproduce the observed partial transfer, then an exact range
            # response. This is a controlled HTTP protocol test, not media QC.
            self.wfile.write(content[:32768] if len(requests)==1 else content[start:])
            self.close_connection=True
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    target=tmp_path/'source.mp4'
    try:
        run=lambda:D.download_one(urllib.request.build_opener(),
            [f'http://127.0.0.1:{server.server_port}/public.mp4'],
            'https://v.ifeng.com/c/8WHpW9qUT1g',target,
            attempts=1,deadline=time.monotonic()+8,stop_http_statuses=(567,))
        if denied:
            with pytest.raises(urllib.error.HTTPError) as error:run()
            assert error.value.code==567 and len(requests)==2
            assert target.with_suffix('.mp4.part').read_bytes()==content[:32768]
            assert not target.exists()
        else:
            run()
            assert target.read_bytes()==content
            assert requests==[None,'bytes=32768-']
            assert json.loads(target.with_suffix('.mp4.download.json').read_text())['sha256']==hashlib.sha256(content).hexdigest()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_official_wrapper_keeps_exact_source_checkpoint_and_revalidates_complete_media(tmp_path,monkeypatch):
    from contextlib import contextmanager
    source=dict(article='https://v.ifeng.com/c/8WHpW9qUT1g',guid='actual-guid',
        media_url='https://video19.ifeng.com/original.mp4',declared_duration=1641)
    @contextmanager
    def page(*a,**k):
        from io import BytesIO
        yield BytesIO(b'public document')
    monkeypatch.setattr(F.urllib.request,'urlopen',page)
    monkeypatch.setattr(F,'article_video',lambda *a:dict(source))
    monkeypatch.setattr(D,'validate_media',lambda p:dict(duration=1641.45))
    attempts=[]
    def download(op,urls,referer,out,**options):
        assert urls==[source['media_url']] and referer==source['article']
        assert options['attempts']==1 and 567 in options['stop_http_statuses']
        partial=out.with_suffix('.mp4.part');attempts.append(out)
        if len(attempts)==1:
            partial.write_bytes(b'verified exact source prefix')
            raise D.FetchBudgetExceeded('bounded transfer deadline')
        assert partial.read_bytes()==b'verified exact source prefix'
        out.write_bytes(partial.read_bytes()+b' rest');partial.unlink()
    monkeypatch.setattr(D,'download_one',download)
    output=tmp_path/'video.mp4'
    with pytest.raises(D.FetchBudgetExceeded):F.fetch(source['article'],output)
    assert attempts[0].with_suffix('.mp4.part').exists() and not output.exists()
    assert F.fetch(source['article'],output)==output
    assert attempts[0]==attempts[1]
    origin=json.loads(output.with_suffix('.origin.json').read_text())
    assert origin['sha256']==hashlib.sha256(output.read_bytes()).hexdigest()
    assert F.fetch(source['article'],output)==output and len(attempts)==2
