"""Read-only seed census and live metadata research; never approves media."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3

import monitor_v2 as monitor

BASE = Path(__file__).resolve().parent
BVID = re.compile(r'^BV[0-9A-Za-z]{10}$')
NO_MEDIA = re.compile(r'纯音频|纯录音|会议录音|音频版|录音版|文字版|图文|张海辉|解读林园|点评林园')


def census(base=BASE):
    """Count unique parent videos, not collection parts or reference videos."""
    counts = {}
    for kind, filename, key in (
        ('douyin', 'douyin_seeds.json', 'urls'),
        ('haokan', 'haokan_seeds.json', 'vids'),
        ('netease', 'netease_seeds.json', 'vcodes'),
        ('yicai', 'yicai_seeds.json', 'ids'),
        ('weibo', 'weibo_seeds.json', 'urls'),
    ):
        data = json.loads((base / filename).read_text())
        values = data[key]
        if kind == 'weibo':
            values = [re.search(r'/(\d{10,25})/?$', value)[1] for value in values]
        counts[kind] = len(set(values))
    roots = set()
    for config in json.loads((base / 'monitor_v2_config.json').read_text()):
        if config['type'] not in {'bilibili_collection', 'bilibili_series'}:
            continue
        roots.update(s['bvid'] for s in config.get('seeds', []))
        path = base / config.get('seeds_file', 'bilibili_seeds.json')
        if config.get('seeds_file') and path.exists():
            roots.update(s['bvid'] for s in json.loads(path.read_text())['seeds'])
    counts['bilibili'] = len(roots)
    return dict(counts=counts, total=sum(counts.values()),
                reference_only=len(json.loads((base / 'up_videos.json').read_text())),
                counting_policy='unique_parent_video_id_not_parts_or_search_queries')


def catalog_candidates(db=BASE / 'monitor_v2.db'):
    from fc import index as fc
    candidates = {}
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for record in conn.execute('SELECT * FROM items ORDER BY publish_time DESC'):
            row = dict(record)
            if row['source'] not in {'bilibili_search', 'reference_origin_search', 'bilibili_collection'}:
                continue
            extra = json.loads(row['extra'] or '{}')
            bvid = extra.get('bvid', '')
            if not BVID.fullmatch(bvid) or bvid in candidates:
                continue
            if row['author'] in {'', '园园滚雪球', '园来滚雪球', '账号已注销', '已注销'}:
                continue
            if not 120 <= monitor.duration_seconds(extra.get('duration')) <= 5400:
                continue
            title = row['title'] or ''
            if not fc.item_has_target_speaker(row, extra) or fc.NOISE.search(title) or NO_MEDIA.search(title):
                continue
            if fc.CLIP_TITLE_PAT.search(title) and not fc.FULL_TITLE_PAT.search(title):
                continue
            if extra.get('direct_dispatch') is False or extra.get('source_role') == 'reference':
                continue
            candidates[bvid] = dict(bvid=bvid, discovery='historical_catalog_revalidated',
                catalog_id=row['id'], catalog_title=title, catalog_author=row['author'])
    return list(candidates.values())


def verify_seed(candidate, fetch=monitor.http_get):
    bvid = candidate['bvid']
    try:
        payload = json.loads(fetch('https://api.bilibili.com/x/web-interface/view?bvid=' + bvid,
                                  referer='https://www.bilibili.com/', timeout=12))
        data = payload.get('data')
        if payload.get('code') != 0 or not isinstance(data, dict) or data.get('bvid') != bvid:
            raise ValueError('exact video metadata unavailable')
        title = data.get('title') or ''
        author = (data.get('owner') or {}).get('name') or ''
        if '林园' not in title or monitor.YicaiVideoSource.NOISE.search(title) or NO_MEDIA.search(title):
            raise ValueError('unrelated or non-live title')
        if author in {'', '园园滚雪球', '园来滚雪球'}:
            raise ValueError('unknown, reference-only or self publisher')
        pages = [dict(page=int(p['page']), cid=int(p['cid']),
                      duration=monitor.duration_seconds(p.get('duration')), title=p.get('part', ''))
                 for p in data.get('pages') or []
                 if 120 <= monitor.duration_seconds(p.get('duration')) <= 5400
                 and not NO_MEDIA.search(p.get('part') or '')]
        if not pages or any(p['page'] < 1 or p['cid'] < 1 for p in pages):
            raise ValueError('no eligible real video pages')
        return dict(candidate, status='verified_metadata', title=title, observed_author=author,
            owner_mid=(data.get('owner') or {}).get('mid'),
            url='https://www.bilibili.com/video/' + bvid,
            published_at=monitor.source_publish_time(data.get('pubdate')),
            metadata_provenance='bilibili_view_exact_id_author_pages',
            checked_at=datetime.now(timezone.utc).isoformat(), pages=pages,
            media_verified=False, editorial_approved=False, rights_status='not_determined',
            origin_role='repost_or_lead')
    except Exception as exc:
        return dict(**candidate, status='not_verified', error=type(exc).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--census', action='store_true')
    parser.add_argument('--web-leads', type=Path)
    parser.add_argument('--workers', type=int, default=3)
    args = parser.parse_args()
    if args.census:
        print(json.dumps(census(), ensure_ascii=False))
        return
    candidates = catalog_candidates()
    if args.web_leads:
        web = json.loads(args.web_leads.read_text())
        candidates = list({c['bvid']: c for c in candidates + web}.values())
    report = dict(baseline=census(), counting_policy='distinct_parent_bvid',
                  accepted_mp4_count=0, production_dispatch=False)
    if args.verify:
        with ThreadPoolExecutor(max_workers=min(4, max(1, args.workers))) as executor:
            rows = list(executor.map(verify_seed, candidates))
        report.update(verified=[r for r in rows if r['status'] == 'verified_metadata'],
                      rejected=[r for r in rows if r['status'] != 'verified_metadata'])
    else:
        report['candidates'] = candidates
    print(json.dumps(report, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
