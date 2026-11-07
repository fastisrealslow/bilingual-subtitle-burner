"""Read-only real creator/seed checks before release; never upload or admit seeds."""
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
from bili_archive_status import archive_status
from seed_campaign import metadata_session, verify_seed


def main():
    title='林园：现在港股不跟美股，跟着 A 股走，两地上市除非特好不买'
    report=dict(checked_at=time.time(),state_mutated=False,production_dispatch=False,
        media_approved=False,seed_added_count=0)
    report['creator']=archive_status([],275211725,os.environ['BILIBILI_COOKIES'],
        expected_titles=[title],max_pages=12,scan_all=True)
    scan=json.loads((ROOT/'linyuan/.automation/seed_growth_scan.json').read_text())
    cooldown=scan.get('verification_cooldown') or {}
    if cooldown.get('until',0)>time.time():
        report['metadata']=dict(skipped=True,reason='provider_cooldown',until=cooldown['until'])
    else:
        queue=json.loads((ROOT/'linyuan/.automation/seed_growth_leads.json').read_text())['leads']
        leads=[r for r in queue if float(r.get('verification_retry_after') or 0)<=time.time()][:3]
        request=metadata_session();rows=[]
        for lead in leads:
            row=verify_seed(lead,fetch=request)
            rows.append({k:v for k,v in row.items() if k in ['bvid','status','error','http_status',
                'api_code','metadata_provenance','checked_at','title','pages','source_role','direct_dispatch']})
            if row.get('http_status') in {403,412,429} or row.get('api_code') in {-352,-412,-509}:break
            time.sleep(.5)
        report['metadata']=dict(rows=rows,verified_count=sum(r['status']=='verified_metadata' for r in rows))
    Path('production-recovery-preflight.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))
    if (report['creator'].get('api_code')!=0 or report['creator'].get('response_error')
            or report['creator'].get('owner_verified') is not True
            or not report['creator'].get('scan_complete') and not report['creator'].get('found_count')):
        raise SystemExit('Creator archive read is incomplete; do not infer absence')


if __name__=='__main__':main()
