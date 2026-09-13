"""Read actual daily receipts and creator visibility; never upload or retry."""
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path

BEIJING=timezone(timedelta(hours=8))
HOURS=(10,14,16,21)


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
    ready=int(stock.get('verified_live') or 0)
    wide=int(stock.get('verified_landscape') or 0)
    return dict(required_live=needed,verified_live=ready,required_landscape=wide_needed,
        verified_landscape=wide,inventory_fresh=stock.get('inventory_fresh') is True,
        passed=needed==0 or (stock.get('inventory_fresh') is True and ready>=needed and wide>=wide_needed))


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
    report['passed']=report['receipt_check_passed'] and report['public_check_passed']
    if now.hour<HOURS[0]:
        report['opening_stock']=opening_stock_check(report,source_inventory(state),now)
        report['passed']=report['passed'] and report['opening_stock']['passed']
    Path('daily-publication-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    summary=(f"{report['date']}：到当前应有 {report['expected_by_now']} 条；"
             f"实际独立文件 {report['unique_verified_file_count']} 条，确认公开 {len(visible)} 条。")
    print(summary,flush=True)
    if report.get('opening_stock'):
        opening=report['opening_stock']
        summary+=(f" 开播前库存需 {opening['required_live']} 条，已验收 {opening['verified_live']} 条；"
                  f"横屏需 {opening['required_landscape']} 条，已验收 {opening['verified_landscape']} 条。")
        print(json.dumps(opening,ensure_ascii=False),flush=True)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:
            f.write(summary+'\n\n')
            for row in report['receipts']:
                f.write(f"- [{row['bvid']}](https://www.bilibili.com/video/{row['bvid']})：{row['title']}\n")
    if not report['passed']:
        raise RuntimeError('每日4条验收未通过：见开播前库存、实际回执、重复文件和公开状态；本检查不会重复投稿')


if __name__=='__main__':main()
