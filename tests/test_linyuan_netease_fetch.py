"""Bind public HLS to the requested article, not related videos or guesses."""
import hashlib
import json
from pathlib import Path
import sys
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import ci_fetch_netease as n
import ci_fetch_generic as g

PAGE = 'https://www.163.com/v/video/VA7UULO86.html'
MEDIA = 'https://flv3.bn.netease.com/real.m3u8'
HTML = f'<link rel="canonical" href="{PAGE}"><div id="dplayer" data-url="{MEDIA}"></div>'
PLAYLIST = '#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:6.5,\n/real-0.ts\n#EXTINF:3.5,\nreal-1.ts\n#EXT-X-ENDLIST\n'


class Response:
    def __init__(self, url=MEDIA, content=PLAYLIST.encode()):self.url=url;self.content=content
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def geturl(self):return self.url
    def read(self,limit):return self.content[:limit]


@pytest.mark.parametrize('error',[TimeoutError('timed out'),urllib.error.URLError(TimeoutError()),
    urllib.error.HTTPError(MEDIA,502,'temporary',{},None)])
def test_temporary_read_retries_same_request_and_keeps_exact_body(monkeypatch,error):
    calls=[];sleeps=[]
    def open_(request,timeout):
        calls.append((request,timeout))
        if len(calls)==1:raise error
        return Response()
    monkeypatch.setattr(n.urllib.request,'urlopen',open_)
    monkeypatch.setattr(n.time,'sleep',sleeps.append)
    assert n._read(MEDIA,PAGE)==PLAYLIST
    assert calls[0]==calls[1] and calls[0][1]==10 and sleeps==[2]


@pytest.mark.parametrize('status',[401,403,404,410])
def test_permission_or_removed_content_is_not_retried(monkeypatch,status):
    error=urllib.error.HTTPError(MEDIA,status,'permanent',{},None)
    def open_(*a,**kw):raise error
    monkeypatch.setattr(n.urllib.request,'urlopen',open_)
    monkeypatch.setattr(n.time,'sleep',lambda _:pytest.fail('must not retry'))
    with pytest.raises(urllib.error.HTTPError) as caught:n._read(MEDIA,PAGE)
    assert caught.value is error


def test_exhausted_reads_raise_without_empty_playlist(monkeypatch):
    calls=[];sleeps=[];error=TimeoutError('timed out')
    def open_(*a,**kw):calls.append(1);raise error
    monkeypatch.setattr(n.urllib.request,'urlopen',open_)
    monkeypatch.setattr(n.time,'sleep',sleeps.append)
    with pytest.raises(TimeoutError):n._read(MEDIA,PAGE)
    assert len(calls)==3 and sleeps==[2,4]


@pytest.mark.parametrize('response,match',[(Response(url='https://evil.test/a'),'非官方'),
    (Response(content=b'x'*(4*1024*1024+1)),'安全大小')])
def test_retry_never_waives_redirect_or_size_guard(monkeypatch,response,match):
    monkeypatch.setattr(n.urllib.request,'urlopen',lambda *a,**kw:response)
    monkeypatch.setattr(n.time,'sleep',lambda _:pytest.fail('must not retry invalid source'))
    with pytest.raises(ValueError,match=match):n._read(MEDIA,PAGE)


def test_exact_page_and_unique_body_player_ignore_sidebar():
    page = HTML + '<a href="https://www.163.com/v/video/VOTHER.html">related</a>'
    page += '<div data-url="https://flv3.bn.netease.com/wrong.m3u8"></div>'
    assert n.player_url(page, PAGE) == MEDIA
    assert n.playlist_seconds(PLAYLIST, MEDIA) == 10


@pytest.mark.parametrize('page', [
    HTML.replace('VA7UULO86', 'VOTHER'),
    HTML + '<div id="dplayer" data-url="https://flv3.bn.netease.com/other.m3u8"></div>',
    HTML.replace('id="dplayer"', 'id="sidebar"'),
    HTML.replace('flv3.bn.netease.com', 'flv3.bn.netease.com.evil.test'),
    HTML.replace('https://flv3', 'http://flv3'),
    HTML.replace('real.m3u8', 'preview.jpg'),
    HTML.replace(f'<link rel="canonical" href="{PAGE}">', ''),
])
def test_wrong_or_ambiguous_player_is_never_downloaded(page):
    with pytest.raises(ValueError):
        n.player_url(page, PAGE)


@pytest.mark.parametrize('content', [
    PLAYLIST.replace('#EXT-X-ENDLIST', ''),
    PLAYLIST.replace('#EXTINF:6.5,', '#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:6.5,'),
    PLAYLIST.replace('#EXTINF:6.5,', '#EXT-X-STREAM-INF:BANDWIDTH=1'),
    PLAYLIST.replace('/real-0.ts', 'https://evil.test/video.ts'),
    PLAYLIST.replace('/real-0.ts', 'file:///tmp/video.ts'),
    PLAYLIST.replace('#EXTINF:6.5,', '#EXTINF:nan,'),
    PLAYLIST.replace('#EXTINF:6.5,', '#EXTINF:-1,'),
    PLAYLIST.replace('/real-0.ts\n', ''),
    PLAYLIST.replace('#EXTINF:6.5,\n', ''),
])
def test_incomplete_encrypted_or_foreign_media_is_rejected(content):
    with pytest.raises(ValueError):
        n.playlist_seconds(content, MEDIA)


def test_resolution_saves_exact_provenance_without_guessing(monkeypatch):
    reads = []
    def read(url, referer, is_page=False):
        reads.append((url, referer, is_page))
        return HTML if is_page else PLAYLIST
    monkeypatch.setattr(n, '_read', read)
    result = n.resolve(PAGE)
    assert reads == [(PAGE, 'https://www.163.com/', True), (MEDIA, PAGE, False)]
    assert result['playlist_sha256'] == hashlib.sha256(PLAYLIST.encode()).hexdigest()
    assert result['declared_duration'] == 10 and result['recording_date'] is None


@pytest.mark.parametrize('duration, succeeds', [(10, True), (30, False)])
def test_production_downloads_actual_player_then_checks_media_and_duration(monkeypatch, tmp_path, duration, succeeds):
    monkeypatch.setattr(n, 'resolve', lambda url: dict(article=url, media_url=MEDIA, declared_duration=10))
    media = tmp_path/'video.mp4'
    media.write_bytes(b'actual-media-gated-separately')
    def download(command, **kwargs):
        assert command[-3:] == ['--referer', PAGE, MEDIA]
        assert '--print-to-file' in command and g.FORMAT in command
        (tmp_path/'downloaded-path.txt').write_text(str(media)+'\n')
    monkeypatch.setattr(g.subprocess, 'run', download)
    monkeypatch.setattr(g, 'validate_media', lambda path: dict(duration=duration))
    if succeeds:
        assert g.fetch(PAGE, tmp_path, tmp_path/'source_quality.json') == media
        proof = json.loads((tmp_path/'source_origin.json').read_text())
        assert proof['sha256'] == hashlib.sha256(media.read_bytes()).hexdigest()
    else:
        with pytest.raises(ValueError, match='实际媒体时长'):
            g.fetch(PAGE, tmp_path, tmp_path/'source_quality.json')
        assert not (tmp_path/'source_origin.json').exists()
