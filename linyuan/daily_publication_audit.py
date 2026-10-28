"""Read actual daily receipts and creator visibility; never upload or retry."""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path

BEIJING=timezone(timedelta(hours=8))
HOURS=(10,14,16,21)


def experiment_receipts(state,now):
    """Validate executed groups, not the requested job profile or view counts."""
    import publishing_experiment as E
    now=now.astimezone(BEIJING);day=now.date().isoformat()
    expected=[E.assignment(now.timestamp(),f'{day} {hour:02d}')
              for hour in HOURS if hour<=now.hour]
    expected=[p for p in expected if p]
    actual={}
    for info in (state.get('published') or {}).values():
        for part in info.get('parts') or []:
            row=part.get('publishing_experiment') or {}
            if (part.get('status')=='published' and part.get('bvid')
                    and row.get('experiment_id')==E.EXPERIMENT_ID):
                actual[row.get('slot')]=dict(row,bvid=part['bvid'],
                    file_sha256=(part.get('fingerprints') or {}).get('sha256'))
    missing=[];bad=[];treatments=0;seen=set()
    for plan in expected:
        row=actual.get(plan['slot'])
        if not row:missing.append(plan['slot']);continue
        identity=(row['bvid'],row.get('file_sha256'))
        if (row.get('planned')!=plan['planned'] or row.get('actual')!=plan['planned']
                or row.get('compliant') is not True or not identity[1]
                or any(identity[0]==old[0] or identity[1]==old[1] for old in seen)):
            bad.append(dict(slot=plan['slot'],actual=row.get('actual'),
                            reason=row.get('reason') or 'group_or_file_mismatch'))
        else:
            seen.add(identity)
            treatments+=plan['planned']==E.TREATMENT
    return dict(active=bool(E.START<=day<E.END_EXCLUSIVE),expected_slots=[p['slot'] for p in expected],
        missing_slots=missing,noncompliant_slots=bad,treatment_published=treatments,
        passed=not missing and not bad)


def daily_receipts(state, now):
    now=now.astimezone(BEIJING);day=now.date().isoformat()
    receipts={};duplicates=[];hashes={};missing_hash=[]
    for slug,info in state.get('published',{}).items():
        for part in info.get('parts') or [info]:
            if part.get('status')!='published' or not part.get('bvid') or not part.get('ts'):continue
            if datetime.fromtimestamp(part['ts'],BEIJING).date()!=now.date():continue
            bvid=part['bvid'];sha=(part.get('fingerprints') or {}).get('sha256')
            if bvid in receipts:continue
            row=dict(bvid=bvid,slug=slug,title=part.get('title'),sha256=sha,
                     submitted_at=datetime.fromtimestamp(part['ts'],BEIJING).isoformat())
            receipts[bvid]=row
            if not sha:missing_hash.append(bvid)
            elif sha in hashes:duplicates.append(dict(bvid=bvid,duplicates=hashes[sha],sha256=sha))
            else:hashes[sha]=bvid
    expected=sum(hour<=now.hour for hour in HOURS)
    return dict(date=day,checked_at=now.isoformat(),expected_by_now=expected,daily_target=4,
        submitted_count=len(receipts),unique_verified_file_count=len(hashes),
        receipts=list(receipts.values()),duplicate_files=duplicates,missing_file_hashes=missing_hash,
        state_counter=state.get('daily_publish'),
        receipt_check_passed=(expected<=len(hashes)<=4 and not duplicates and not missing_hash))


def opening_stock_check(receipts, stock, now):
    """Before the first slot, require real clips for the remaining daily quota."""
    now=now.astimezone(BEIJING)
    if now.hour>=HOURS[0]:return None
    needed=max(0,4-receipts['unique_verified_file_count'])
    wide_needed=int(needed>0)
    ready=int(stock.get('daily_mix_usable', stock.get('verified_live') or 0))
    wide=int(stock.get('verified_landscape') or 0)
    portrait=int(stock.get('verified_portrait', max(0, ready-wide)))
    # Only Sunday's 21:00 slot may consume one reserved full interview.
    # It cannot cover another day's deficit or several daily slots at once.
    full=int(now.weekday()==6 and needed>wide_needed and stock.get('verified_weekly_full',0)>0)
    ready+=full
    portrait_needed=max(0,needed-wide_needed-full)
    return dict(required_live=needed,verified_live=ready,required_landscape=wide_needed,
        usable_weekly_full=full,
        required_portrait=portrait_needed,verified_portrait=portrait,
        verified_landscape=wide,inventory_fresh=stock.get('inventory_fresh') is True,
        passed=needed==0 or (stock.get('inventory_fresh') is True and ready>=needed
                            and wide>=wide_needed and portrait>=portrait_needed))


def experiment_stock_check(stock, now):
    """Check actual inspected drafts before today's remaining trial slots.

    A running job or an ordinary clip cannot fill a trial. The inventory has
    already deduplicated file hashes and excluded used/expired evidence.
    """
    import publishing_experiment as E
    now=now.astimezone(BEIJING);day=now.date().isoformat()
    remaining=[hour for hour in (14,21) if hour>now.hour
               and E.assignment(now.timestamp(),f'{day} {hour:02d}')]
    if not remaining and now.hour>=21:
        day=(now.date()+timedelta(days=1)).isoformat()
        remaining=[hour for hour in (14,21)
                   if E.assignment(now.timestamp(),f'{day} {hour:02d}')]
    if not remaining:return None
    ready=stock.get('experiment_ready') or {}
    wide=int(ready.get('landscape') or 0)
    portrait=int(ready.get('portrait') or 0)
    required_wide=int(14 in remaining)
    return dict(remaining_slots=[f'{day} {hour:02d}' for hour in remaining],
        required_distinct_drafts=len(remaining),verified_distinct_drafts=wide+portrait,
        required_landscape=required_wide,verified_landscape=wide,verified_portrait=portrait,
        inventory_fresh=stock.get('inventory_fresh') is True,
        passed=(stock.get('inventory_fresh') is True and wide>=required_wide
                and wide+portrait>=len(remaining)))


def experiment_recovery_needed(report):
    if not (report.get('experiment') or {}).get('active'):return False
    drafts=report.get('experiment_drafts') or {}
    executed=report['experiment']
    return bool(executed.get('missing_slots') or executed.get('noncompliant_slots')
                or drafts and not drafts.get('passed'))


def main():
    from register_selection_recovery import read_state
    from bili_archive_status import archive_status
    from fc.index import OWNER_MID,source_inventory
    _,state=read_state()
    now=datetime.now(BEIJING)
    report=daily_receipts(state,now)
    bvids=[r['bvid'] for r in report['receipts']]
    try:
        creator=archive_status(bvids,OWNER_MID,os.environ['BILIBILI_COOKIES']) if bvids else dict(videos=[],public_count=0)
        report['creator_verification']=creator
    except Exception as exc:
        creator=dict(videos=[],public_count=0,error_type=type(exc).__name__)
        report['creator_verification']=creator
    visible={r['bvid'] for r in creator.get('videos',[]) if r.get('public')}
    report['not_public_or_unconfirmed']=sorted(set(bvids)-visible)
    report['public_check_passed']=len(visible)>=report['expected_by_now']
    report['experiment']=experiment_receipts(state,now)
    report['passed']=(report['receipt_check_passed'] and report['public_check_passed']
                      and report['experiment']['passed'])
    stock=None
    if now.hour<HOURS[0] or report['experiment']['active']:
        stock=source_inventory(state)
    if report['experiment']['active']:
        report['experiment_drafts']=experiment_stock_check(stock or {},now)
        if report['experiment_drafts']:
            report['passed']=report['passed'] and report['experiment_drafts']['passed']
    if now.hour<HOURS[0]:
        report['opening_stock']=opening_stock_check(report,stock,now)
        report['passed']=report['passed'] and report['opening_stock']['passed']
    Path('daily-publication-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    summary=(f"{report['date']}：到当前应有 {report['expected_by_now']} 条；"
             f"实际独立文件 {report['unique_verified_file_count']} 条，确认公开 {len(visible)} 条。")
    print(summary,flush=True)
    if report['experiment']['active']:
        trial=report['experiment']
        summary+=(f" 实验片实际发布 {trial['treatment_published']} 条；"
                  f"缺失时段 {trial['missing_slots']}；分组未达标 {trial['noncompliant_slots']}。")
        print(json.dumps(trial,ensure_ascii=False),flush=True)
    if report.get('experiment_drafts'):
        drafts=report['experiment_drafts']
        summary+=(f" 待发实验稿需 {drafts['required_distinct_drafts']} 条独立成片，"
                  f"实际验收 {drafts['verified_distinct_drafts']} 条；"
                  f"库存新鲜：{drafts['inventory_fresh']}。")
        print(json.dumps(drafts,ensure_ascii=False),flush=True)
    if report.get('opening_stock'):
        opening=report['opening_stock']
        summary+=(f" 开播前库存需 {opening['required_live']} 条，已验收 {opening['verified_live']} 条；"
                  f"横屏需 {opening['required_landscape']} 条，已验收 {opening['verified_landscape']} 条；"
                  f"竖屏需 {opening['required_portrait']} 条，已验收 {opening['verified_portrait']} 条。")
        print(json.dumps(opening,ensure_ascii=False),flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write(summary+'\n\n')
            for row in report['receipts']:
                f.write(f"- [{row['bvid']}](https://www.bilibili.com/video/{row['bvid']})：{row['title']}\n")
    if not report['passed']:
        raise RuntimeError('每日发布/实验验收未通过：见库存、实际回执、重复文件、公开状态及实验分组；本检查不会重复投稿')


if __name__=='__main__':main()
