import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from ci_fetch_ifeng import article_video


URL='https://original.ifeng.com/c/8wY5k7dWpWj'
VIDEO=dict(guid='4096ba6d-3561-4827-9297-7dc3d31d4325',duration=107,
    title='林园：我看好AI，但我不投资AI个股',
    playUrl='https://video19.ifeng.com/video09/2026/09/19/p7506905455520649435-102-104510.mp4')


def page(rows,sidebar=None):
    return '<script>var allData = '+json.dumps(dict(docData=dict(title=VIDEO['title'],newsTime='2026-09-19 16:28:17',
        contentData=dict(contentList=rows)),video=sidebar or {}),ensure_ascii=False)+';</script>'


def test_only_main_article_attachment_can_supply_video_and_date():
    r=article_video(page([dict(type='video',data=VIDEO)],dict(playUrl='https://video19.ifeng.com/unrelated.mp4')),URL)
    assert r['media_url']==VIDEO['playUrl']
    assert r['published_at']=='2026-09-19 16:28:17'
    assert r['recording_date'] is None
    with pytest.raises(ValueError,match='正文没有唯一'):
        article_video(page([dict(type='text',data='正文仅文字')],VIDEO),URL)
    with pytest.raises(ValueError,match='正文没有唯一'):
        article_video(page([dict(type='video',data=VIDEO)]*2),URL)


@pytest.mark.parametrize('url',['https://example.com/movie.mp4','https://video19.ifeng.com.example.com/movie.mp4','https://video19.ifeng.com/image.jpg'])
def test_wrong_media_hosts_and_images_are_rejected(url):
    wrong={**VIDEO,'playUrl':url}
    with pytest.raises(ValueError):article_video(page([dict(type='video',data=wrong)]),URL)


def test_article_javascript_is_never_executed():
    with pytest.raises(ValueError):article_video('var allData = evilFunction();',URL)


def test_generic_fetch_routes_article_and_retains_origin_proof(tmp_path,monkeypatch):
    import ci_fetch_ifeng as official
    import ci_fetch_generic as generic
    def fetch(url,path):
        assert url==URL
        path.write_bytes(b'validated media')
        path.with_suffix('.origin.json').write_text(json.dumps({'article':URL}))
        return path
    monkeypatch.setattr(official,'fetch',fetch)
    monkeypatch.setattr(generic.subprocess,'run',lambda *a,**k:pytest.fail('Must not use generic sidebar extraction'))
    report=tmp_path/'evidence/source_quality.json'
    video=generic.fetch(URL,tmp_path/'source',report)
    assert video.read_bytes()==b'validated media'
    assert json.loads((report.parent/'source_origin.json').read_text())['article']==URL
