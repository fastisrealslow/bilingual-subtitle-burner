import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
from seed_expansion import build_report


def test_candidates_keep_real_page_ids_without_admitting_known_or_audio(tmp_path):
    db = tmp_path / 'catalog.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE items(id TEXT,extra TEXT)')
        conn.execute('INSERT INTO items VALUES(?,?)', ('other-id', json.dumps({'cid': 12})))
    before = db.read_bytes()
    seed = {'bvid': 'BV1uN4y1H7kS', 'title': 'collection'}
    pages = [dict(page=n, cid=10+n, duration=d, part=t) for n, d, t in
             [(1, 300, 'interview'), (2, 400, 'known cid'), (3, 119, 'short'),
              (4, 5401, 'long'), (5, 300, '会议录音'), (6, 5400, 'interview')]]
    report = build_report({'seeds': [seed, seed]}, db, lambda _: pages)
    assert report['seed_count'] == 1
    assert report['counts'] == {'new_metadata_candidate': 2, 'already_cataloged': 1,
        'outside_duration_limit': 2, 'audio_only_title': 1}
    assert [c['page'] for c in report['candidates']] == [1, 6]
    assert report['candidates'][1]['url'].endswith('?p=6')
    assert all(not c['direct_dispatch'] and not c['media_verified'] for c in report['pages'])
    assert report['accepted_mp4_count'] == 0
    assert db.read_bytes() == before


def test_api_failure_remains_unknown_and_other_seeds_continue(tmp_path):
    db = tmp_path / 'catalog.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE items(id TEXT,extra TEXT)')
    seeds = [{'bvid': b, 'title': b} for b in ['BV1uN4y1H7kS', 'BV1c54y1A7Bc']]
    def fetch(bvid):
        if bvid == seeds[0]['bvid']:
            raise TimeoutError('metadata timeout')
        return [dict(page=2, cid=999, duration=300, part='访谈')]
    report = build_report({'seeds': seeds}, db, fetch)
    assert report['status'] == 'partial'
    assert report['errors'][0]['status'] == 'unknown'
    assert len(report['candidates']) == 1
