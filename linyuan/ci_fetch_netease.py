"""Resolve only the requested NetEase page's public, finite video player."""
import hashlib
from html.parser import HTMLParser
import math
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit


PAGE_HOSTS = {'www.163.com', 'm.163.com'}


class Player(HTMLParser):
    def __init__(self):
        super().__init__()
        self.canonical = []
        self.players = []

    def handle_starttag(self, tag, attrs):
        row = dict(attrs)
        if tag == 'link' and 'canonical' in (row.get('rel') or '').split():
            self.canonical.append(row.get('href'))
        if row.get('id') == 'dplayer':
            self.players.append(row.get('data-url'))


def page_key(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname not in PAGE_HOSTS
            or parsed.username or parsed.password or parsed.port
            or parsed.query or parsed.fragment
            or not re.fullmatch(r'/v/video/V[A-Z0-9]+\.html', parsed.path)):
        raise ValueError('需要网易公开视频页面的完整地址')
    return parsed.path


def media_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.netease.com')
            or parsed.username or parsed.password or parsed.port or parsed.fragment):
        raise ValueError('网易播放器包含非官方公开视频地址')
    return url


def player_url(page, requested):
    key = page_key(requested)
    parser = Player()
    parser.feed(page)
    if (len(parser.canonical) != 1 or page_key(parser.canonical[0]) != key
            or len(parser.players) != 1 or not parser.players[0]):
        raise ValueError('网易页面没有与请求对应的唯一正文播放器')
    result = media_url(parser.players[0])
    if not urlsplit(result).path.endswith('.m3u8'):
        raise ValueError('网易正文播放器不是公开HLS视频')
    return result


def playlist_seconds(content, url):
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if (not lines or lines[0] != '#EXTM3U' or lines[-1] != '#EXT-X-ENDLIST'
            or any(line.startswith(('#EXT-X-KEY', '#EXT-X-SESSION-KEY',
                                    '#EXT-X-STREAM-INF', '#EXT-X-MAP')) for line in lines)):
        raise ValueError('只接收完整、未加密的网易正文视频，不解析加密或直播流')
    durations, segments = [], []
    pending = False
    for line in lines[1:]:
        if line.startswith('#EXTINF:'):
            if pending:
                raise ValueError('网易视频清单的分段时长缺少对应片段')
            duration = float(line.split(':', 1)[1].split(',', 1)[0])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError('网易视频清单时长无效')
            durations.append(duration)
            pending = True
        elif not line.startswith('#'):
            if not pending:
                raise ValueError('网易视频清单存在未声明的媒体片段')
            segments.append(media_url(urljoin(url, line)))
            pending = False
    if pending or not segments or len(durations) != len(segments):
        raise ValueError('网易视频清单不完整')
    return round(sum(durations), 3)


def _read(url, referer, is_page=False):
    request = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': referer})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                (page_key if is_page else media_url)(response.geturl())
                raw = response.read(4 * 1024 * 1024 + 1)
            break
        except urllib.error.HTTPError as exc:
            if exc.code not in {408,429,500,502,503,504} or attempt==2:raise
        except (urllib.error.URLError,TimeoutError,ConnectionError):
            if attempt==2:raise
        # No URL/token or raw response in logs. A temporary read is never
        # interpreted as missing content, public permission or a media pass.
        print(f'[网易取源] 正文/清单临时读取失败，有限重试 {attempt+1}/2',flush=True)
        time.sleep(2 ** (attempt+1))
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError('网易正文或视频清单超过安全大小限制')
    return raw.decode('utf-8-sig')


def resolve(url):
    page_key(url)
    page = _read(url, 'https://www.163.com/', is_page=True)
    playlist_url = player_url(page, url)
    playlist = _read(playlist_url, url)
    return dict(article=url, media_url=playlist_url,
                declared_duration=playlist_seconds(playlist, playlist_url),
                provenance='canonical+unique_dplayer+finite_unencrypted_hls',
                page_sha256=hashlib.sha256(page.encode()).hexdigest(),
                playlist_sha256=hashlib.sha256(playlist.encode()).hexdigest(),
                recording_date=None)
