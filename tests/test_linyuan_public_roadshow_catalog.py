import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
import monitor_v2 as M
from public_roadshow_catalog import CATALOGUE_URL, parse_catalogue


def anchor(href, title='林园访谈', day='2026.10.15'):
    return f'<a href="{href}"><div>{title}</div><span>主 讲：</span>林园<span>时 间：</span>{day}</a>'


def test_actual_publisher_catalogue_is_metadata_and_not_production_supply():
    rows = parse_catalogue((ROOT / 'tests/fixtures/linyuan_simuwang_public_catalog.html').read_text())
    assert len(rows) == len({r['url'] for r in rows}) == 32
    extras = [json.loads(r['extra']) for r in rows]
    assert sum(e['vip_label_present'] for e in extras) == 3
    assert rows[0]['title'] == '看好 AI 大时代，但我不会投个股'
    assert rows[0]['publish_time'] == '2026-10-15'
    for e in extras:
        assert e['publisher_identity_verified'] is True
        assert e['source_role'] == 'reference'
        for key in ('has_video', 'direct_dispatch', 'recording_date_verified',
                    'media_access_verified', 'media_quality_approved'):
            assert e[key] is False
        assert not any(key in e for key in ('duration', 'duration_sec', 'mp4_url', 'speech_date'))


def test_canonical_url_dedup_invalid_dates_and_wrong_hosts():
    h = anchor('/roadshow/1.html?ref=tracking') + anchor('/roadshow/1.html')
    h += anchor('https://evil.example/roadshow/2.html')
    h += anchor('https://ly.simuwang.com.evil.example/roadshow/3.html')
    h += anchor('/roadshow/4.html', day='2026.02.30')
    h += anchor('/roadshow/5.html', day='日期未定')
    h += anchor('/roadshow/not-a-number.html')
    rows = parse_catalogue(h)
    assert [r['url'] for r in rows] == ['https://ly.simuwang.com/roadshow/1.html']
    assert len(parse_catalogue(''.join(anchor(f'/roadshow/{i}.html') for i in range(100)))) == 64


def test_source_makes_one_unauthenticated_public_get_and_preserves_clock(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return anchor('/roadshow/1.html', title='VIP 专享 林园访谈')
    monkeypatch.setattr(M, 'http_get', get)
    state = {'public_roadshow_catalog': {'last_fetch': 123}}
    source = M.SOURCES['public_roadshow_catalog']({}, state)
    rows = source.fetch(None)
    assert calls == [(CATALOGUE_URL, {'timeout': 25})]
    assert source.min_interval == 3600 and source.state['last_fetch'] == 123
    assert rows[0]['title'] == '林园访谈'
    assert json.loads(rows[0]['extra'])['vip_label_present'] is True
