import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from seed_scan import rotating_batch
import monitor_v2 as monitor


def test_more_than_the_old_limit_eventually_reaches_every_seed(tmp_path):
    path = tmp_path / 'scan.json'
    seeds = [str(i) for i in range(37)]
    visited = [seed for _ in range(4) for seed in rotating_batch('weibo', seeds, path, 12)]
    assert set(visited) == set(seeds)
    assert visited[:12] != visited[12:24]


def test_cursor_survives_interruption_and_deduplicates_before_batching(tmp_path):
    path = tmp_path / 'scan.json'
    generator = rotating_batch('channel', ['one', 'one', 'two', 'three'], path, 2)
    assert next(generator) == 'one'
    generator.close()
    assert list(rotating_batch('channel', ['one', 'one', 'two', 'three'], path, 2)) == ['two', 'three']
    assert json.loads(path.read_text())['channel']['seed_count'] == 3


def test_channels_have_independent_cursors_and_empty_batches_do_not_write(tmp_path):
    path = tmp_path / 'scan.json'
    assert list(rotating_batch('empty', [], path, 10)) == []
    assert not path.exists()
    assert list(rotating_batch('first', [1, 2, 3], path, 1)) == [1]
    assert list(rotating_batch('second', [1, 2, 3], path, 1)) == [1]
    assert list(rotating_batch('first', [1, 2, 3], path, 1)) == [2]


@pytest.mark.parametrize('content', ['[]', '{"x":{"cursor":-1}}', '{"x":{"cursor":true}}'])
def test_corrupt_scan_state_is_not_silently_reset(tmp_path, content):
    path = tmp_path / 'scan.json'
    path.write_text(content)
    with pytest.raises(ValueError):
        list(rotating_batch('x', [1], path, 1))


def test_source_deadline_keeps_already_fetched_items(monkeypatch):
    class Partial(monitor.Source):
        name = 'partial'
        def fetch(self, page):
            self.partial_items.append(dict(id='actual-video'))
            raise monitor.SourceDeadline()
    assert monitor._run_source(Partial, {}, {}, None) == [dict(id='actual-video')]


def test_search_challenge_is_not_a_successful_empty_search(monkeypatch):
    monkeypatch.setattr(monitor, 'http_get', lambda *a, **kw: '{"code":0,"data":{"v_voucher":"redacted"}}')
    with pytest.raises(RuntimeError, match='真实结果列表'):
        monitor.BilibiliSearchSource({}, {})._fetch_via_api('林园')


def test_weibo_rotates_past_twelve_without_guessing_titles(monkeypatch):
    ids = [str(5331066086756985 + i) for i in range(25)]
    calls = []
    def request(url, headers):
        mid = url.split('=')[-1]
        calls.append(mid)
        return json.dumps(dict(mid=mid, text_raw='林园谈投资', user=dict(screen_name='真实作者'),
            page_info=dict(media_info=dict(duration=600, stream_url_hd='https://cdn.example/real.mp4'))))
    monkeypatch.setattr(monitor.WeiboVideoSource, '_visitor_session', lambda _: (request, ''))
    source = monitor.WeiboVideoSource({'urls': ['https://m.weibo.cn/detail/' + mid for mid in ids]}, {})
    assert len(source.fetch(None)) == 12
    assert len(source.fetch(None)) == 12
    assert len(source.fetch(None)) == 12
    assert set(calls) == set(ids)
