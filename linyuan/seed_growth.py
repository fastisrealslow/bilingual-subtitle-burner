"""Bounded, resumable seed growth. Metadata is never accepted media or rights."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sqlite3
import time

import monitor_v2 as monitor
from seed_campaign import BASE, BVID, NO_MEDIA, census, verify_seed
from seed_scan import rotating_batch
from source_catalogue_duration import eligible_duration

QUERIES = ('林园 完整访谈', '林园 演讲', '林园 第一财经', '林园 腾讯财经',
           '林园 私募排排网', '林园 北大', '林园 春瑜会客厅', '林园 红周刊',
           '林园 央视', '林园 雪球嘉年华', '林园 财联社', '林园 凤凰论坛',
           '林园 早年 经历 完整访谈', '林园 创业 经历 访谈',
           '林园 第一笔钱 买房 访谈', '林园 茅台 可口可乐 投资 访谈',
           '林园 实地调研 访谈', '林园 看走眼 亏损 访谈',
           '林园 投资', '林园 医药', '林园 消费', '林园 可转债',
           '林园 茅台', '林园 片仔癀', '林园 达仁堂', '林园 同仁堂',
           '林园 白酒', '林园 苹果 腾讯', '林园 分红', '林园 垄断',
           '林园 现金流', '林园 股东大会', '林园 牛市', '林园 熊市',
           '林园 首席说', '林园 大道财经', '林园 证券时间',
           '林园 中国经营者', '林园 财富非常道', '林园 资本人物',
           '林园 投资者说', '林园 商业人生', '林园 微观世界',
           '林园 会见财经界', '林园 华南农大', '林园 雪球 2017',
           '林园 新浪财经 2020', '林园 2026', '林园 2025',
           '林园 2024', '林园 2019', '林园 投资经历',
           '林园 投资者交流', '林园 圆桌会议', '林园 投资讲座', '林园 申万宏源 全集')


def existing_roots(base):
    roots = set()
    for config in json.loads((base / 'monitor_v2_config.json').read_text()):
        if config.get('type') not in {'bilibili_collection', 'bilibili_series'}:
            continue
        roots.update(s['bvid'] for s in config.get('seeds', []))
        if config.get('seeds_file'):
            roots.update(s['bvid'] for s in json.loads((base / config['seeds_file']).read_text())['seeds'])
    return roots


def catalog_leads(db):
    """Unapproved archive leads; includes historical programme titles, not rivals."""
    rows = {}
    with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for row in conn.execute('SELECT * FROM items ORDER BY publish_time DESC'):
            if row['source'] not in {'bilibili_search', 'reference_origin_search',
                                      'bilibili_collection', 'bilibili_series'}:
                continue
            extra = json.loads(row['extra'] or '{}')
            bvid = extra.get('bvid', '')
            if (not BVID.fullmatch(bvid) or '林园' not in row['title']
                    or NO_MEDIA.search(row['title'])
                    or row['author'] in {'', '园园滚雪球', '园来滚雪球', '账号已注销', '已注销'}
                    or extra.get('source_role') == 'reference'):
                continue
            duration = monitor.duration_seconds(extra.get('duration'))
            if not eligible_duration(duration):
                continue
            lead = dict(bvid=bvid, discovery='historical_catalog_revalidated',
                catalog_title=row['title'], catalog_author=row['author'], catalog_duration=duration)
            if duration > rows.get(bvid, {}).get('catalog_duration', 0):
                rows[bvid] = lead
    return sorted(rows.values(), key=lambda r: (-r['catalog_duration'], r['bvid']))


def admit_verified(seed_data, rows, existing, limit):
    """Exact parent dedup; never promote metadata into media/editorial approval."""
    added = []
    for row in rows:
        if len(added) >= max(0, limit):
            break
        bvid = row.get('bvid', '')
        if (row.get('status') != 'verified_metadata' or not BVID.fullmatch(bvid)
                or bvid in existing or NO_MEDIA.search(row.get('title') or '')
                or row.get('owner_mid') in {1700344493, 275211725}
                or row.get('observed_author') in {'', '园园滚雪球', '园来滚雪球', '账号已注销', '已注销'}):
            continue
        entry = {key: row[key] for key in ('bvid', 'title', 'observed_author', 'owner_mid',
            'url', 'published_at', 'metadata_provenance', 'checked_at', 'pages', 'no_reprint',
            'discovery', 'rights_status', 'origin_role') if key in row}
        from fc import index as fc
        if fc.NOISE.search(entry['title']) or fc.AI_NOISE.search(entry['title']):
            continue
        evidence = fc.title_has_target_speaker(entry['title'])
        from source_admission_guard import restriction
        hold = restriction(entry.get('url') or 'https://www.bilibili.com/video/'+bvid, [])
        # A matching date/title is a lead, not audio identity proof or reuse
        # permission. Keep possible copies of the restricted 15 Oct roadshow
        # in the catalogue until origin/permission checks resolve them.
        programme_hint = (re.search(r'2026[-./年]?10[-./月]?15',entry['title'])
            or str(row.get('published_at') or '').startswith('2026-10')
            and re.search(r'10[./月]15',entry['title']))
        if programme_hint:
            hold = hold or 'restricted_20261015_programme_match_pending'
        dispatch = row.get('no_reprint') != 1 and evidence and not hold
        entry.update(media_verified=False, editorial_approved=False,
            target_speaker_title_evidence=evidence,
            source_role='mother_candidate' if dispatch else 'catalog_only',
            direct_dispatch=dispatch)
        if hold:
            entry['reuse_hold_reason'] = hold
        # A title date is only an uploader clue. Do not infer filming from upload.
        match = re.search(r'(?<!\d)(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})(?:日)?(?!\d)', entry['title'])
        if match:
            from datetime import date
            try:
                entry['content_date_hint'] = date(*map(int, match.groups())).isoformat()
                entry['content_date_hint_provenance'] = 'uploader_title_unverified'
            except ValueError:
                pass
        seed_data['seeds'].append(entry)
        existing.add(bvid)
        added.append(entry)
    return added


def pending_leads(path):
    """Unverified discovery work survives a batch; it never enters census."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if data.get('version') != 1 or not isinstance(data.get('leads'), list):
        raise ValueError('Invalid pending seed discovery queue')
    return {r['bvid']: r for r in data['leads'] if isinstance(r, dict)
            and BVID.fullmatch(str(r.get('bvid', '')))
            and eligible_duration(monitor.duration_seconds(r.get('catalog_duration')))}


def save_pending_leads(path, leads):
    from batch_delivery import write_json
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, dict(version=1, counting_policy='unverified_not_seed_or_stock',
                         leads=list(leads.values())))


def pause_provider(state, observation):
    from batch_delivery import write_json
    scan = json.loads(state.read_text()) if state.exists() else {}
    cooldown = dict(until=time.time()+3600,reason='provider_restricted',
        http_status=observation.get('http_status'),api_code=observation.get('api_code'))
    scan['verification_cooldown'] = cooldown
    state.parent.mkdir(parents=True,exist_ok=True)
    write_json(state,scan)
    return cooldown


def long_replenishment_budget(base):
    """Catalogue target is a floor; a proven T shortage gets a bounded refill."""
    from datetime import datetime, timezone, timedelta
    from fc import index as fc
    try:
        state=json.loads((base/'.automation/fc_state.json').read_text())
        inventory=json.loads((base/'.automation/source_inventory.json').read_text())
        stock=fc.source_inventory(state,inventory)
        admission=inventory['source_admission']
        seeds=json.loads((base/'bilibili_seeds.json').read_text())['seeds']
    except (OSError,ValueError,KeyError,TypeError):return 0
    if not stock['inventory_fresh'] or not fc.experiment.preparing():return 0
    ready=stock.get('experiment_ready') or {}
    if int(ready.get('landscape') or 0)>=2 and sum(ready.values())>=4:return 0
    if not isinstance(admission.get('candidates'),list):return 0
    available=sum(fc.experiment.long_candidate(dict(extra=dict(duration=row.get('duration_sec'))))
                  for row in admission['candidates'])
    if available>=6:return 0
    day=datetime.fromtimestamp(time.time(),timezone(timedelta(hours=8))).date().isoformat()
    added=sum(e.get('long_replenishment_added_on')==day for e in seeds)
    return min(4,max(0,8-added))


def run(base=BASE, *, target=1000, limit=48, search=True, verify=verify_seed,replenish_long=False):
    deadline = time.monotonic() + 210
    before = census(base)
    roots = existing_roots(base)
    remaining = max(0, target - before['total'])
    report = dict(baseline=before, target=target, accepted_mp4_count=0,
                  rights_approved_count=0, production_dispatch=False, added_count=0,
                  errors=[], counting_policy='unique_parent_id_not_parts_or_stock')
    replenishing=remaining==0 and replenish_long
    if replenishing:
        remaining=long_replenishment_budget(base)
        limit=min(limit,12)
        report.update(long_replenishment=True,target_is_minimum=True,replenishment_budget=remaining)
    if remaining == 0:
        report['after'] = before
        return report
    state = base / '.automation/seed_growth_scan.json'
    scan = json.loads(state.read_text()) if state.exists() else {}
    cooldown = scan.get('verification_cooldown') or {}
    if cooldown.get('until', 0) > time.time():
        report.update(after=before, verification_paused=cooldown)
        return report
    pending_path = base / '.automation/seed_growth_leads.json'
    pending = {key: row for key, row in pending_leads(pending_path).items() if key not in roots}
    for row in catalog_leads(base / 'monitor_v2.db'):
        if row['bvid'] not in roots:
            pending[row['bvid']] = {**row, **pending.get(row['bvid'], {})}
    if search:
        search_scan = json.loads(state.read_text()) if state.exists() else {}
        for query in rotating_batch('queries', QUERIES, state, 3, state=search_scan):
            if time.monotonic() >= deadline:
                report['errors'].append(dict(stage='budget', error='time_budget_reached'))
                break
            try:
                depths = search_scan.setdefault('query_page_cursors', {})
                first_page = depths.get(query, 1)
                if type(first_page) is not int or not 1 <= first_page <= 19:
                    raise ValueError('Invalid bounded search-page cursor')
                depths[query] = first_page + 2 if first_page < 19 else 1
                from batch_delivery import write_json
                write_json(state, search_scan)
                report.setdefault('search_pages', {})[query] = [first_page, first_page+1]
                for row in monitor.BilibiliSearchSource({'pages': 2, 'start_page': first_page}, {})._fetch_via_api(query):
                    bvid = row.get('bvid', '')
                    if (BVID.fullmatch(bvid) and bvid not in roots
                            and '林园' in row.get('title', '') and not NO_MEDIA.search(row['title'])
                            and row.get('up') not in {'', '园园滚雪球', '园来滚雪球'}
                            and eligible_duration(monitor.duration_seconds(row.get('duration')))):
                        pending[bvid] = {**pending.get(bvid, {}), **dict(
                            bvid=bvid, discovery='normal_public_search',
                            catalog_title=row['title'], catalog_author=row['up'],
                            catalog_duration=row['duration'])}
            except Exception as exc:
                observation = {}
                if getattr(exc,'code',None) in {403,412,429}:
                    observation['http_status'] = exc.code
                api_code = re.search(r'\bcode=(-?\d+)\b',str(exc))
                if api_code and int(api_code[1]) == -412:
                    observation['api_code'] = -412
                report['errors'].append(dict(stage='search',error=type(exc).__name__,**observation))
                if observation:
                    report['verification_paused'] = pause_provider(state,observation)
                    break
    # Save ALL discoveries before the bounded verifier starts. Previously the
    # unselected tail of each search disappeared when the process exited.
    save_pending_leads(pending_path, pending)
    if report.get('verification_paused'):
        report.update(after=census(base),pending_lead_count=len(pending))
        return report
    candidates = sorted((r for r in pending.values()
                         if float(r.get('verification_retry_after') or 0) <= time.time()
                         and (not replenishing or 1200<=float(r.get('catalog_duration') or 0)<=5400)),
                        key=lambda r: (-r['catalog_duration'], r['bvid']))
    rows = []
    for candidate in rotating_batch('parents', candidates, state, min(64, max(1, limit)), key=lambda r: r['bvid']):
        if time.monotonic() >= deadline:
            report['errors'].append(dict(stage='budget', error='time_budget_reached'))
            break
        result = verify(candidate)
        rows.append(result)
        if (result.get('status') == 'not_verified' and
                (result.get('http_status') in {403, 412, 429} or result.get('api_code') == -412)):
            # A provider restriction is not evidence against the footage. Stop
            # this batch; the lazy cursor has advanced only for actual requests.
            # Persist across immediate monitor refreshes, not just this process.
            report['verification_paused'] = pause_provider(state,result)
            break
        if result.get('status') == 'not_verified':
            # A failed exact lookup is not a seed. Retain it for a later
            # metadata refresh without querying the same dead lead each batch.
            stable = (result.get('api_code') in {-404, 62002, 62012}
                      or result.get('api_code') == 0 and result.get('error') == 'ValueError')
            pending[candidate['bvid']]['verification_retry_after'] = time.time() + (86400 if stable else 1800)
        time.sleep(.5)
    data = json.loads((base / 'bilibili_seeds.json').read_text())
    if replenishing:
        # Reuse the same identity/no-reprint/programme-hold checks, then keep
        # only dispatchable long metadata. Rejected research leads do not grow
        # the catalogue beyond its target or count toward actual clip stock.
        staging=dict(seeds=[])
        proposals=admit_verified(staging,rows,set(roots),len(rows))
        eligible=[e for e in proposals if e.get('direct_dispatch') is True
               and type(e.get('no_reprint')) is int and e['no_reprint']==0
               and any(1200<=p['duration']<=5400 for p in e.get('pages',[]))]
        added=eligible[:min(remaining,limit)]
        eligible_ids={e['bvid'] for e in eligible}
        from datetime import datetime, timezone, timedelta
        day=datetime.fromtimestamp(time.time(),timezone(timedelta(hours=8))).date().isoformat()
        for entry in added:entry['long_replenishment_added_on']=day
        data['seeds'].extend(added);roots.update(e['bvid'] for e in added)
        for row in rows:
            if row.get('status')=='verified_metadata' and row['bvid'] not in roots|eligible_ids:
                pending[row['bvid']]['verification_retry_after']=time.time()+86400
    else:
        added = admit_verified(data, rows, roots, min(remaining, limit))
    from batch_delivery import write_json
    if added:
        from datetime import datetime, timezone
        data['updated_at'] = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        write_json(base / 'bilibili_seeds.json', data)
    # Commit seeds first: an interruption must not remove a verified lead
    # before its actual catalogue record has been saved. Root dedup repairs a
    # repeated queue entry safely on the next run.
    pending = {key: row for key, row in pending.items() if key not in roots}
    save_pending_leads(pending_path, pending)
    report.update(added_count=len(added), added_bvids=[r['bvid'] for r in added],
        pending_lead_count=len(pending),
        roles=dict(Counter(r['source_role'] for r in added)),
        new_long_parent_count=sum(any(p['duration'] >= 900 for p in r['pages']) for r in added),
        verification=dict(Counter(r['status'] for r in rows)), after=census(base))
    failed=[r for r in rows if r['status']!='verified_metadata']
    # Provider availability is not a media-quality rejection. Keep safe codes,
    # never response bodies, credentials, or a failed lead's rights approval.
    report.update(verification_errors=dict(Counter(r.get('error','unknown') for r in failed)),
        verification_http_status=dict(Counter(str(r['http_status']) for r in failed if 'http_status' in r)),
        verification_api_codes=dict(Counter(str(r['api_code']) for r in failed if 'api_code' in r)))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=int, default=1000)
    parser.add_argument('--limit', type=int, default=48)
    parser.add_argument('--replenish-long',action='store_true',
        help='After the catalogue target, replenish proven long-source shortages within a daily cap')
    parser.add_argument('--output', type=Path, default=BASE / '.automation/seed_growth_report.json')
    args = parser.parse_args()
    if not 1 <= args.limit <= 64 or not 1 <= args.target <= 5000:
        parser.error('bounded target/limit required')
    report = run(target=args.target, limit=args.limit,replenish_long=args.replenish_long)
    from batch_delivery import write_json
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
