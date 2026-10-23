import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import monitor_v2 as M
from ci_fetch_ifeng import article_video
from source_priority import publisher_priority

URL = 'https://v.ifeng.com/c/8WHpW9qUT1g'


def test_actual_official_document_enters_as_unapproved_full_source(monkeypatch):
    data = json.loads((ROOT / 'tests/fixtures/linyuan_ifeng_official_long_document.json').read_text())
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return 'var allData = ' + json.dumps(data, ensure_ascii=False) + ';'
    monkeypatch.setattr(M, 'http_get', get)
    source = M.SOURCES['ifeng_video']({'urls': [URL]}, {})
    rows = source.fetch(None)
    assert calls == [(URL, {'timeout': 25})]
    assert len(rows) == 1 and source.min_interval == 3600
    row = rows[0]; extra = json.loads(row['extra'])
    assert extra['duration'] == 1641
    assert extra['guid'] == 'f9b1bab4-287b-4888-a40c-d792d4e90db5'
    assert row['publish_time'] == '2024-01-12 16:08:45'
    assert extra['has_video'] and extra['source_role'] == 'mother_candidate'
    assert extra['origin_role'] == 'official_publisher'
    assert not extra['recording_date_verified']
    assert not extra['media_quality_approved'] and not extra['rights_clearance']
    assert not any(k in extra for k in ('speech_date', 'mp4_url', 'video_url'))
    assert publisher_priority(dict(page_url=URL, extra=extra)) == 30
    # The ordinary generic production downloader already uses this resolver.
    assert article_video(get(URL), URL)['declared_duration'] == 1641


def test_sidebars_and_non_interview_pages_do_not_become_materials(monkeypatch):
    data = json.loads((ROOT / 'tests/fixtures/linyuan_ifeng_official_long_document.json').read_text())
    doc = data['docData']; recommendation = dict(doc)
    doc.pop('videoPlayUrl')
    data['relatedVideos'] = [recommendation]
    monkeypatch.setattr(M, 'http_get', lambda *a, **k: 'var allData = ' + json.dumps(data))
    assert M.IfengVideoSource({'urls': [URL]}, {}).fetch(None) == []
    data['docData'] = recommendation
    recommendation['title'] = '林园酒店开业'
    assert M.IfengVideoSource({'urls': [URL]}, {}).fetch(None) == []


def test_config_preserves_existing_first_two_sources_and_adds_exact_original():
    config = json.loads((ROOT / 'linyuan/monitor_v2_config.json').read_text())
    assert [x['type'] for x in config[:2]] == ['yicai_video', 'weibo_video']
    assert next(x for x in config if x['type'] == 'ifeng_video')['urls'] == [URL]
