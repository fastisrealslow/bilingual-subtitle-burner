import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from bilibili_series import episode_items
import monitor_v2 as monitor


def episode(bvid='BV1NpBFYkEKx', author='实际作者', title='林园完整访谈', duration=600):
    return dict(bvid=bvid, title=title,
        arc=dict(title=title, author=dict(name=author), pubdate=1700000000),
        pages=[dict(page=1, cid=111, duration=duration), dict(page=2, cid=222, duration=duration)])


def data(*episodes):
    return dict(owner=dict(name='不能猜用的父作者'), ugc_season=dict(id=123, sections=[dict(episodes=list(episodes))]))


def test_series_uses_episode_author_cid_page_and_actual_source_date():
    rows = episode_items(data(episode()), monitor.duration_seconds, monitor.source_publish_time)
    assert len(rows) == 2 and rows[1]['id'].endswith(':p2')
    assert rows[1]['url'].endswith('?p=2')
    assert rows[1]['author'] == '实际作者'
    extra = json.loads(rows[1]['extra'])
    assert extra['cid'] == 222 and extra['duration'] == 600
    assert not extra['media_verified'] and not extra['editorial_approved']


def test_reference_author_unknown_author_audio_and_short_pages_are_not_stock():
    rows = episode_items(data(episode(author='园园滚雪球'), episode(author=''),
        episode(title='林园录音版'), episode(title='园林投资'), episode(duration=119)),
        monitor.duration_seconds, monitor.source_publish_time)
    assert rows == []


def test_series_deduplicates_the_same_video_page_across_sections():
    rows = episode_items(data(episode(), episode()), monitor.duration_seconds, monitor.source_publish_time)
    assert len(rows) == 2


def test_exact_series_parent_identity_must_be_verified(monkeypatch):
    monkeypatch.setattr(monitor, 'http_get', lambda *a, **kw: json.dumps(dict(code=0,
        data=dict(bvid='BV1wrong0000', **data(episode())))))
    source = monitor.BilibiliSeriesSource(dict(seeds=[dict(bvid='BV1NpBFYkEKx')]), {})
    assert source.fetch(None) == []
