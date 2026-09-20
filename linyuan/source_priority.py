"""Bounded dispatch preference from observed source outcomes, never a new gate."""
from collections import defaultdict
from urllib.parse import urlsplit

from editorial_policy import source_key
from production_diagnostics import failure_category

WINDOW_SECONDS = 30 * 86400
MIN_OBSERVATIONS = 3


def family(candidate):
    author = str(candidate.get('author') or '').strip()
    url = candidate.get('page_url') or candidate.get('source_url') or ''
    host = (urlsplit(url).hostname or '').lower()
    for domain in ('bilibili.com', '163.com', 'douyin.com', 'weibo.com', 'qq.com', 'yicai.com', 'xueqiu.com'):
        if host == domain or host.endswith('.'+domain):
            host = domain
            break
    return (host, author) if host and author else None


def observed_priorities(state, now):
    latest = {}
    for entry in state.get('dispatched', []):
        url = entry.get('source_url') or entry.get('page_url')
        ts = float(entry.get('ts') or 0)
        if not url or not 0 <= now-ts <= WINDOW_SECONDS:
            continue
        key = source_key(url)
        if key not in latest or ts >= float(latest[key].get('ts') or 0):
            latest[key] = entry
    # Retries of one mother count once; service errors do not count as poor media.
    groups = defaultdict(lambda: dict(delivered_mothers=0, quality_rejected_mothers=0))
    for entry in latest.values():
        key = family(entry)
        if key is None:
            continue
        publication = state.get('published', {}).get(entry.get('slug')) or {}
        delivered = bool(publication.get('bvid') or any(
            p.get('bvid') and p.get('status') != 'skipped' for p in publication.get('parts', [])))
        if delivered:
            groups[key]['delivered_mothers'] += 1
        elif entry.get('failed') and failure_category(entry.get('last_error')) in {
                'identity', 'resolution', 'framing', 'selection'}:
            groups[key]['quality_rejected_mothers'] += 1
    result = {}
    for key, counts in groups.items():
        good, bad = counts['delivered_mothers'], counts['quality_rejected_mothers']
        observed = good+bad
        # Three observations minimum, smoothing, and a small bounded adjustment.
        # Unknown sources stay neutral and remain eligible for exploration.
        adjustment = round(12*(good-bad)/(observed+2), 2) if observed >= MIN_OBSERVATIONS else 0
        result[key] = dict(**counts, observed_mothers=observed, adjustment=adjustment,
                           window_days=30, policy='observed_source_priority_v1')
    return result
