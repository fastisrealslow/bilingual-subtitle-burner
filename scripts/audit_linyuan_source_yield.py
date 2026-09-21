#!/usr/bin/env python3
"""Read-only source yield audit; preserve cohort, platform and uploader denominators."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/benchmark-20260921/source-audit'
sys.path.insert(0, str(ROOT / 'linyuan'))
from production_diagnostics import failure_category, summarize
from simulate_sources import aggregate


def read(path):
    return json.loads(path.read_text())


def platform(sample):
    host = urlsplit(sample.get('source_url') or sample.get('url') or '').hostname or ''
    for domain, label in [('bilibili.com', 'B站'), ('weibo.cn', '微博'),
                          ('weibo.com', '微博'), ('yicai.com', '第一财经'),
                          ('qq.com', '腾讯'), ('163.com', '网易'), ('douyin.com', '抖音'),
                          ('haokan.baidu.com', '好看视频')]:
        if host == domain or host.endswith('.' + domain):
            return label
    return sample.get('source_platform') or host or '未知'


def grouped(rows, library, by_author=False):
    by_id = {r['id']: r for r in library}
    by_url = {r.get('url'): r for r in library if r.get('url')}
    groups = defaultdict(list)
    for r in rows:
        s = r['sample']
        item = by_id.get(s.get('key')) or by_url.get(s.get('source_url')) or {}
        author = s.get('author') or item.get('author') or '作者未记录'
        key = platform(s) + (' / ' + author if by_author else '')
        groups[key].append(r)
    result = []
    for key, values in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        count = Counter(r['status'] for r in values)
        reasons = Counter()
        for r in values:
            # One source may fail several candidate gates. Count it at most once
            # in each reason category; category totals are not disjoint.
            errors = [x.get('reason', '') for x in (r.get('batch') or {}).get('rejected', [])]
            if r.get('validation_error'):
                errors.append(r['validation_error'])
            if (r.get('source_quality') or {}).get('reason'):
                errors.append(r['source_quality']['reason'])
            reasons.update({failure_category(e) for e in errors if e})
        result.append(dict(source=key, total=len(values), passed=count['passed'],
            rejected=count['rejected'], unresolved=count['unresolved'],
            downloaded=sum(bool(r.get('source_sha256')) or
                (r.get('steps', {}).get('src') or {}).get('outcome') == 'success' for r in values),
            source_gate_passed=sum((r.get('source_quality') or {}).get('passed') is True for r in values),
            percent=round(100 * count['passed'] / len(values), 2),
            reasons=dict(reasons), ids=[r['sample']['id'] for r in values]))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot-ref', help='Read this Git commit into the isolated audit folder, never production state')
    args = parser.parse_args()
    provenance_path = OUT / 'snapshot.json'
    if args.snapshot_ref:
        sha = subprocess.check_output(['git', 'rev-parse', '--verify', args.snapshot_ref+'^{commit}'], cwd=ROOT, text=True).strip()
        OUT.mkdir(parents=True, exist_ok=True)
        files = {}
        for source in ('linyuan/dashboard/data.json', 'linyuan/.automation/fc_state.json',
                       'linyuan/.automation/source_research_report.json', 'site/production_status.json'):
            raw = subprocess.check_output(['git', 'show', sha+':'+source], cwd=ROOT)
            target = OUT / Path(source).name
            target.write_bytes(raw)
            files[target.name] = hashlib.sha256(raw).hexdigest()
        provenance_path.write_text(json.dumps(dict(commit=sha, sha256=files), indent=2)+'\n')
    provenance = read(provenance_path)
    for name, expected in provenance['sha256'].items():
        if hashlib.sha256((OUT/name).read_bytes()).hexdigest() != expected:
            raise ValueError('Source snapshot changed: '+name)
    library = read(OUT / 'data.json')
    batches = {}
    for variant in ('baseline', 'optimized'):
        folder = ROOT / 'output/baseline-comparison-20260921/results'
        reports = [read(p) for p in folder.glob(f'ab-report-{variant}-*/report.json')]
        manifest = read(ROOT / 'linyuan/simulations/source100-20260916.json')
        batches[variant] = aggregate(manifest, reports)
    for key, manifest, folder in (
        ('reference100', 'source100-20260916.json', 'output/reference100-20260921/results'),
        ('library20', 'library20-20260921.json', 'output/benchmark-20260921/library-35556200021'),
        ('candidate100', 'source100-20260916.json', 'output/candidate100-35565180877/results')):
        reports = [read(p) for p in (ROOT / folder).glob('simulation-report-*/report.json')]
        summary = aggregate(read(ROOT / 'linyuan/simulations' / manifest), reports)
        (ROOT / folder / 'local-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        batches[key] = summary
    cohorts = {}
    for key, summary in batches.items():
        rows = summary['samples']
        cohorts[key] = dict(total=summary['total'], passed=summary['passed'], rejected=summary['rejected'],
            unresolved=summary['unresolved'], missing=sum(r['stage'] == 'missing-report' for r in rows),
            tested_shas=sorted({r['tested_sha'] for r in rows if r.get('tested_sha')}),
            run_ids=sorted({str(r['run_id']) for r in rows if r.get('run_id')}),
            platforms=grouped(rows, library), authors=grouped(rows, library, True))
    # Same URL is not enough: download bytes can change between runs.
    baseline = {r['sample']['id']: r for r in batches['baseline']['samples']}
    for key in ('optimized', 'reference100', 'candidate100'):
        pairs = defaultdict(list)
        for row in batches[key]['samples']:
            ident = row['sample']['id']; old = baseline[ident]
            if not old.get('source_sha256') or not row.get('source_sha256'):
                kind = 'missing_source_hash'
            elif old['source_sha256'] != row['source_sha256']:
                kind = 'different_source_bytes'
            elif 'unresolved' in (old['status'], row['status']):
                kind = 'same_source_unresolved'
            else:
                kind = {('passed','passed'):'both_passed', ('rejected','passed'):'gain',
                        ('passed','rejected'):'loss', ('rejected','rejected'):'both_rejected'}[
                            old['status'], row['status']]
            pairs[kind].append(ident)
        cohorts[key]['paired_against_baseline'] = dict(pairs)
    research = read(OUT / 'source_research_report.json')
    jobs = list(research.get('jobs', {}).values())
    # GitHub's Linux monitor emits naive UTC created_at. This is discovery time,
    # not recording time; publish_time may be a repost date.
    new = [r for r in library if r.get('created_at', '') >= '2026-09-20T16:00:00']
    slim = lambda r: {k: r.get(k) for k in ('id', 'source', 'title', 'url', 'author', 'publish_time', 'created_at')}
    research_groups = {}
    for role in ('mother', 'reference'):
        selected = [r for r in jobs if r.get('role') == role]
        research_groups[role] = dict(total=len(selected), statuses=dict(Counter(r['status'] for r in selected)))
    sys.path.insert(0, str(ROOT / 'linyuan/fc'))
    import index as fc
    state = read(OUT / 'fc_state.json')
    saved_min = fc.MIN_DUR
    admission = {}
    try:
        for minimum in (120, 20):
            fc.MIN_DUR = minimum
            admission[str(minimum)] = fc.source_admission_audit(library, state)
    finally:
        fc.MIN_DUR = saved_min
    result = dict(checked_at=datetime.now(timezone.utc).isoformat(),
        snapshot_main_sha=provenance['commit'],
        library_records=len(library), library_sources=dict(Counter(r['source'] for r in library)),
        discovered_today=len(new), discovered_today_sources=dict(Counter(r['source'] for r in new)),
        new_records=[slim(r) for r in new], research_run=research['run_id'], research=research_groups,
        recent_media_attempts=sorted([dict(url=r['url'], role=r['role'], status=r['status'],
            checked_at=r.get('last_attempt_at'), evidence_run=r.get('evidence_run_id')) for r in jobs],
            key=lambda r:r.get('checked_at') or 0, reverse=True)[:8],
        admission=admission, cohorts=cohorts,
        production=summarize(read(OUT / 'production_status.json')),
        editorial_pass_rate=None, production_success_rate=None,
        limits=['自动出片不是编辑质量通过；历史任务状态不足以计算线上实际素材出片率。',
                '按平台和作者分组仍可能含同一母片的不同搬运版本，不等于独立内容数量。',
                '下载核验历史任务是定向样本，不代表该平台全体素材；源检查通过也不是最终出片。',
                '来源失败分类可多选，分类数不可相加当总失败数。'])
    (OUT / 'yield-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(dict(library=len(library), new=len(new),
        candidates={k:v['candidate_count'] for k,v in admission.items()},
        cohorts={k:{z:v[z] for z in ('total','passed','rejected','unresolved')} for k,v in cohorts.items()}), ensure_ascii=False))


if __name__ == '__main__':
    main()
