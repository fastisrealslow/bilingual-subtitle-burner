"""Bounded, resumable seed growth. Metadata is never accepted media or rights."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
import time

import monitor_v2 as monitor
from seed_campaign import BASE, BVID, NO_MEDIA, census, verify_seed
from seed_scan import rotating_batch

QUERIES = ('林园 完整访谈', '林园 演讲', '林园 第一财经', '林园 腾讯财经',
           '林园 私募排排网', '林园 北大', '林园 春瑜会客厅', '林园 红周刊',
           '林园 央视', '林园 雪球嘉年华', '林园 财联社', '林园 凤凰论坛')


def existing_roots(base):
    roots = set()
    for config in json.loads((base / 'monitor_v2_config.json').read_text()):
        if config.get('type') not in {'bilibili_collection', 'bilibili_series'}:
            continue
        roots.update(s['bvid'] for s in config.get('seeds', []))
        if config.get('seeds_file'):
            roots.update(s['bvid'] for s in json.loads((base / config['seeds_file']).read_text())['seeds'])
    return roots


def catalog_leads(db):
    """Unapproved archive leads; includes historical programme titles, not rivals."""
    rows = {}
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute('SELECT * FROM items ORDER BY publish_time DESC'):
            if row['source'] not in {'bilibili_search', 'reference_origin_search',
                                      'bilibili_collection', 'bilibili_series'}:
                continue
            extra = json.loads(row['extra'] or '{}')
            bvid = extra.get('bvid', '')
            if (not BVID.fullmatch(bvid) or '林园' not in row['title']
                    or NO_MEDIA.search(row['title'])
                    or row['author'] in {'', '园园滚雪球', '园来滚雪球', '账号已注销', '已注销'}
                    or extra.get('source_role') == 'reference'):
                continue
            duration = monitor.duration_seconds(extra.get('duration'))
            if not 120 <= duration <= 5400:
                continue
            lead = dict(bvid=bvid, discovery='historical_catalog_revalidated',
                catalog_title=row['title'], catalog_author=row['author'], catalog_duration=duration)
            if duration > rows.get(bvid, {}).get('catalog_duration', 0):
                rows[bvid] = lead
    return sorted(rows.values(), key=lambda r: (-r['catalog_duration'], r['bvid']))


def admit_verified(seed_data, rows, existing, limit):
    """Exact parent dedup; never promote metadata into media/editorial approval."""
    added = []
    for row in rows:
        if len(added) >= max(0, limit):
            break
        bvid = row.get('bvid', '')
        if (row.get('status') != 'verified_metadata' or not BVID.fullmatch(bvid)
                or bvid in existing or NO_MEDIA.search(row.get('title') or '')
                or row.get('owner_mid') in {1700344493, 275211725}
                or row.get('observed_author') in {'', '园园滚雪球', '园来滚雪球', '账号已注销', '已注销'}):
            continue
        entry = {key: row[key] for key in ('bvid', 'title', 'observed_author', 'owner_mid',
            'url', 'published_at', 'metadata_provenance', 'checked_at', 'pages', 'no_reprint',
            'discovery', 'rights_status', 'origin_role') if key in row}
        from fc import index as fc
        if fc.NOISE.search(entry['title']) or fc.AI_NOISE.search(entry['title']):
            continue
        evidence = fc.title_has_target_speaker(entry['title'])
        dispatch = row.get('no_reprint') != 1 and evidence
        entry.update(media_verified=False, editorial_approved=False,
            target_speaker_title_evidence=evidence,
            source_role='mother_candidate' if dispatch else 'catalog_only',
            direct_dispatch=dispatch)
        # A title date is only an uploader clue. Do not infer filming from upload.
        import re
        match = re.search(r'(?<!\d)(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})(?:日)?(?!\d)', entry['title'])
        if match:
            from datetime import date
            try:
                entry['content_date_hint'] = date(*map(int, match.groups())).isoformat()
                entry['content_date_hint_provenance'] = 'uploader_title_unverified'
            except ValueError:
                pass
        seed_data['seeds'].append(entry)
        existing.add(bvid)
        added.append(entry)
    return added


def run(base=BASE, *, target=1000, limit=48, search=True, verify=verify_seed):
    deadline = time.monotonic() + 210
    before = census(base)
    roots = existing_roots(base)
    remaining = max(0, target - before['total'])
    report = dict(baseline=before, target=target, accepted_mp4_count=0,
                  rights_approved_count=0, production_dispatch=False, added_count=0,
                  errors=[], counting_policy='unique_parent_id_not_parts_or_stock')
    if remaining == 0:
        report['after'] = before
        return report
    state = base / '.automation/seed_growth_scan.json'
    leads = {r['bvid']: r for r in catalog_leads(base / 'monitor_v2.db') if r['bvid'] not in roots}
    if search:
        for query in rotating_batch('queries', QUERIES, state, 3):
            if time.monotonic() >= deadline:
                report['errors'].append(dict(stage='budget', error='time_budget_reached'))
                break
            try:
                for row in monitor.BilibiliSearchSource({'pages': 2}, {})._fetch_via_api(query):
                    bvid = row.get('bvid', '')
                    if (BVID.fullmatch(bvid) and bvid not in roots
                            and '林园' in row.get('title', '') and not NO_MEDIA.search(row['title'])
                            and row.get('up') not in {'', '园园滚雪球', '园来滚雪球'}
                            and 120 <= monitor.duration_seconds(row.get('duration')) <= 5400):
                        leads[bvid] = dict(bvid=bvid, discovery='normal_public_search',
                            catalog_title=row['title'], catalog_duration=row['duration'])
            except Exception as exc:
                report['errors'].append(dict(stage='search', error=type(exc).__name__))
    candidates = sorted(leads.values(), key=lambda r: (-r['catalog_duration'], r['bvid']))
    rows = []
    for candidate in rotating_batch('parents', candidates, state, min(64, max(1, limit)), key=lambda r: r['bvid']):
        if time.monotonic() >= deadline:
            report['errors'].append(dict(stage='budget', error='time_budget_reached'))
            break
        rows.append(verify(candidate))
        time.sleep(.5)
    data = json.loads((base / 'bilibili_seeds.json').read_text())
    added = admit_verified(data, rows, roots, min(remaining, limit))
    from batch_delivery import write_json
    if added:
        from datetime import datetime, timezone
        data['updated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        write_json(base / 'bilibili_seeds.json', data)
    report.update(added_count=len(added), added_bvids=[r['bvid'] for r in added],
        roles=dict(Counter(r['source_role'] for r in added)),
        new_long_parent_count=sum(any(p['duration'] >= 900 for p in r['pages']) for r in added),
        verification=dict(Counter(r['status'] for r in rows)), after=census(base))
    failed=[r for r in rows if r['status']!='verified_metadata']
    # Provider availability is not a media-quality rejection. Keep safe codes,
    # never response bodies, credentials, or a failed lead's rights approval.
    report.update(verification_errors=dict(Counter(r.get('error','unknown') for r in failed)),
        verification_http_status=dict(Counter(str(r['http_status']) for r in failed if 'http_status' in r)),
        verification_api_codes=dict(Counter(str(r['api_code']) for r in failed if 'api_code' in r)))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=int, default=1000)
    parser.add_argument('--limit', type=int, default=48)
    parser.add_argument('--output', type=Path, default=BASE / '.automation/seed_growth_report.json')
    args = parser.parse_args()
    if not 1 <= args.limit <= 64 or not 1 <= args.target <= 2000:
        parser.error('bounded target/limit required')
    report = run(target=args.target, limit=args.limit)
    from batch_delivery import write_json
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
