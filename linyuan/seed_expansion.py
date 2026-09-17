"""Read-only seed discovery: no production DB writes, FC, ASR, or publishing."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from urllib.request import Request, urlopen

BASE = Path(__file__).resolve().parent


def known_media(db):
    ids, cids = set(), set()
    # Fail visibly if the catalog is unavailable; never claim everything is new.
    with sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True) as conn:
        for item_id, raw in conn.execute('SELECT id, extra FROM items'):
            ids.add(item_id)
            extra = json.loads(raw or '{}')
            if extra.get('cid'):
                cids.add(str(extra['cid']))
    return ids, cids


def page_list(bvid):
    request = Request('https://api.bilibili.com/x/player/pagelist?bvid=' + bvid,
                      headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})
    with urlopen(request, timeout=15) as response:
        data = json.load(response)
    if data.get('code') != 0 or not isinstance(data.get('data'), list) or not data['data']:
        raise ValueError('No verified page list: ' + str(data.get('code')))
    return data['data']


def build_report(manifest, db, fetch=page_list):
    known_ids, known_cids = known_media(db)
    seen_ids, seen_cids = set(), set()
    rows, errors, seeds_seen = [], [], set()
    for seed in manifest['seeds']:
        bvid = seed['bvid']
        if not re.fullmatch(r'BV[0-9A-Za-z]{10}', bvid):
            raise ValueError('Invalid bvid: ' + bvid)
        if bvid in seeds_seen:
            continue
        seeds_seen.add(bvid)
        try:
            pages = fetch(bvid)
            staged = []
            for part in pages:
                number, cid, duration = int(part['page']), int(part['cid']), int(part['duration'])
                if number < 1 or cid < 1 or duration < 0:
                    raise ValueError('Invalid page metadata')
                item_id = 'bilibili_search:' + bvid + (f':p{number}' if number > 1 else '')
                staged.append(dict(id=item_id, bvid=bvid, page=number, cid=cid,
                    title=part.get('part', ''), duration_seconds=duration,
                    url='https://www.bilibili.com/video/' + bvid + (f'?p={number}' if number > 1 else ''),
                    collection_title=seed['title'], origin_role='repost_or_lead',
                    direct_dispatch=False, media_verified=False))
            for row in staged:
                item_id, cid = row['id'], str(row['cid'])
                if item_id in seen_ids or cid in seen_cids:
                    status = 'duplicate_in_pool'
                elif item_id in known_ids or cid in known_cids:
                    status = 'already_cataloged'
                elif not 120 <= row['duration_seconds'] <= 5400:
                    status = 'outside_duration_limit'
                elif '录音' in row['title'] or '纯音频' in row['title']:
                    status = 'audio_only_title'
                else:
                    status = 'new_metadata_candidate'
                seen_ids.add(item_id)
                seen_cids.add(cid)
                row['status'] = status
                rows.append(row)
        except Exception as exc:
            errors.append(dict(bvid=bvid, error=str(exc), status='unknown'))
    new = [r for r in rows if r['status'] == 'new_metadata_candidate']
    return dict(schema_version=1, checked_at=datetime.now(timezone.utc).isoformat(),
        status='partial' if errors else 'complete_metadata_check',
        manifest_sha256=hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        catalog_sha256=hashlib.sha256(Path(db).read_bytes()).hexdigest(),
        seed_count=len(seeds_seen), counts=dict(Counter(r['status'] for r in rows)),
        new_candidate_duration_seconds=sum(r['duration_seconds'] for r in new),
        accepted_mp4_count=0, production_dispatch=False,
        caveat='URL/CID dedup only. Reposts of the same event can remain. Download, content dedup, speaker, ASR, frame and final MP4 acceptance are still required. Fixed source100 is unchanged.',
        candidates=new, pages=rows, errors=errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=BASE / 'seed_expansion.json')
    parser.add_argument('--db', type=Path, default=BASE / 'monitor_v2.db')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = build_report(json.loads(args.manifest.read_text()), args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('status', 'seed_count', 'counts', 'errors')}, ensure_ascii=False))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
