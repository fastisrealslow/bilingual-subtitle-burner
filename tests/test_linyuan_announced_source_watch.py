import json
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'linyuan'))
from source_research import available_search_queries


def test_announced_event_is_not_searched_as_a_recording_before_airing():
    before = datetime(2026, 10, 15, 7, tzinfo=timezone.utc).timestamp()
    after = datetime(2026, 10, 15, 10, tzinfo=timezone.utc).timestamp()
    assert not any(f == 'simuwang_2026_10_15' for f, _ in available_search_queries(before))
    assert any(f == 'simuwang_2026_10_15' for f, _ in available_search_queries(after))


def test_announcement_is_not_a_downloadable_mother_or_dispatchable_stock():
    catalog = json.loads((ROOT / 'linyuan/source_lineage.json').read_text())
    event = next(f for f in catalog['families'] if f['id'] == 'simuwang_2026_10_15')
    assert not event['official_urls'] and not event['mirror_urls']
    assert event['official_status'] == 'first_party_announcement_not_recording'
