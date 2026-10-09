"""The first page URL is SD; actual quality metadata must choose the HD stream."""
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import monitor_v2 as M


def test_actual_quality_ranks_beat_sd_first_and_unrelated_preview(monkeypatch):
    sd='https://video.example/mda-real/360p/h264/real.mp4'
    hd='https://video.example/mda-real/1080p/cae_h264/real.mp4'
    unrelated='https://video.example/2160p/other.mp4'
    body='<title>林园真实访谈_好看视频</title>'+json.dumps(dict(clarityUrl=[
        dict(key='sd',rank=0,url=sd),dict(key='1080p',rank=3,url=hd)],playurl=sd,preview=unrelated))
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return body.encode()
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**kw:Response())
    info=M.HaokanVideoSource({},{})._extract('12345')
    assert info['mp4_url']==hd
    assert info['mp4_sd']==sd


def test_real_stream_paths_rank_new_and_legacy_formats_without_inventing_urls():
    source=M.HaokanVideoSource({}, {})
    sd='https://video.example/360p/v.mp4'
    hd='https://video.example/hd/v.mp4'
    sc='https://video.example/sc/v.mp4'
    full='https://video.example/1080p/v.mp4'
    assert source._pick_best([sd,hd,sc,full])[0]==full
    assert source._pick_best([sd,hd,full])[0]==full
    assert source._pick_best([])==('','')
    assert source._pick_best([sd,hd],[dict(rank=99,url='https://not-on-page.example/v.mp4')])[0]==hd
