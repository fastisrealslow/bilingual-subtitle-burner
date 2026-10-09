"""A real HLS source is discoverable, but an unfinished playlist is not stock."""
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import monitor_v2 as M
from fc import index as FC


@pytest.mark.parametrize('body', ['captcha', '#EXTM3U\n#EXTINF:10,\na.ts',
    '#EXTM3U\n#EXTINF:nan,\na.ts\n#EXT-X-ENDLIST',
    '#EXTM3U\n#EXTINF:-10,\na.ts\n#EXT-X-ENDLIST',
    '#EXTM3U\n#EXTINF:broken,\na.ts\n#EXT-X-ENDLIST',
    '#EXTM3U\n#EXTINF:10,\na.ts\n# comment #EXT-X-ENDLIST',
    '#EXTM3Ucaptcha\n#EXTINF:10,\na.ts\n#EXT-X-ENDLIST'])
def test_unknown_live_or_corrupt_playlist_does_not_invent_duration(body):
    assert M.NeteaseVideoSource.playlist_duration(body)==0


def test_complete_playlist_duration_and_actual_metadata(monkeypatch):
    source=M.NeteaseVideoSource({}, {})
    monkeypatch.setattr(source,'_page',lambda *_:'<title>林园：最新访谈_网易</title> https://video.example/index.m3u8')
    monkeypatch.setattr(M,'http_get',lambda *a,**kw:'#EXTM3U\n#EXTINF:100.25,\na.ts\n#EXTINF:80.5,\nb.ts\n#EXT-X-ENDLIST')
    info=source._parse_video('VREAL12345')
    assert info['duration']==180.75
    assert info['duration_provenance']=='hls_extinf_endlist'
    assert info['mp4']==''  # Never guess an unrelated mp4 from an opaque hash.
    assert info['title']=='林园：最新访谈'


def test_master_playlist_uses_actual_child_duration_and_host(monkeypatch):
    source=M.NeteaseVideoSource({}, {})
    monkeypatch.setattr(source,'_page',lambda *_:'<title>林园访谈_网易</title> https://master.example/main.m3u8')
    seen=[]
    def fetch(url,**kw):
        seen.append(url)
        if url=='https://master.example/main.m3u8':
            return '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=123\nhttps://child.example/media.m3u8'
        return '#EXTM3U\n#EXTINF:180,\n/videolib1/2026/abcdef-mobile-0.ts\n#EXT-X-ENDLIST'
    monkeypatch.setattr(M,'http_get',fetch)
    info=source._parse_video('VREAL12345')
    assert info['duration']==180
    assert info['mp4']=='https://child.example/videolib1/2026/abcdef-mobile.mp4'
    assert seen==['https://master.example/main.m3u8','https://child.example/media.m3u8']


def test_netease_seeds_rotate_past_25_and_hls_rows_are_real_candidates(monkeypatch):
    monkeypatch.setattr(M.time,'sleep',lambda *_:None)
    ids=['VTEST'+str(i).zfill(5) for i in range(30)]
    source=M.NeteaseVideoSource(dict(tag_urls=['unused'],pages=1),{})
    monkeypatch.setattr(source,'_page',lambda *_:' '.join(
        f'<a href="https://www.163.com/v/video/{v}.html">林园最新访谈</a>' for v in ids))
    monkeypatch.setattr(source,'_parse_video',lambda v:dict(title='林园最新访谈'+v,mp4='',
        m3u8='https://video.example/'+v+'.m3u8',cover='',duration=180,
        duration_provenance='hls_extinf_endlist'))
    first=list(source.fetch(None))
    source.partial_items=[]
    second=source.fetch(None)
    assert {r['id'] for r in first+second}=={'netease_video:'+v for v in ids}
    assert len(first)==len(second)==24
    assert all(json.loads(r['extra'])['has_video'] for r in first+second)
    state=dict(dispatched=[],rejected=[],published={})
    audit={}
    assert FC.pick([first[0]],state,10,audit=audit)
    assert audit['unknown_duration_candidates']==0
