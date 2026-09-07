"""Regression checks for broad, deduplicated Bilibili source discovery."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linyuan"))

import monitor_v2
import ci_fetch_bilibili as fetcher
import json
import pytest


def test_bilibili_search_combines_configured_keywords_without_duplicate_bvid(monkeypatch):
    source = monitor_v2.BilibiliSearchSource({
        "keyword": "林园",
        "keywords": ["林园", "林园 完整访谈", "林园"],
    }, {})
    calls = []

    def fake_fetch(keyword):
        calls.append(keyword)
        common = {"bvid": "BVcommon", "title": "林园完整访谈",
                  "view_count": 1, "up": "媒体"}
        if keyword == "林园":
            return [common]
        return [common, {"bvid": "BVfull", "title": "林园演讲完整版",
                         "view_count": 2, "up": "媒体"}]

    monkeypatch.setattr(source, "_fetch_via_api", fake_fetch)
    items = source.fetch(None)

    assert calls == ["林园", "林园 完整访谈"]
    assert [item["id"] for item in items] == [
        "bilibili_search:BVcommon", "bilibili_search:BVfull"]


def test_actual_duration_and_date_survive_search(monkeypatch):
    source = monitor_v2.BilibiliSearchSource({}, {})
    monkeypatch.setattr(source, '_fetch_via_api', lambda _: [dict(
        bvid='BVevent', title='林园演讲完整版', duration='1:02:30', pubdate=1700000000)])
    item = source.fetch(None)[0]
    assert json.loads(item['extra'])['duration'] == 3750
    assert item['publish_time'].startswith('2023-11-14')
    assert monitor_v2.source_publish_time(None) == ''
    assert monitor_v2.duration_seconds('03:02') == 182
    assert monitor_v2.duration_seconds('unknown') == 0


def test_collection_pages_keep_distinct_cids_and_filter_shorts(monkeypatch):
    monkeypatch.setattr(monitor_v2, 'http_get', lambda *a, **kw: json.dumps(dict(code=0, data=dict(
        owner=dict(name='原活动上传者'), title='林园访谈合集', pubdate=1700000000,
        pages=[dict(page=1,cid=111,duration=300,part='访谈第一期'),
               dict(page=2,cid=222,duration=400,part='访谈第二期'),
               dict(page=3,cid=333,duration=50,part='短宣传')]))))
    rows = monitor_v2.BilibiliCollectionSource(dict(seeds=[dict(bvid='BVseries')]), {}).fetch(None)
    assert len(rows) == 2
    assert rows[1]['url'].endswith('?p=2')
    assert rows[1]['id'] == 'bilibili_search:BVseries:p2'
    assert json.loads(rows[1]['extra'])['cid'] == 222
    assert json.loads(rows[1]['extra'])['source_role'] == 'mother_candidate'


def test_page_lookup_never_silently_downloads_first_episode():
    assert fetcher.requested_page('https://www.bilibili.com/video/BVseries?p=2') == 2
    pages = [dict(page=1,cid=111), dict(page=2,cid=222)]
    assert fetcher.page_cid(pages, 2) == 222
    with pytest.raises(ValueError):
        fetcher.page_cid(pages, 3)
    with pytest.raises(ValueError):
        fetcher.requested_page('https://www.bilibili.com/video/BVseries?p=0')
