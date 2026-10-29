"""Grow the verified source catalogue across independent providers, not clip stock."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

import monitor_v2 as monitor
import seed_growth as bilibili
from seed_campaign import BASE, NO_MEDIA, census
from seed_scan import rotating_batch
from source_catalogue_duration import eligible_duration

TARGET = 5000
WEIBO_LIMIT = 4


class SeedMetadataRejected(ValueError):
    def __init__(self, checks):
        self.checks = checks
        super().__init__('Metadata checks failed')


def held_programme_hint(text):
    """A new route must not release the unresolved 15 October roadshow."""
    if re.search(r'20\d{2}[-./年]', text) and not re.search(r'2026[-./年]', text):
        return False
    return bool(re.search(r'(?:2026[-./年])?10[-./月]15(?:日)?(?!\d)', text))


def video_path_hash(url):
    path = urlsplit(str(url or '')).path
    return hashlib.sha256(path.encode()).hexdigest() if path and path != '/' else None


def weibo_leads(base):
    """Cached search rows are discovery only; forwarding mentions are not identity."""
    from fc import index as fc
    data = json.loads((base / 'weibo_seeds.json').read_text())
    existing = {u.rstrip('/').rsplit('/', 1)[-1] for u in data['urls']}
    research = {str(v) for v in data.get('research_only_mids', [])}
    leads = {}
    with sqlite3.connect((base / 'monitor_v2.db').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute("SELECT * FROM items WHERE source IN ('weibo_search','weibo_video')"):
            extra = json.loads(row['extra'] or '{}')
            mid = str(extra.get('mid') or '')
            title = row['title'] or ''
            # Don't turn a reference in a repost chain into a new source of
            # the guest's footage. The fresh exact lookup still checks again.
            own_text = title.split('//@', 1)[0]
            duration = monitor.duration_seconds(extra.get('duration'))
            if (not re.fullmatch(r'\d{10,25}', mid) or mid in existing | research
                    or not extra.get('has_video') or not eligible_duration(duration)
                    or not fc.title_has_target_speaker(own_text)
                    or fc.NOISE.search(title) or fc.AI_NOISE.search(title) or NO_MEDIA.search(title)
                    or held_programme_hint(title)
                    or extra.get('source_role') in {'reference', 'catalog_only'}
                    or extra.get('direct_dispatch') is False or extra.get('reuse_hold_reason')
                    or row['author'] in {'', '园园滚雪球', '园来滚雪球'}):
                continue
            leads[mid] = dict(mid=mid, title=title, duration=duration, author=row['author'])
    # Long interviews first, then shorter complete material; never claim these
    # metadata durations describe decoded/approved video.
    return sorted(leads.values(), key=lambda r: (not 1200 <= r['duration'] <= 5400, -r['duration'], r['mid']))


def verify_weibo(lead, request, xsrf):
    from fc import index as fc
    from weibo_media import best_mp4
    mid = lead['mid']
    result = json.loads(request('https://weibo.com/ajax/statuses/show?id=' + mid,
        {'Accept': 'application/json', 'X-XSRF-TOKEN': xsrf, 'Referer': 'https://weibo.com/'}))
    row = result.get('data', result)
    if not isinstance(row, dict) or str(row.get('mid') or row.get('id')) != mid:
        raise ValueError('Exact public post identity unavailable')
    if row.get('retweeted_status'):
        raise ValueError('Repost is not a distinct video source')
    text = re.sub(r'<[^>]+>', '', row.get('text_raw') or row.get('text') or '')
    own_text = text.split('//@', 1)[0]
    author = (row.get('user') or {}).get('screen_name')
    page = row.get('page_info') or {}
    media = page.get('media_info') or {}
    duration = monitor.duration_seconds(media.get('duration'))
    media_id = str(page.get('object_id') or media.get('object_id') or '')
    checks = dict(known_author=bool(author and author not in {'园园滚雪球','园来滚雪球'}),
        speaker_title=fc.title_has_target_speaker(own_text),
        relevant_live_title=not bool(fc.NOISE.search(text) or fc.AI_NOISE.search(text) or NO_MEDIA.search(text)),
        programme_not_held=not held_programme_hint(text),eligible_duration=eligible_duration(duration),
        public_video_present=bool(best_mp4(media)),stable_video_identity=bool(re.fullmatch(r'\d{4}:\d{10,25}',media_id)))
    if not all(checks.values()):
        raise SeedMetadataRejected([key for key, passed in checks.items() if not passed])
    # Retain only stable identity, not expiring media URLs or visitor tokens.
    from source_admission_guard import restriction
    url = 'https://m.weibo.cn/detail/' + mid
    if restriction(url, [lead]):
        raise ValueError('Known source restriction remains closed')
    return dict(mid=mid, url=url, title=monitor.weibo_discovery_title(text)[:300],
        author=author, source_parent_id='weibo:' + media_id, duration_sec=duration,
        video_identity_sha256=video_path_hash(best_mp4(media)),
        metadata_provenance='weibo_public_exact_post_video_identity',
        checked_at=datetime.now(timezone.utc).isoformat(), media_verified=False,
        editorial_approved=False, rights_status='not_determined')


def grow_weibo(base=BASE, *, target=TARGET, limit=WEIBO_LIMIT, session=None):
    from batch_delivery import write_json
    base = Path(base)
    before = census(base)
    report = dict(provider='weibo', target=target, before=before['total'], added_count=0,
        accepted_mp4_count=0, rights_approved_count=0, production_dispatch=False, errors=[])
    if before['total'] >= target:
        return report
    path = base / '.automation/seed_pool_growth_scan.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    if state.get('weibo_cooldown_until', 0) > now:
        report['paused_until'] = state['weibo_cooldown_until']
        return report
    data = json.loads((base / 'weibo_seeds.json').read_text())
    evidence = data.setdefault('verified_sources', [])
    parent_ids = {row['source_parent_id'] for row in evidence if row.get('source_parent_id')}
    ids = {u.rstrip('/').rsplit('/', 1)[-1] for u in data['urls']}
    seen_media = {r['video_identity_sha256'] for r in evidence if r.get('video_identity_sha256')}
    with sqlite3.connect((base / 'monitor_v2.db').resolve().as_uri() + '?mode=ro', uri=True) as conn:
        for (encoded,) in conn.execute("SELECT extra FROM items WHERE source IN ('weibo_search','weibo_video')"):
            extra = json.loads(encoded or '{}')
            if str(extra.get('mid')) in ids and extra.get('video_url'):
                seen_media.add(video_path_hash(extra['video_url']))
    leads = weibo_leads(base)
    report['discovery_leads'] = len(leads)
    if not leads:
        return report
    try:
        request, xsrf = (session or monitor.WeiboSearchSource({}, {})._visitor_session)()
    except Exception as exc:
        state['weibo_cooldown_until'] = now + 3600
        write_json(path, state)
        report['errors'].append(dict(stage='public_session', error=type(exc).__name__))
        return report
    deadline = time.monotonic() + 90
    for lead in rotating_batch('weibo_parents', leads, path, min(WEIBO_LIMIT, max(1, limit)),
                               key=lambda r: r['mid'], state=state):
        if time.monotonic() >= deadline or census(base)['total'] >= target:
            break
        try:
            row = verify_weibo(lead, request, xsrf)
            if (row['mid'] in ids or row['source_parent_id'] in parent_ids
                    or row['video_identity_sha256'] in seen_media):
                report.setdefault('duplicate_video_count', 0)
                report['duplicate_video_count'] += 1
                continue
            data['urls'].append(row['url'])
            evidence.append(row)
            ids.add(row['mid']); parent_ids.add(row['source_parent_id'])
            seen_media.add(row['video_identity_sha256'])
            data['updated_at'] = datetime.now(timezone.utc).date().isoformat()
            # Checkpoint actual verified identities before proceeding. A timeout
            # or failure in one provider cannot erase another provider's work.
            write_json(base / 'weibo_seeds.json', data)
            report['added_count'] += 1
            report.setdefault('added_mids', []).append(row['mid'])
        except Exception as exc:
            code = getattr(exc, 'code', None)
            report['errors'].append(dict(stage='exact_video', mid=lead['mid'], error=type(exc).__name__,
                                         **({'checks': exc.checks} if isinstance(exc, SeedMetadataRejected) else {}),
                                         **({'http_status': code} if type(code) is int else {})))
            if code in {403, 412, 429} or not isinstance(exc, ValueError):
                state['weibo_cooldown_until'] = time.time() + 3600
                write_json(path, state)
                break
        time.sleep(.5)
    report['after'] = census(base)['total']
    return report


def run(base=BASE, *, target=TARGET, limit=48):
    """Provider cooldowns stay independent; the six-worker producer is untouched."""
    base = Path(base)
    before = census(base)
    providers = {}
    for name, call in (
        ('bilibili', lambda: bilibili.run(base, target=target, limit=limit, replenish_long=True)),
        ('weibo', lambda: grow_weibo(base, target=target)),
    ):
        try:
            providers[name] = call()
        except Exception as exc:
            providers[name] = dict(added_count=0, error=type(exc).__name__)
        if name == 'bilibili':
            from batch_delivery import write_json
            write_json(base / '.automation/seed_growth_report.json', providers[name])
    after = census(base)
    return dict(target=target, baseline=before, after=after, added_count=after['total']-before['total'],
        providers=providers, accepted_mp4_count=0, rights_approved_count=0, production_dispatch=False,
        remaining_to_target=max(0, target-after['total']),
        counting_policy='unique_parent_video_ids_not_parts_queries_or_accepted_stock')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=int, default=TARGET)
    parser.add_argument('--limit', type=int, default=48)
    args = parser.parse_args()
    if not 1 <= args.target <= TARGET or not 1 <= args.limit <= 64:
        parser.error('bounded target/limit required')
    from batch_delivery import write_json
    report = run(target=args.target, limit=args.limit)
    write_json(BASE / '.automation/seed_pool_growth_report.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
