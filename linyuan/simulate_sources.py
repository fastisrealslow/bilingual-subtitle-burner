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
import tempfile
import urllib.request

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
_manifest_relative = os.environ.get(
    'SIMULATION_MANIFEST_PATH', 'simulations/source100-20260916.json')
MANIFEST = (BASE / _manifest_relative).resolve()
if BASE.resolve() not in MANIFEST.parents:
    raise ValueError('Simulation manifest must stay inside linyuan')


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
    kind = manifest.get('kind') or 'fixed100'
    if kind == 'fixed100':
        assert len(rows) == 100, 'Exactly 100 source samples are required'
        assert all(re.fullmatch(r'sim-0916-\d{3}-[a-f0-9]{6}', r['slug']) for r in rows)
    elif kind == 'seed_expansion_acceptance':
        assert 1 <= len(rows) <= 20, 'Seed acceptance batches must remain bounded'
        assert all(re.fullmatch(r'seed-accept-[a-f0-9]{12}', r['slug']) for r in rows)
        assert manifest.get('source_preflight_run_id')
        assert all(r.get('source_preflight_sha256') for r in rows)
    elif kind == 'source_library_acceptance':
        assert len(rows) == 20, 'Library acceptance uses a fixed 20-source denominator'
        assert all(re.fullmatch(r'library-0921-\d{3}-[a-f0-9]{6}', r['slug']) for r in rows)
        audit_path=(BASE / manifest['library_snapshot']).resolve()
        assert BASE.resolve() in audit_path.parents
        assert digest(audit_path)==manifest['library_snapshot_sha256'], 'Library snapshot changed'
        pool=read(audit_path)['profiles']['20']['top']
        chosen=sample(pool,count=20,seed=manifest['seed'])
        assert [r['source_url'] for r in rows]==[r['source_url'] for r in chosen], 'Library sample changed'
    else:
        raise ValueError('Unknown simulation manifest kind')
    assert len({source_key(r['source_url']) for r in rows}) == len(rows)
    assert len({r['id'] for r in rows}) == len(rows)
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
        duration_error = fc.editorial.metadata_error(row, float(probe['format']['duration']))
        if duration_error:
            raise ValueError(duration_error)
        if not {'audio', 'video'} <= {s['codec_type'] for s in probe['streams']}:
            raise ValueError('Missing video/audio stream')
        finals.append(dict(file=name, sha256=sha, duration=float(probe['format']['duration']),
                           title=row['title'], source_sha256=row.get('source_sha256'),
                           content_policy=row.get('content_policy','legacy120'),
                           content_format=row.get('content_format','complete_view')))
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
    visual_reasons=('来源取景无法保留完整人脸', '来源角标无法避开', '整段取景预检：',
                    '动态取景剩余帧即使全部匹配', '动态取景连续', '动态取景目标人物匹配不足',
                    '真人取景源区域仅')
    def quality_rejection(r):
        return (not r.get('retryable') and (r.get('error_type') in quality_types or
            (r.get('error_type')=='ValueError' and r.get('stage')=='part-quality'
             and str(r.get('reason','')).startswith(visual_reasons))))
    if rejects and all(quality_rejection(r) for r in rejects):
        return 'rejected', 'candidate-quality'
    if batch.get('retryable') or rejects:
        return 'unresolved', 'candidate-runtime'
    source_reason=str(source.get('reason',''))
    duration_rejection=bool(re.search(r'(?:时长 \d+(?:\.\d+)?s 不在|Resolved duration outside production source bounds)',source_reason))
    if source.get('passed') is False and (source.get('retryable') is False or duration_rejection):
        return 'rejected', 'source-quality'
    failed = [k for k, v in steps.items() if v.get('outcome') in ('failure', 'cancelled')]
    return 'unresolved', failed[0] if failed else 'missing-final-evidence'


def retain_identity_evidence(out, evidence):
    """Copy the bounded source-identity samples, never source or final media."""
    copied, remaining = 0, 24 * 1024 * 1024
    for path in sorted((out / '_tmp').glob('identity_*.jpg'))[:24]:
        size = path.stat().st_size if path.is_file() else 0
        if 0 < size <= 2 * 1024 * 1024 and size <= remaining:
            target = evidence / 'evidence' / path.relative_to(out)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
            remaining -= size
    return copied


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
            'live_tracking.py', 'ci_fetch_bilibili.py',
            'simulate_sources.py', 'asr_production_config.json']
    value = dict(sample=row, status=status, stage=stage, finals=finals,
                 validation_error=error, source_sha256=source.get('source_sha256'),
                 batch=batch, source_quality=source, execution=execution, steps=steps,
                 tested_sha=os.environ.get('GITHUB_SHA'), run_id=os.environ.get('GITHUB_RUN_ID'),
                 diagnostic_subset=bool(os.environ.get('SIMULATION_SAMPLE_IDS')),
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
    # Source identity can fail before source_identity.json is written. Retain
    # its six already-sampled frames so a wrong-person rejection can be
    # distinguished from sparse host/audience cutaways. These are evidence
    # only and can never count as an accepted render.
    retain_identity_evidence(out, evidence)
    # Failed live-window checks used to retain only a generic reason while the
    # six measured frames were discarded. Keep those small, already-sampled
    # images so padding can be distinguished from a naturally dark scene
    # without uploading the mother video or an unaccepted render.
    for path in out.glob('_tmp/**/live-region-*/frame-*.jpg'):
        if path.is_file() and path.stat().st_size <= 2 * 1024 * 1024:
            target = evidence / 'evidence' / path.relative_to(out)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    # The cheap pre-ranking pass can reject every crop before the tracker
    # starts. Retain its existing six-per-candidate samples for diagnosis;
    # they are not renders and can never count as accepted media.
    for path in out.glob('_tmp/visual-selection/frame-*.jpg'):
        if path.is_file() and path.stat().st_size <= 2 * 1024 * 1024:
            target = evidence / 'evidence' / path.relative_to(out)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    # Keep the bounded failure frames already emitted by the tracker. A text
    # exception alone cannot distinguish a detector bug from a genuinely
    # obstructed face; these are diagnostics, never accepted media.
    remaining=24*1024*1024
    for directory in sorted((out/'_tmp').glob('*.evidence')):
        if not directory.is_dir():continue
        paths=sorted(directory.glob('*.jpg'),key=lambda p:('failure' not in p.name,p.name))
        for path in paths[:9]:
            size=path.stat().st_size
            if size>2*1024*1024 or size>remaining:continue
            target=evidence/'evidence'/path.relative_to(out)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,target);remaining-=size
    print(json.dumps(dict(sample=row['id'], status=status, stage=stage, finals=len(finals)), ensure_ascii=False))


def aggregate(manifest, reports):
    samples = validate_manifest(manifest)
    total=len(samples)
    expected_samples={s['slug']:s for s in samples}
    # A diagnostic or a best-of merge across runs is not one benchmark.
    if any(r.get('diagnostic_subset') for r in reports):
        raise ValueError('Diagnostic subset reports cannot enter full acceptance')
    for field in ('tested_sha','run_id','publication_snapshot_sha256'):
        values={str(r[field]) for r in reports if r.get(field)}
        if len(values)>1 or values and any(not r.get(field) for r in reports):
            raise ValueError('Mixed or incomplete report provenance: '+field)
    expected=(manifest.get('comparison') or {}).get('publication_sha256')
    if expected and any(r.get('publication_snapshot_sha256') not in (None,expected) for r in reports):
        raise ValueError('Reports used a different publication snapshot')
    by_slug = {}
    for report in reports:
        slug = report['sample']['slug']
        if slug not in expected_samples or report['sample']['source_url']!=expected_samples[slug]['source_url']:
            raise ValueError('Unexpected or replaced source report: '+slug)
        if slug in by_slug:
            raise ValueError('Duplicate sample report: ' + slug)
        by_slug[slug] = report
    rows = [by_slug.get(s['slug'], dict(sample=s, status='unresolved', stage='missing-report', finals=[])) for s in samples]
    counts = Counter(r['status'] for r in rows)
    assert sum(counts.values()) == total
    passed = counts['passed']
    unresolved = counts['unresolved']
    known = total-unresolved
    target=int((manifest.get('comparison') or {}).get('target_passed',(total*30+99)//100))
    hashes = Counter(r.get('source_sha256') for r in rows if r.get('source_sha256'))
    return dict(total=total, manifest_kind=manifest.get('kind','fixed100'), passed=passed, rejected=counts['rejected'], unresolved=unresolved,
                target_passed=target, target_met=passed>=target,
                confirmed_success_percent=round(100*passed/total,2),
                possible_success_percent_range=[round(100*passed/total,2),round(100*(passed+unresolved)/total,2)],
                resolved_success_percent=round(100*passed/known,2) if known else None,
                complete=unresolved == 0, stages=dict(Counter(r['stage'] for r in rows)),
                identical_mother_hash_groups={k:n for k,n in hashes.items() if n>1},
                samples=rows)


def prepare_snapshot():
    """Use the exact same publication exclusions as the baseline experiment."""
    manifest=read(MANIFEST)
    comparison=manifest.get('comparison') or {}
    if not comparison:
        shutil.copy2(BASE/'.automation/fc_state.json',BASE/'_publication_state.json')
        return
    commit=comparison['publication_commit']
    if not re.fullmatch(r'[0-9a-f]{40}',commit):raise ValueError('Invalid baseline commit')
    url=f'https://raw.githubusercontent.com/fastisrealslow/bilingual-subtitle-burner/{commit}/linyuan/.automation/fc_state.json'
    data=urllib.request.urlopen(url,timeout=60).read()
    if hashlib.sha256(data).hexdigest()!=comparison['publication_sha256']:
        raise ValueError('Baseline publication snapshot hash mismatch')
    (BASE/'_publication_state.json').write_bytes(data)


def restore_simulation_evidence():
    """Reuse ASR only for this sample and these exact downloaded source bytes."""
    comparison=read(MANIFEST).get('comparison') or {}
    if not comparison.get('run_id'):return
    slug=os.environ['RUN_SLUG']
    assert slug in {r['slug'] for r in validate_manifest(read(MANIFEST))}
    repo=os.environ.get('GITHUB_REPOSITORY','fastisrealslow/bilingual-subtitle-burner')
    with tempfile.TemporaryDirectory(prefix='simulation-asr-') as directory:
        subprocess.run(['gh','run','download',str(int(comparison['run_id'])),
            '--repo',repo,'--name','simulation-report-'+slug,'--dir',directory],
            check=True,timeout=180)
        from restore_production_evidence import restore
        restore(Path(directory)/'evidence/_tmp',BASE/'deliver'/slug/'_tmp')


def matrix_samples(manifest, requested=''):
    samples=validate_manifest(manifest)
    if not requested:return samples
    # Only original manifest IDs, never replacement URLs or a new denominator.
    fields=requested.split(',')
    if not all(field.isdigit() for field in fields):
        raise ValueError('Diagnostic sample IDs must be comma-separated integers')
    ids=[int(field) for field in fields]
    available={row['id'] for row in samples}
    if len(ids)!=len(set(ids)) or not set(ids)<=available:
        raise ValueError('Diagnostic samples must be unique IDs from the selected manifest')
    return [row for row in samples if row['id'] in ids]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['sample','matrix','run','report','aggregate','fetch-domestic-source','prepare-snapshot','restore-simulation-evidence'])
    parser.add_argument('--source')
    parser.add_argument('--reports', default='simulation-reports')
    args = parser.parse_args()
    if args.mode == 'prepare-snapshot':
        prepare_snapshot()
    elif args.mode == 'restore-simulation-evidence':
        restore_simulation_evidence()
    elif args.mode == 'sample':
        build_manifest()
    elif args.mode == 'matrix':
        print(json.dumps(dict(include=matrix_samples(read(MANIFEST),os.environ.get('SIMULATION_SAMPLE_IDS',''))),
                         ensure_ascii=False, separators=(',',':')))
    elif args.mode == 'fetch-domestic-source':
        fetch_domestic_source()
    elif args.mode == 'run':
        return run_producer(args)
    elif args.mode == 'report':
        report()
    else:
        if os.environ.get('SIMULATION_SAMPLE_IDS'):
            raise ValueError('A diagnostic subset cannot produce a 100-source acceptance summary')
        reports = [read(p) for p in Path(args.reports).rglob('report.json')]
        summary = aggregate(read(MANIFEST), reports)
        dest = Path(args.reports) / 'summary.json'
        write(dest, summary)
        text = (f"100素材模拟：已确认成功 {summary['passed']}/100，质量拒绝 {summary['rejected']}/100，"
                f"未确定 {summary['unresolved']}/100。\n"
                f"本轮确认成功率 {summary['confirmed_success_percent']}%；目标 {summary['target_passed']}%，达标 {summary['target_met']}。\n"
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
