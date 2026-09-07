"""Source-level observed yield. Links and unused time never become stock."""
from collections import Counter
from urllib.parse import urlparse

import editorial_policy as editorial


def family(url):
    key = editorial.source_key(url)
    return key.split(':p', 1)[0] if key.startswith('BV') else urlparse(url).netloc or 'unknown'


def union_seconds(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    return round(sum(b-a for a, b in merged), 3)


def audit(state, records, latest_entries, processed_parts, paused_slugs):
    mothers = {}
    by_slug = {e['slug']: e for e in latest_entries}

    def mother(url):
        key = editorial.source_key(url)
        return mothers.setdefault(key, dict(source_key=key, source_url=url,
            family=family(url), slugs=set(), source_sha256=set(), failures=[],
            verified_parts={}, used_ranges={}, receipts=set()))

    for entry in by_slug.values():
        url = entry.get('source_url') or entry.get('asset_url')
        if not url:
            continue
        row = mother(url)
        row['slugs'].add(entry['slug'])
        if entry.get('last_error'):
            row['failures'].append(dict(slug=entry['slug'],
                stage=entry.get('failure_stage') or 'unclassified',
                terminal=bool(entry.get('failed')), reason=entry['last_error']))

    for record in records:
        slug = record['slug']
        entry = by_slug.get(slug)
        if not entry or entry.get('failed') or slug in paused_slugs:
            continue
        url = entry.get('source_url') or record.get('source_url')
        if not url:
            continue
        row = mother(url)
        for part in record.get('parts', []):
            if part.get('source_sha256'):
                row['source_sha256'].add(part['source_sha256'])
            if part.get('status') != 'verified' or part['index'] in processed_parts(entry):
                continue
            # The inventory worker has already decoded and hashed these files.
            # Repeated artifacts must not inflate observed supply.
            key = part.get('sha256') or (slug, part['index'])
            row['verified_parts'][key] = part

    for slug, info in state.get('published', {}).items():
        url = info.get('source_url') or by_slug.get(slug, {}).get('source_url')
        if not url:
            continue
        row = mother(url)
        for part in info.get('parts') or ([info] if info.get('bvid') else []):
            if not part.get('bvid') or part.get('status') == 'skipped':
                continue
            row['receipts'].add(part['bvid'])
            digest = part.get('source_sha256') or info.get('source_sha256')
            spans = editorial.intervals(part.get('source_segments'))
            if digest:
                row['source_sha256'].add(digest)
            if spans:
                row['used_ranges'].setdefault(digest or 'unknown', []).extend(spans)

    result = []
    for row in mothers.values():
        parts = list(row.pop('verified_parts').values())
        row['verified_available'] = len(parts)
        row['verified_live'] = sum(p.get('render_mode') != 'audio_card' for p in parts)
        row['verified_seconds'] = round(sum(float(p.get('duration_sec') or 0) for p in parts), 3)
        row['used_source_seconds'] = sum(union_seconds(spans) for spans in row.pop('used_ranges').values())
        row['historical_receipts'] = len(row.pop('receipts'))
        row['slugs'] = sorted(row['slugs'])
        row['source_sha256'] = sorted(row['source_sha256'])
        result.append(row)
    groups = {}
    for row in result:
        group = groups.setdefault(row['family'], Counter())
        group['attempted_mothers'] += 1
        group['verified_available'] += row['verified_available']
        group['verified_seconds'] += row['verified_seconds']
        # A transient service error is not evidence against footage quality.
        for stage in {f['stage'] for f in row['failures'] if f['terminal']}:
            group['terminal_' + stage] += 1
    return dict(mothers=result, families={k: dict(v) for k, v in groups.items()})
