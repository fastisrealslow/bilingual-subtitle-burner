"""Exact social links are leads; only actual matching video metadata is admitted."""
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location('weibo_exact_test',
    Path(__file__).resolve().parents[1]/'linyuan/monitor_v2.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
MID = '5331066086756985'


def status(mid=MID, text='林园谈投资和股市', video=True):
    return dict(mid=mid, text_raw=text, user=dict(screen_name='实际发布者'),
        page_info=dict(media_info=dict(duration=600,
            stream_url_hd='https://cdn.example/video.mp4?Expires=123' if video else '')))


def fetch(monkeypatch, payload, urls=None):
    calls=[]
    def request(url, headers):
        calls.append(url)
        return json.dumps(payload)
    monkeypatch.setattr(m.WeiboVideoSource, '_visitor_session', lambda _: (request, 'token'))
    rows=m.WeiboVideoSource({'urls': urls or [f'https://weibo.com/2/detail/{MID}']}, {}).fetch(None)
    return rows,calls


def test_actual_api_title_author_media_are_used(monkeypatch):
    rows,calls=fetch(monkeypatch, {'data':status()})
    assert len(rows)==1 and rows[0]['author']=='实际发布者'
    assert rows[0]['title']=='林园谈投资和股市'
    assert json.loads(rows[0]['extra'])['duration']==600
    assert calls==['https://weibo.com/ajax/statuses/show?id='+MID]


def test_no_text_only_unrelated_or_mismatched_status_is_stock(monkeypatch):
    for data in [status(video=False),status(text='虎林园动物园投资'),
                 status(text='其他人谈股市'),status(mid='5331066086756999')]:
        assert fetch(monkeypatch, data)[0]==[]


def test_invalid_hosts_and_schemes_never_request(monkeypatch):
    rows,calls=fetch(monkeypatch,status(),[
        f'http://weibo.com/{MID}',f'https://evil.example/{MID}',
        f'https://weibo.com.evil.example/{MID}',f'https://weibo.com/2/detail/no-id'])
    assert rows==[] and calls==[]


def test_duplicate_seed_requests_once(monkeypatch):
    rows,calls=fetch(monkeypatch,status(),[f'https://weibo.com/{MID}',f'https://m.weibo.cn/detail/{MID}'])
    assert len(rows)==1 and len(calls)==1


def test_bad_payload_is_rejected_without_leaking_signed_media(monkeypatch,capsys):
    assert fetch(monkeypatch, {'data':None,'error':'SECRET'})[0]==[]
    assert 'SECRET' not in capsys.readouterr().err


def test_direct_sources_run_before_expensive_discovery_and_are_registered():
    configs=json.loads((Path(__file__).resolve().parents[1]/'linyuan/monitor_v2_config.json').read_text())
    assert [c['type'] for c in configs[:2]]==['yicai_video','weibo_video']
    assert m.SOURCES['weibo_video'] is m.WeiboVideoSource
