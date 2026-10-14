"""Bounded dispatch preference from observed source outcomes, never a new gate."""
from collections import defaultdict
import json
from urllib.parse import urlsplit
from datetime import datetime, timezone

from editorial_policy import source_key
from production_diagnostics import failure_category

WINDOW_SECONDS = 30 * 86400
MIN_OBSERVATIONS = 3


def freshness_evidence(candidate, now):
    """An upload timestamp is not a filming date; old context earns no news bonus.

    A title-date hint is conservative ordering evidence only, not verified
    recording metadata. Missing/future clocks stay neutral, never highest rank.
    """
    extra = candidate.get('extra') or {}
    if isinstance(extra, str):
        try:extra = json.loads(extra)
        except (TypeError, ValueError):extra = {}
    if not isinstance(extra, dict):extra = {}
    hint = extra.get('content_date_hint')
    from audience_copy import speech_date_evidence
    hint = speech_date_evidence(candidate, now)['date'] or hint
    if hint:
        try:
            date = datetime.fromisoformat(hint).replace(tzinfo=timezone.utc)
            if date.timestamp() < now - 168 * 3600:
                return dict(score=0, basis='historical_context_hint_not_new_recording',
                            content_date_verified=False, content_date_hint=hint)
        except (ValueError, TypeError):pass
    try:
        timestamp = datetime.fromisoformat(candidate.get('publish_time', '').replace('Z', '+00:00'))
        if timestamp.tzinfo is None:timestamp = timestamp.replace(tzinfo=timezone.utc)
        age = now - timestamp.timestamp()
        score = (15 if age <= 86400 else 10 if age <= 3 * 86400 else 5 if age <= 7 * 86400 else 0) if age >= 0 else 0
        return dict(score=score, basis='upload_time_only_recording_date_unknown',
                    content_date_verified=False)
    except (ValueError, TypeError, AttributeError):
        return dict(score=0, basis='date_unavailable', content_date_verified=False)


def recording_recency_rank(candidate, now):
    """Recent *source date clues* before unknown/old; never claim verification."""
    from audience_copy import speech_date_evidence
    hint = speech_date_evidence(candidate, now)['date']
    if not hint: return 1
    age = now - datetime.fromisoformat(hint).replace(tzinfo=timezone.utc).timestamp()
    return 0 if 0 <= age <= 30 * 86400 else 2

# Exact pages with first-party interview bylines, not a platform-wide whitelist.
# This ranks provenance only: it neither proves a reference match nor grants reuse.
VERIFIED_PRIMARY_PAGES = frozenset({
    'https://www.yicai.com/video/103329354.html',
    'https://www.yicai.com/video/102581577.html',
})


def publisher_priority(candidate):
    extra = candidate.get('extra') or {}
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (ValueError, TypeError):
            extra = {}
    if not isinstance(extra, dict):
        extra = {}
    url = candidate.get('page_url') or candidate.get('source_url') or candidate.get('url') or ''
    if url in VERIFIED_PRIMARY_PAGES and extra.get('origin_role') == 'official_publisher':
        return 30
    return 10


def family(candidate):
    author = str(candidate.get('author') or '').strip()
    # These labels represent unrelated/unknown uploaders, not one source.
    if author in {'账号已注销', '帐号已注销', '已注销', '未知', '未知作者',
                  '网易视频', '好看视频', '哔哩哔哩', 'bilibili'}:
        return None
    url = candidate.get('page_url') or candidate.get('source_url') or ''
    host = (urlsplit(url).hostname or '').lower()
    for domain in ('bilibili.com', '163.com', 'douyin.com', 'weibo.com', 'weibo.cn', 'qq.com', 'yicai.com', 'xueqiu.com'):
        if host == domain or host.endswith('.'+domain):
            host = 'weibo.com' if domain == 'weibo.cn' else domain
            break
    return (host, author) if host and author else None


def observed_priorities(state, now):
    latest = {}
    delivered_sources = set()
    for entry in state.get('dispatched', []):
        url = entry.get('source_url') or entry.get('page_url')
        ts = float(entry.get('ts') or 0)
        if not url or not 0 <= now-ts <= WINDOW_SECONDS:
            continue
        key = source_key(url)
        publication = state.get('published', {}).get(entry.get('slug')) or {}
        if publication.get('bvid') or any(
                p.get('bvid') and p.get('status') != 'skipped' for p in publication.get('parts', [])):
            delivered_sources.add(key)
        if key not in latest or ts >= float(latest[key].get('ts') or 0):
            latest[key] = entry
    # Retries of one mother count once; service errors do not count as poor media.
    groups = defaultdict(lambda: dict(delivered_mothers=0, quality_rejected_mothers=0))
    for entry in latest.values():
        key = family(entry)
        if key is None:
            continue
        delivered = source_key(entry.get('source_url') or entry.get('page_url')) in delivered_sources
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
