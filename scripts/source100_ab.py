#!/usr/bin/env python3
"""Compare pinned revisions on the existing 100 inputs, retaining every failure."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'linyuan/simulations/baseline-comparison-20260921/plan.json'


def read(path):
    return json.loads(Path(path).read_text())


def inputs():
    plan = read(PLAN)
    path = ROOT / plan['manifest']
    if hashlib.sha256(path.read_bytes()).hexdigest() != plan['manifest_sha256']:
        raise ValueError('fixed source manifest changed')
    sys.path.insert(0, str(ROOT / 'linyuan'))
    from simulate_sources import validate_manifest
    manifest = read(path)
    validate_manifest(manifest)
    return plan, manifest


def matrix(plan, manifest):
    # Omit large archived metadata from the Actions matrix, not from the source list.
    fields = ('id', 'slug', 'source_url', 'source_platform', 'original_slug', 'recovery_run_id')
    return {'include': [dict({k: row.get(k, '') for k in fields}, variant=variant,
                            code_ref=plan[variant])
                        for row in manifest['samples']
                        for variant in ('baseline', 'optimized')]}


def compare(plan, manifest, reports):
    expected = {r['slug']: r for r in manifest['samples']}
    grouped = {v: {} for v in ('baseline', 'optimized')}
    run_ids = set()
    for variant, row in reports:
        slug = row['sample']['slug']
        if variant not in grouped or slug not in expected:
            raise ValueError('unknown variant or source')
        if slug in grouped[variant]:
            raise ValueError('duplicate report')
        if row['sample']['source_url'] != expected[slug]['source_url']:
            raise ValueError('source URL replaced')
        if row.get('tested_sha') != plan[variant] or row.get('diagnostic_subset'):
            raise ValueError('wrong revision or diagnostic subset')
        # A failed early step can lack a snapshot, but can never count as a pass.
        snapshot = row.get('publication_snapshot_sha256')
        if snapshot is not None and snapshot != plan['publication_sha256']:
            raise ValueError('publication snapshot changed')
        if row.get('status') not in ('passed', 'rejected', 'unresolved'):
            raise ValueError('unknown result status')
        if row['status'] == 'passed' and (not row.get('finals') or not snapshot
                                        or not row.get('source_sha256')
                                        or row.get('validation_error')):
            raise ValueError('pass without final acceptance evidence')
        if not row.get('run_id'):
            raise ValueError('missing run provenance')
        run_ids.add(str(row['run_id']))
        grouped[variant][slug] = row
    if len(run_ids) > 1:
        raise ValueError('cannot combine runs into a best-of score')
    summaries, paired = {}, []
    for variant, rows in grouped.items():
        counts = Counter(row['status'] for row in rows.values())
        missing = [r['id'] for slug, r in expected.items() if slug not in rows]
        counts['unresolved'] += len(missing)
        summaries[variant] = dict(tested_sha=plan[variant], total=len(expected),
                                  passed=counts['passed'], rejected=counts['rejected'],
                                  unresolved=counts['unresolved'], missing_ids=missing,
                                  stages=dict(Counter(r['stage'] for r in rows.values())))
    for slug, source in expected.items():
        a = grouped['baseline'].get(slug, {})
        b = grouped['optimized'].get(slug, {})
        equal = bool(a.get('source_sha256')) and a.get('source_sha256') == b.get('source_sha256')
        if not equal:
            outcome = 'unpaired_source_bytes'
        elif a['status'] == 'passed' and b['status'] != 'passed':
            outcome = 'regression'
        elif a['status'] != 'passed' and b['status'] == 'passed':
            outcome = 'recovery'
        elif a['status'] == b['status'] == 'passed':
            outcome = 'retained_pass'
        else:
            outcome = 'neither_passed'
        paired.append(dict(id=source['id'], outcome=outcome,
                           baseline_status=a.get('status', 'unresolved'),
                           optimized_status=b.get('status', 'unresolved'),
                           baseline_source_sha256=a.get('source_sha256'),
                           optimized_source_sha256=b.get('source_sha256')))
    return dict(plan=plan, variants=summaries, paired=paired,
                paired_counts=dict(Counter(r['outcome'] for r in paired)),
                actual_mp4_verification_required=True, publication_authorized=False,
                note='Report passes still require independent final MP4 and editorial review; unresolved inputs remain in both denominators.')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode', choices=['matrix', 'aggregate'])
    ap.add_argument('--reports', type=Path)
    args = ap.parse_args()
    plan, manifest = inputs()
    if args.mode == 'matrix':
        print(json.dumps(matrix(plan, manifest), ensure_ascii=False, separators=(',', ':')))
        return
    if args.reports is None:
        ap.error('--reports required for aggregate')
    rows = []
    for variant in ('baseline', 'optimized'):
        for directory in args.reports.glob('ab-report-' + variant + '-*'):
            for path in directory.rglob('report.json'):
                rows.append((variant, read(path)))
    result = compare(plan, manifest, rows)
    args.reports.mkdir(parents=True, exist_ok=True)
    (args.reports / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(result['variants'], ensure_ascii=False))


if __name__ == '__main__':
    main()
