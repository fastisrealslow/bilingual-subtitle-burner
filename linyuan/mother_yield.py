"""Read-only observed long-mother yield. Jobs, links and approvals are not clips."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit, urlunsplit, parse_qs, urlencode

from editorial_policy import source_key
from production_diagnostics import failure_category

BASE = Path(__file__).resolve().parent
ACTIVE = {'running', 'queued', 'retry_queued', 'awaiting_validation', 'awaiting_run'}
TERMINAL = {'failed', 'interrupted', 'validation_failed', 'complete', 'ready', 'reserved', 'excluded'}
LIVE = {'live_video_card', 'stage_context', 'direct', 'crop', 'crop_delogo', 'delogo'}


def canonical(url):
    """Keep the exact Bilibili page; never expose expiring media signatures."""
    key = source_key(url)
    if key.startswith('BV'):
        return key
    parsed = urlsplit(str(url or ''))
    if parsed.hostname in {'weibo.com', 'www.weibo.com', 'm.weibo.cn'}:
        mid = re.search(r'/(\d{10,25})/?$', parsed.path)
        if mid:
            return 'https://m.weibo.cn/detail/' + mid[1]
    # A Haokan /v?vid=... (or embedded player ?vid=...) uses its query as
    # identity. Remove signatures/tracking, never collapse different videos.
    query = parse_qs(parsed.query)
    identity = {name: query[name][0] for name in ('vid', 'id', 'v', 'aid', 'bvid', 'cid', 'p', 'docid')
                if query.get(name)}
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(identity), ''))


def positive(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError):
        return None


def digest(value):
    return isinstance(value, str) and bool(re.fullmatch('[0-9a-f]{64}', value))


def duration_evidence(items, seeds=(), exact_pages=()):
    """Exact per-page clocks take precedence over aggregate search durations."""
    values = defaultdict(list)
    for item in items:
        extra = item.get('extra') or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        duration = positive(extra.get('duration'))
        key = canonical(item.get('url'))
        if duration and key:
            rank = 2 if extra.get('cid') and extra.get('page') else 1
            values[key].append((rank, duration, 'catalog_page' if rank == 2 else 'catalog_metadata'))
    for rank, rows in ((3, seeds), (4, exact_pages)):
        for row in rows:
            if rank == 4 and row.get('status') == 'verified_exact_metadata':
                duration = positive(row.get('duration'))
                key = canonical(row.get('source_key'))
                if duration and key:
                    values[key].append((rank, duration, 'live_exact_source_metadata'))
                continue
            if rank == 4 and row.get('status') != 'verified_exact_pages':
                continue
            if not re.fullmatch(r'BV[0-9A-Za-z]{10}', row.get('bvid', '')):
                continue
            for page in row.get('pages') or []:
                duration = positive(page.get('duration'))
                number = page.get('page')
                if duration and type(number) is int and number > 0 and page.get('cid'):
                    key = row['bvid'] + ':p' + str(number)
                    values[key].append((rank, duration, 'live_exact_page' if rank == 4 else 'verified_seed_page'))
    result = {}
    for key, evidence in values.items():
        rank = max(v[0] for v in evidence)
        best = [v for v in evidence if v[0] == rank]
        result[key] = dict(seconds=sorted({v[1] for v in best}), basis=best[0][2],
                           actual_media_duration_verified=False)
    return result


def summarize(state, inventory, snapshot, durations, *, days=7, long_seconds=900, as_of=None):
    end = float(snapshot['updated_at'] if as_of is None else as_of)
    start = end - days * 86400
    latest = {}
    for entry in state.get('dispatched') or []:
        slug = entry.get('slug')
        if slug and float(entry.get('ts') or 0) >= float(latest.get(slug, {}).get('ts') or 0):
            latest[slug] = entry
    tasks = {t['slug']: t for t in snapshot.get('tasks') or []}
    groups = defaultdict(list)
    for entry in latest.values():
        if start <= float(entry.get('ts') or 0) <= end:
            key = canonical(entry.get('source_url') or entry.get('asset_url'))
            if key:
                groups[key].append(entry)
    records = defaultdict(list)
    # Only independent source_supply inspection records, not producer meta flags.
    if digest(inventory.get('validation_sha256')) and inventory.get('quality_gate_version'):
        for record in inventory.get('artifacts') or []:
            if record.get('checked_at') and record['checked_at'] <= end:
                records[record['slug']].append(record)
    rows = []
    for key, entries in sorted(groups.items()):
        states, files, receipts, failures = [], {}, set(), []
        first_dispatch = min(float(e['ts']) for e in entries)
        for entry in entries:
            task = tasks.get(entry['slug'], {})
            current = (task.get('signature') or {}).get('ts') == entry.get('ts')
            states.append(task.get('status', 'unknown') if current else 'unknown')
            reason = entry.get('last_error') or task.get('detail')
            if entry.get('failed') or task.get('status') in {'failed', 'interrupted', 'validation_failed'}:
                failures.append(failure_category(reason))
            publication = (state.get('published') or {}).get(entry['slug']) or {}
            for part in publication.get('parts') or []:
                when = float(part.get('ts') or publication.get('ts') or 0)
                sha = (part.get('fingerprints') or {}).get('sha256')
                resolution = part.get('resolution') or publication.get('resolution') or {}
                mode = part.get('render_mode') or publication.get('render_mode')
                if (part.get('bvid') and part.get('status') == 'published'
                        and first_dispatch <= when <= end and digest(sha)
                        and digest(part.get('source_sha256')) and mode in LIVE
                        and min(resolution.get('width', 0), resolution.get('height', 0)) >= 480):
                    files[sha] = dict(sha256=sha, evidence='published_receipt', bvid=part['bvid'])
                    receipts.add(part['bvid'])
            for record in records[entry['slug']]:
                if record.get('run_id') != task.get('run_id') or record['checked_at'] < first_dispatch:
                    continue
                for part in record.get('parts') or []:
                    sha = part.get('sha256')
                    resolution = part.get('resolution') or {}
                    if (part.get('status') == 'verified' and digest(sha)
                            and digest(part.get('source_sha256')) and part.get('render_mode') in LIVE
                            and min(resolution.get('width', 0), resolution.get('height', 0)) >= 480):
                        files.setdefault(sha, dict(sha256=sha, evidence='independent_media_inspection'))
        observed = durations.get(key) or {}
        lengths = observed.get('seconds') or []
        length_class = ('unknown' if not lengths else 'long' if min(lengths) >= long_seconds
                        else 'short' if max(lengths) < long_seconds else 'conflicting')
        finished = bool(states) and all(s in TERMINAL for s in states)
        rows.append(dict(source_key=key, slugs=sorted(e['slug'] for e in entries),
            length_class=length_class, duration_evidence=observed, task_statuses=states,
            finished=finished, accepted_clip_count=len(files), accepted_files=list(files.values()),
            published_receipts=sorted(receipts), failure_categories=sorted(set(failures))))
    eligible = [r for r in rows if r['finished'] and r['length_class'] == 'long']
    unknown = [r for r in rows if r['finished'] and r['length_class'] in {'unknown', 'conflicting'}]
    successes = [r for r in eligible if r['accepted_clip_count']]
    all_long = [r for r in rows if r['length_class'] == 'long']
    all_unknown = [r for r in rows if r['length_class'] in {'unknown', 'conflicting'}]
    all_successes = [r for r in all_long if r['accepted_clip_count']]
    attempted_denominator = len(all_long) + len(all_unknown)
    attempted_fraction = (Fraction(len(all_successes), attempted_denominator)
                          if attempted_denominator else None)
    accepted = {p['sha256'] for r in eligible for p in r['accepted_files']}
    numerator, denominator = len(successes), len(eligible)
    fraction = Fraction(numerator, denominator) if denominator else None
    # Unclassified durations are never silently removed from the coverage audit.
    upper_unknown_successes = sum(bool(r['accepted_clip_count']) for r in unknown)
    lower = numerator / (denominator + len(unknown)) if denominator + len(unknown) else None
    upper = ((numerator + upper_unknown_successes) / (denominator + upper_unknown_successes)
             if denominator + upper_unknown_successes else None)
    failed = [r for r in eligible if not r['accepted_clip_count']]
    failures = Counter(category for r in failed for category in r['failure_categories'])
    return dict(version=1, metric='long_mother_to_at_least_one_accepted_live_clip',
        window=dict(start=datetime.fromtimestamp(start, timezone.utc).isoformat(),
                    end=datetime.fromtimestamp(end, timezone.utc).isoformat(), days=days),
        long_threshold_seconds=long_seconds, same_source_retries_count_once=True,
        unit='canonical_source_page_not_task_or_search_result',
        cohort=dict(unique_attempted_sources=len(rows), completed_known_long=denominator,
            successful_known_long=numerator, completed_duration_unknown=len(unknown),
            attempted_known_long=len(all_long), successful_attempted_known_long=len(all_successes),
            attempted_duration_unknown=len(all_unknown),
            pending_known_long=len(all_long)-len(eligible),
            pending_duration_unknown=len(all_unknown)-len(unknown),
            active_or_unresolved=sum(not r['finished'] for r in rows),
            classifications=dict(Counter(r['length_class'] for r in rows))),
        observed_success_rate=float(fraction) if fraction is not None else None,
        target_multiplier=2, target_rate=float(fraction * 2) if fraction is not None else None,
        observed_fraction=dict(numerator=numerator, denominator=denominator),
        double_current_fraction=dict(numerator=2*numerator, denominator=denominator),
        target_is_feasible=bool(fraction is not None and 0 < fraction * 2 <= 1),
        possible_all_completed_rate_bounds=dict(lower=lower, upper=upper),
        conservative_attempted_fraction=dict(numerator=len(all_successes), denominator=attempted_denominator),
        conservative_attempted_success_rate=float(attempted_fraction) if attempted_fraction is not None else None,
        accepted_unique_clip_count=len(accepted),
        clips_per_successful_mother=len(accepted)/numerator if numerator else None,
        clips_per_completed_long_mother=len(accepted)/denominator if denominator else None,
        failure_categories=dict(failures), failures_are_nonexclusive=True,
        scope_note='Metadata identifies intended long mothers, not decoded footage. Unknown lengths are reported separately. Complete/success jobs alone do not prove a clip. This is observed yield, not current usable inventory or rights clearance.',
        rows=rows)


def apply_frozen_goal(report, goal):
    """Do not move the target each time the rolling-window rate changes."""
    if (goal['metric'] != report['metric'] or goal['window_days'] != report['window']['days']
            or goal['long_threshold_seconds'] != report['long_threshold_seconds']):
        raise ValueError('Frozen goal and observation use different cohort definitions')
    baseline = goal['baseline_fraction']
    numerator, denominator = baseline['numerator'], baseline['denominator']
    if type(numerator) is not int or type(denominator) is not int or not 0 <= numerator <= denominator or denominator <= 0:
        raise ValueError('Invalid frozen baseline')
    mode = goal.get('target_mode', 'baseline_multiplier')
    if mode == 'absolute_rate':
        requested = goal['target_fraction']
        n, d = requested['numerator'], requested['denominator']
        if type(n) is not int or type(d) is not int or not 0 < n <= d or d <= 0:
            raise ValueError('Invalid absolute target fraction')
        target = Fraction(n, d)
        multiplier = None
    elif mode == 'baseline_multiplier':
        if goal['multiplier'] != 2:
            raise ValueError('Legacy target must be twice the frozen baseline')
        target = Fraction(numerator * 2, denominator)
        multiplier = 2
    else:
        raise ValueError('Unknown frozen target mode')
    feasible = 0 < target <= 1
    cohort=report['cohort']
    if all(key in cohort for key in ('attempted_known_long','attempted_duration_unknown',
                                     'successful_attempted_known_long')):
        cohort_size=cohort['attempted_known_long']+cohort['attempted_duration_unknown']
        verified_successes=cohort['successful_attempted_known_long']
        pending_classifications_known=True
    else:
        # Legacy reports cannot prove that active sources are short. Reserve
        # them all rather than make a target pass by reopening failed mothers.
        cohort_size=(cohort['completed_known_long']+cohort['completed_duration_unknown']
                     +cohort.get('active_or_unresolved',0))
        verified_successes=cohort['successful_known_long']
        pending_classifications_known=False
    if (type(cohort_size) is not int or type(verified_successes) is not int
            or not 0<=verified_successes<=cohort_size):
        raise ValueError('Invalid conservative attempted cohort')
    comparison=Fraction(verified_successes,cohort_size) if cohort_size else None
    required = math.ceil(target * cohort_size) if cohort_size and feasible else None
    return dict(report, target_rate=float(target), target_multiplier=multiplier, target_is_feasible=feasible,
        frozen_goal=dict(baseline_as_of=goal['baseline_as_of'], baseline_fraction=baseline,
            target_mode=mode, multiplier=multiplier,
            target_fraction=dict(numerator=target.numerator, denominator=target.denominator)),
        required_successful_mothers_at_current_cohort_size=required,
        goal_comparison_fraction=dict(numerator=verified_successes,denominator=cohort_size),
        goal_comparison_rate=float(comparison) if comparison is not None else None,
        pending_classifications_known=pending_classifications_known,
        target_achieved=bool(feasible and comparison is not None and comparison>=target),
        goal_comparison_basis='All attempted known-long and possibly-long sources remain in the denominator, including running retries; only independent verified clips count. Completed-only rate is descriptive, not target proof.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--days', type=int, default=7)
    parser.add_argument('--duration-evidence', type=Path)
    parser.add_argument('--source-duration-evidence', type=Path)
    parser.add_argument('--goal', type=Path, default=BASE/'mother_yield_target.json')
    args = parser.parse_args()
    if not 1 <= args.days <= 90:
        parser.error('days must be in [1,90]')
    state = json.loads((BASE/'.automation/fc_state.json').read_text())
    inventory = json.loads((BASE/'.automation/source_inventory.json').read_text())
    snapshot = json.loads((BASE.parent/'site/production_status.json').read_text())
    with sqlite3.connect((BASE/'monitor_v2.db').resolve().as_uri()+'?mode=ro', uri=True) as conn:
        items = [dict(url=r[0], extra=r[1]) for r in conn.execute('SELECT url, extra FROM items')]
    seeds = json.loads((BASE/'bilibili_seeds.json').read_text())['seeds']
    exact = json.loads(args.duration_evidence.read_text())['rows'] if args.duration_evidence else []
    if args.source_duration_evidence:
        exact += json.loads(args.source_duration_evidence.read_text())['rows']
    result = summarize(state, inventory, snapshot, duration_evidence(items, seeds, exact), days=args.days)
    if args.goal.exists():
        result = apply_frozen_goal(result, json.loads(args.goal.read_text()))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
