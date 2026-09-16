"""Isolated source-level benchmark. Never publishes or changes production state."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import random
from urllib.parse import urlparse
import re
import shutil
import subprocess
import sys
import traceback

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
MANIFEST = BASE / 'simulations/source100-20260916.json'


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sample(pool, count=100, seed='source100-20260916-v1'):
    from editorial_policy import source_key
    unique = {}
    for row in pool:
        url = row.get('page_url') or row.get('video_url')
        key = source_key(url)
        if key and key not in unique:
            unique[key] = dict(row, source_url=url, canonical_key=key)
    return random.Random(seed).sample([unique[k] for k in sorted(unique)], count)


def validate_manifest(manifest):
    from editorial_policy import source_key
    rows = manifest['samples']
    assert len(rows) == 100, 'Exactly 100 source samples are required'
    assert len({source_key(r['source_url']) for r in rows}) == 100
    assert len({r['id'] for r in rows}) == 100
    assert all(re.fullmatch(r'sim-0916-\d{3}-[a-f0-9]{6}', r['slug']) for r in rows)
    assert all(r['source_url'].startswith('https://') for r in rows)
    return rows


def build_manifest():
    sys.path.insert(0, str(BASE / 'fc'))
    import index as fc
    source = BASE / 'dashboard/data.json'
    state = read(BASE / '.automation/fc_state.json')
    audit = {}
    items = read(source)
    pool = fc.pick(items, dict(dispatched=[], rejected=[], published=state['published'],
                              pending_retry=[]), len(items), audit=audit)
    historical = {r.get('key'): r for r in state['dispatched']}
    rows = []
    for i, r in enumerate(sample(pool), 1):
        old = historical.get(r['key'], {})
        url = r['source_url']
        rows.append(dict(id=i, slug=f'sim-0916-{i:03d}-{hashlib.sha256(url.encode()).hexdigest()[:6]}',
                         source_url=url, canonical_key=r['canonical_key'], title=r['title'],
                         platform=r['source'], source_platform=fc.platform_of(r['source']),
                         video_url=r.get('video_url'), extra=r.get('extra'),
                         key=r['key'], duration=fc.candidate_duration(r),
                         original_slug=old.get('slug', ''),
                         recovery_run_id=str(old.get('source_check_run_id') or old.get('recovery_origin_run_id') or '')))
    manifest = dict(version=1, seed='source100-20260916-v1',
                    created_at=datetime.now(timezone.utc).isoformat(),
                    pool_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                    pool_count=len(pool), pool_platforms=dict(Counter(r['source'] for r in pool)),
                    eligibility_audit=audit, dashboard_sha256=digest(source),
                    sampling='Simple random sample of metadata-eligible canonical source URLs; prior failures/cooldowns ignored; published topic exclusions retained.',
                    success='At least one real final per source, producer gates and offline artifact validation passed; no posting.',
                    unresolved='Runtime/download failure, timeout, cancellation or missing evidence; never silently removed from denominator.',
                    publication_state='Same checkout snapshot for every job; no production writes.',
                    samples=rows)
    validate_manifest(manifest)
    write(MANIFEST, manifest)
    print(json.dumps(dict(pool=len(pool), samples=len(rows), platforms=dict(Counter(r['platform'] for r in rows))), ensure_ascii=False))


class FirstSuccess:
    """Run unchanged candidate production until one real successful return."""
    def __init__(self, original):
        self.original = original
        self.accepted = False
        self.calls = 0
        self.skipped = 0

    def __call__(self, *args, **kwargs):
        if self.accepted:
            self.skipped += 1
            return None
        self.calls += 1
        result = self.original(*args, **kwargs)
        if result is not None:
            self.accepted = True
        return result


def run_producer(args):
    import produce_cn as p
    slug = os.environ['RUN_SLUG']
    assert slug in {r['slug'] for r in validate_manifest(read(MANIFEST))}
    original = p.produce_part_with_budget
    limited = FirstSuccess(original)
    p.produce_part_with_budget = limited
    sys.argv = ['produce_cn.py', '--source', args.source, '--slug', slug,
                '--speaker', '林园', '--occasion', '', '--source-platform', os.environ['RUN_SOURCE_PLATFORM'],
                '--source-report', f'deliver/{slug}/_tmp/source_quality.json',
                '--split-highlights', '--require-live-video', '--prefer-live-video']
    result = dict(started_at=datetime.now(timezone.utc).isoformat())
    try:
        result['exit_code'] = p.main()
        return result['exit_code'] or 0
    except BaseException as exc:
        result.update(exception=type(exc).__name__, reason=str(exc))
        traceback.print_exc()
        return 1
    finally:
        result.update(finished_at=datetime.now(timezone.utc).isoformat(),
                      attempted_candidates=limited.calls, skipped_after_success=limited.skipped,
                      producer_returned_final=limited.accepted)
        write(BASE / 'deliver' / slug / 'simulation-execution.json', result)
        p.produce_part_with_budget = original



def fetch_domestic_source():
    """Reuse FC's read-only source resolver/downloader without staging uploads."""
    sys.path.insert(0, str(BASE / 'fc'))
    import index as fc
    from ci_fetch_bilibili import validate_media
    slug = os.environ['RUN_SLUG']
    row = next(r for r in validate_manifest(read(MANIFEST)) if r['slug'] == slug)
    candidate = dict(source=row['platform'], page_url=row['source_url'],
                     video_url=row.get('video_url'), extra=row.get('extra'))
    dest = BASE / '_src/video.mp4'
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if urlparse(row['source_url']).hostname == 'news.qq.com':
            candidate['video_url'], duration = fc.tencent_resolve_url(row['source_url'])
            if duration and not (fc.MIN_DUR <= duration <= fc.MAX_DUR):
                raise ValueError('Resolved duration outside production source bounds')
        if not candidate['video_url']:
            raise RuntimeError('No direct source URL in fixed sample')
        fc._download_inner(candidate, dest)
        validate_media(dest)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as f:
            f.write(f'path={dest}\n')
    except Exception as exc:
        write(BASE / 'deliver' / slug / '_tmp/source_quality.json',
              dict(passed=False, retryable=True, failure_stage='source-fetch',
                   reason=f'{type(exc).__name__}: {exc}',
                   note='FC resolver/download code executed on CI runner; geographic network differences remain unmeasured.'))
        raise

def validate_finals(out):
    sys.path.insert(0, str(BASE / 'fc'))
    import index as fc
    from batch_delivery import archive_accepted
    meta = read(out / 'meta.json', [])
    rows = meta if isinstance(meta, list) else [meta]
    finals = []
    for row in rows:
        for error in (fc.artifact_quality_error(row), fc.artifact_subtitle_error(row, out),
                      fc.artifact_cover_error(row, out), fc.daily_mix_error(row, {})):
            if error:
                raise ValueError(error)
        name = row['final']
        if Path(name).name != name:
            raise ValueError('Unsafe final path')
        video = out / name
        sha = digest(video)
        if sha != (row.get('fingerprints') or {}).get('sha256'):
            raise ValueError('Final MP4 SHA mismatch')
        probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams',
                            '-show_format', '-of', 'json', str(video)]))
        if float(probe['format']['duration']) < 120:
            raise ValueError('Final shorter than 120 seconds')
        if not {'audio', 'video'} <= {s['codec_type'] for s in probe['streams']}:
            raise ValueError('Missing video/audio stream')
        finals.append(dict(file=name, sha256=sha, duration=float(probe['format']['duration']),
                           title=row['title'], source_sha256=row.get('source_sha256')))
    if finals:
        archive_accepted(out, out.name)
    return finals


def classify(finals, validation_error, batch, source, execution, steps):
    if finals and not validation_error:
        return 'passed', 'final-accepted'
    if validation_error:
        return 'rejected', 'artifact-validation'
    if execution.get('exception'):
        return 'unresolved', 'producer-runtime'
    # Runtime and service failures are not evidence of unusable material.
    rejects = batch.get('rejected') or []
    quality_types = {'VisualQualityError', 'NoStructuralCandidate', 'NoEligibleArgument'}
    if rejects and all(r.get('error_type') in quality_types and not r.get('retryable') for r in rejects):
        return 'rejected', 'candidate-quality'
    if batch.get('retryable') or rejects:
        return 'unresolved', 'candidate-runtime'
    if source.get('passed') is False and source.get('retryable') is False:
        return 'rejected', 'source-quality'
    failed = [k for k, v in steps.items() if v.get('outcome') in ('failure', 'cancelled')]
    return 'unresolved', failed[0] if failed else 'missing-final-evidence'


def report():
    slug = os.environ['RUN_SLUG']
    row = next(r for r in validate_manifest(read(MANIFEST)) if r['slug'] == slug)
    out = BASE / 'deliver' / slug
    batch = read(out / 'batch_report.json', {})
    source = read(out / '_tmp/source_quality.json', {})
    execution = read(out / 'simulation-execution.json', {})
    steps = json.loads(os.environ.get('SIMULATION_STEPS', '{}'))
    finals, error = [], ''
    try:
        finals = validate_finals(out)
    except Exception as exc:
        error = f'{type(exc).__name__}: {exc}'
    status, stage = classify(finals, error, batch, source, execution, steps)
    evidence = BASE / 'simulation-reports' / slug
    code = ['produce_cn.py', 'visual_selection.py', 'scene_text.py', 'source_selection.py',
            'simulate_sources.py', 'asr_production_config.json']
    value = dict(sample=row, status=status, stage=stage, finals=finals,
                 validation_error=error, source_sha256=source.get('source_sha256'),
                 batch=batch, source_quality=source, execution=execution, steps=steps,
                 tested_sha=os.environ.get('GITHUB_SHA'), run_id=os.environ.get('GITHUB_RUN_ID'),
                 code_hashes={f:digest(BASE/f) for f in code},
                 publication_snapshot_sha256=digest(BASE/'_publication_state.json') if (BASE/'_publication_state.json').exists() else None)
    write(evidence / 'report.json', value)
    # Compact diagnostics only; no mother video or unaccepted media uploads.
    for pattern in ('_tmp/**/*.json', '_tmp/**/*.txt', 'meta.json', 'batch_report.json', 'subtitles*.ass'):
        for path in out.glob(pattern):
            if path.is_file() and path.stat().st_size <= 10 * 1024 * 1024:
                target = evidence / 'evidence' / path.relative_to(out)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    print(json.dumps(dict(sample=row['id'], status=status, stage=stage, finals=len(finals)), ensure_ascii=False))


def aggregate(manifest, reports):
    samples = validate_manifest(manifest)
    by_slug = {}
    for report in reports:
        slug = report['sample']['slug']
        if slug in by_slug:
            raise ValueError('Duplicate sample report: ' + slug)
        by_slug[slug] = report
    rows = [by_slug.get(s['slug'], dict(sample=s, status='unresolved', stage='missing-report', finals=[])) for s in samples]
    counts = Counter(r['status'] for r in rows)
    assert sum(counts.values()) == 100
    passed = counts['passed']
    unresolved = counts['unresolved']
    known = 100-unresolved
    hashes = Counter(r.get('source_sha256') for r in rows if r.get('source_sha256'))
    return dict(total=100, passed=passed, rejected=counts['rejected'], unresolved=unresolved,
                confirmed_success_percent=passed, possible_success_percent_range=[passed,passed+unresolved],
                resolved_success_percent=round(100*passed/known,2) if known else None,
                complete=unresolved == 0, stages=dict(Counter(r['stage'] for r in rows)),
                identical_mother_hash_groups={k:n for k,n in hashes.items() if n>1},
                samples=rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['sample','matrix','run','report','aggregate','fetch-domestic-source'])
    parser.add_argument('--source')
    parser.add_argument('--reports', default='simulation-reports')
    args = parser.parse_args()
    if args.mode == 'sample':
        build_manifest()
    elif args.mode == 'matrix':
        print(json.dumps(dict(include=validate_manifest(read(MANIFEST))), ensure_ascii=False, separators=(',',':')))
    elif args.mode == 'fetch-domestic-source':
        fetch_domestic_source()
    elif args.mode == 'run':
        return run_producer(args)
    elif args.mode == 'report':
        report()
    else:
        reports = [read(p) for p in Path(args.reports).rglob('report.json')]
        summary = aggregate(read(MANIFEST), reports)
        dest = Path(args.reports) / 'summary.json'
        write(dest, summary)
        text = (f"100素材模拟：已确认成功 {summary['passed']}/100，质量拒绝 {summary['rejected']}/100，"
                f"未确定 {summary['unresolved']}/100。\n"
                f"确认成功率下限 {summary['confirmed_success_percent']}%，可能范围 {summary['possible_success_percent_range']}%。\n"
                "以每个源URL至少一条真实合格测试片为成功；未投稿。未确定不等同质量失败。\n\n"
                '|ID|状态|阶段|成片数|\n|---|---|---|---|\n' +
                '\n'.join(f"|{r['sample']['id']}|{r['status']}|{r['stage']}|{len(r.get('finals',[]))}|" for r in summary['samples']))
        dest.with_suffix('.md').write_text(text)
        if os.environ.get('GITHUB_STEP_SUMMARY'):
            with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
                f.write(text)
        print(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
