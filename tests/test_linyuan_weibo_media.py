from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from weibo_media import best_mp4
from ci_fetch_generic import FORMAT


def playback(width,height,label='video',mime='video/mp4'):
    return dict(play_info=dict(width=width,height=height,mime=mime,
        url='https://f.video.weibocdn.com/'+label+('.jpg' if mime=='image/jpeg' else '.mp4')))


def test_real_weibo_hd_shortcut_does_not_override_actual_1080p_playback():
    media=dict(stream_url_hd='https://f.video.weibocdn.com/480.mp4',
        playback_list=[playback(852,480,'480'),playback(1920,1080,'1080'),
            playback(1280,720,'720'),playback(3840,2160,'4k'),
            playback(1920,1080,'scrubber','image/jpeg')])
    assert best_mp4(media).endswith('/1080.mp4')


def test_portrait_1080p_is_not_misclassified_as_above_1080p():
    assert best_mp4(dict(playback_list=[playback(720,1280,'720'),playback(1080,1920,'1080')])).endswith('/1080.mp4')


def test_legacy_metadata_remains_available_but_images_and_bad_dimensions_do_not_win():
    assert best_mp4(dict(stream_url_hd='https://cdn.example/legacy.mp4')).endswith('legacy.mp4')
    assert best_mp4(dict(playback_list=[playback(1080,1920,'image','image/jpeg'),playback(0,0)]))==''
    assert best_mp4(dict(playback_list=[None,dict(play_info=None),dict(play_info=dict(url='javascript:bad'))]))==''


def test_installed_downloader_selects_real_full_hd_portrait_not_480p():
    yt=pytest.importorskip('yt_dlp')
    formats=[dict(format_id=str(w),width=w,height=h,url=f'https://media.example/{w}.mp4',
        ext='mp4',vcodec='h264',acodec='aac') for w,h in [(480,854),(720,1280),(1080,1920)]]
    with yt.YoutubeDL(dict(quiet=True,no_warnings=True,format=FORMAT,format_sort=['res:1080'])) as ydl:
        result=ydl.process_ie_result(dict(id='fixture',title='actual portrait selection',formats=formats),download=False)
    assert result['width']==1080 and result['height']==1920
