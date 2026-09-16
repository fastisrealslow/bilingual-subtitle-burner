"""Read-only reconciliation of dispatch history, Actions and inspected stock.

This is a public status projection, never a new source of publishing permission.
Historical dispatch rows and publication receipts remain untouched.
"""
from collections import Counter
from datetime import datetime
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parent / 'fc'))
import index as fc

STATUS_PATH = Path(__file__).resolve().parents[1] / 'site/production_status.json'
RUN_FIELDS = ('id', 'status', 'conclusion', 'created_at', 'updated_at',
              'run_started_at', 'html_url', 'run_number', 'display_title')
ACTIVE = {'queued', 'in_progress', 'waiting', 'pending', 'requested'}


def timestamp(value):
    if not value:
        return 0
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def remaining(entry, state):
    pub = state.get('published', {}).get(entry['slug']) or {}
    total = int(pub.get('parts_total') or entry.get('parts_total') or 0)
    if pub and total <= 1:
        return False
    if total:
        return bool(set(range(total)) - fc.processed_part_indices(entry))
    return not pub


def entry_signature(entry):
    """Let the page reject a projection superseded by a newer FC state write."""
    return {k: entry.get(k) for k in ('ts', 'failed', 'published_parts',
            'processed_part_indices', 'source_check_retry_after', 'weekly_full_week')}


def collect_runs(state, api, previous=None, max_pages=10):
    """Refresh active runs and backfill unresolved older jobs with bounded paging.

    Cache only terminal runs. An API failure raises; it must never manufacture
    an empty running queue or turn last hour's running job into a terminal job.
    """
    latest = {e['slug']: e for e in fc._latest_dispatches(state)}
    cached = (previous or {}).get('runs', {})
    runs = {s: r for s, r in cached.items()
            if s in latest and r.get('status') == 'completed'}
    targets = {s: e for s, e in latest.items() if remaining(e, state)
               and not e.get('failed') and s not in fc.REVIEW_PAUSED_SLUGS}
    seen = set()
    for page in range(1, max_pages + 1):
        rows = api(f'actions/workflows/{fc.WF_PRODUCE}/runs?per_page=100&page={page}').get('workflow_runs', [])
        for run in rows:
            slug = str(run.get('display_title') or '').partition(' · ')[2].strip()
            if slug not in latest or slug in seen:
                continue
            seen.add(slug)
            runs[slug] = {k: run.get(k) for k in RUN_FIELDS}
        if not rows or len(rows) < 100:
            break
        oldest = min(timestamp(r['created_at']) for r in rows)
        # A recent dispatch missing from page one cannot be on an older page.
        unresolved = [e for s, e in targets.items() if s not in runs
                      and float(e.get('ts') or 0) < oldest]
        if not unresolved:
            break
    # A previously active run older than the first page still gets a real read.
    for slug, run in cached.items():
        if slug in latest and slug not in seen and run.get('status') in ACTIVE:
            current = api(f"actions/runs/{run['id']}")
            runs[slug] = {k: current.get(k) for k in RUN_FIELDS}
    return runs


def classify(entry, state, inventory, run, now):
    slug = entry['slug']
    if not remaining(entry, state):
        return 'complete', '本批次已处理完毕'
    if slug in fc.REVIEW_PAUSED_SLUGS:
        return 'paused', '已暂停发布：此前画面或字幕验收未通过，保留历史记录'
    # Recovery changes ts before the new run is created. Old completed evidence
    # must not override this new intent; an actual active run remains authoritative.
    current_run = run and timestamp(run.get('updated_at')) >= float(entry.get('ts') or 0) - 60
    if current_run and run.get('status') in ACTIVE:
        return ('running', '正在出片') if run['status'] == 'in_progress' else ('queued', 'Actions 排队中')
    if entry.get('source_check_retry_after') and not entry.get('failed'):
        return 'retry_queued', '等待自动恢复调度（受并发和横屏库存配额约束）'
    if entry.get('failed'):
        return 'failed', entry.get('last_error') or '素材验收未通过'
    records = [r for r in inventory.get('artifacts', []) if r.get('slug') == slug]
    parts = [p for r in records for p in r.get('parts', [])
             if int(p.get('index', -1)) not in fc.processed_part_indices(entry)]
    fresh = (now - float(inventory.get('updated_at') or 0) < 3 * 3600
             and inventory.get('quality_gate_version') == fc.QUALITY_GATE_VERSION
             and inventory.get('editorial_policy_version') == fc.editorial.VERSION)
    if parts and fresh:
        verified = [p for p in parts if p.get('status') == 'verified']
        usable = [p for p in verified if not fc.inventory_publication_error(p, entry, state)
                  and (not entry.get('weekly_full_week') or p.get('content_type') == 'full_interview')]
        if usable:
            if entry.get('weekly_full_week'):
                return 'reserved', '成片已验收，保留在周日 21:00 完整访谈发布位'
            return 'ready', f'{len(usable)} 条成片已验收，等待发布时段及配额'
        if verified:
            return 'excluded', '已有成片，但剩余片段受去重或发布规则限制'
        return 'validation_failed', '成片复验未通过：' + str(parts[0].get('reason') or '无可发布片段')
    if records and not parts and fc.processed_part_indices(entry):
        return 'complete', '库存内的成片已全部处理'
    if current_run and run.get('status') == 'completed':
        if run.get('conclusion') == 'success':
            return 'awaiting_validation', '生产已完成，等待实际成片复验；尚不计入可发布库存'
        return 'interrupted', '运行已结束：' + str(run.get('conclusion') or '结果未知')
    if entry.get('production_rules_version') != fc.PRODUCTION_RULES_VERSION:
        return 'obsolete', '历史旧标准任务，尚未通过当前成片验收'
    if now - float(entry.get('ts') or 0) < 15 * 60:
        return 'awaiting_run', '已提交调度，等待 Actions 运行记录'
    return 'unknown', '未找到本次调度的运行或合格成片证据，需核对；不计为生产中'


def build(state, inventory, runs, now=None):
    now = int(time.time() if now is None else now)
    counts = Counter(e.get('slug') for e in state.get('dispatched', []))
    tasks = []
    for entry in fc._latest_dispatches(state):
        slug = entry['slug']
        run = runs.get(slug) or {}
        status, detail = classify(entry, state, inventory, run, now)
        tasks.append(dict(slug=slug, status=status, detail=detail,
                          signature=entry_signature(entry), history_count=counts[slug],
                          run_id=run.get('id'), run_url=run.get('html_url'),
                          run_started_at=run.get('run_started_at'),
                          run_updated_at=run.get('updated_at')))
    return dict(version=1, updated_at=now, inventory_updated_at=inventory.get('updated_at'),
                counts=dict(Counter(t['status'] for t in tasks)), tasks=tasks, runs=runs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=STATUS_PATH)
    args = parser.parse_args()
    from source_supply import api, INVENTORY
    state = fc.load_state()
    if not state.get('dispatched'):
        raise SystemExit('调度状态不可用，保留上一份状态快照')
    previous = json.loads(args.out.read_text()) if args.out.exists() else {}
    runs = collect_runs(state, api, previous)
    inventory = json.loads(INVENTORY.read_text()) if INVENTORY.exists() else {}
    result = build(state, inventory, runs)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix('.tmp')
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(args.out)
    print(json.dumps(dict(updated_at=result['updated_at'], counts=result['counts']), ensure_ascii=False))


if __name__ == '__main__':
    main()
