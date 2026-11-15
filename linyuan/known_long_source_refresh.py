"""Recheck unused long seeds before searching for more ordinary clip seeds.

Only exact public metadata and available native stream dimensions are checked.
No media download, ASR, model call, production request or approval happens here.
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import time

from seed_campaign import BASE, BVID, verify_seed
from source_admission_guard import restriction
from source_profile_feasibility import native_scene_unavailable
from verified_seed_snapshot import MAX_AGE_SECONDS

PER_PASS = 4
PER_DAY = 8


def candidates(seeds, state, scan, now):
    # Include unknown/failed old attempts, not just published files. A new
    # timestamp is never permission to replay a producer or reset its budget.
    attempted=set()
    for entry in state.get('dispatched') or []:
        for key in ('source_url','asset_url','video_id','key'):
            value=entry.get(key)
            if isinstance(value,str):attempted.update(re.findall(r'BV[A-Za-z0-9]{10}',value))
    checks=(scan.get('known_long_refresh') or {}).get('checks') or {}
    result=[]
    for seed in seeds:
        bvid=seed.get('bvid')
        if (not isinstance(bvid,str) or not BVID.fullmatch(bvid) or bvid in attempted
                or seed.get('metadata_provenance')!='bilibili_view_exact_id_author_pages'
                or seed.get('source_role')!='mother_candidate' or seed.get('direct_dispatch') is not True
                or type(seed.get('no_reprint')) is not int or seed['no_reprint']!=0
                or type(seed.get('owner_mid')) is not int or seed['owner_mid']<=0
                or seed.get('owner_mid') in {1700344493,275211725}
                or seed.get('reuse_hold_reason') or seed.get('diagnostic_only')
                or restriction('https://www.bilibili.com/video/'+bvid,seeds)
                or float((checks.get(bvid) or {}).get('next_check_at') or 0)>now):continue
        try:
            observed=datetime.fromisoformat(seed['checked_at'].replace('Z','+00:00'))
            if observed.tzinfo is None or now-observed.timestamp()<=MAX_AGE_SECONDS:continue
        except (KeyError,ValueError,TypeError,AttributeError):continue
        pages=seed.get('pages') or []
        if len(pages)!=1:continue  # Do not renew unmeasured collection siblings.
        if not any(isinstance(p,dict) and type(p.get('duration')) in (int,float)
                   and 1200<=p['duration']<=5400 and type(p.get('cid')) is int and p['cid']>0
                   and type(p.get('page')) is int and p['page']>0 for p in pages):continue
        result.append(seed)
    return sorted(result,key=lambda s:(s['checked_at'],s['bvid']))


def run(base=BASE, *, limit=PER_PASS, verify=None, streams=None, apply=True):
    from seed_growth import long_replenishment_budget,pause_provider
    from batch_delivery import write_json
    base=Path(base);now=time.time();day=datetime.fromtimestamp(now,timezone(timedelta(hours=8))).date().isoformat()
    report=dict(checked_count=0,refreshed_count=0,denied_count=0,rows=[],
        accepted_mp4_count=0,production_dispatch=False,media_approved=False,
        counting_policy='refresh_existing_parent_identity_not_new_seed_or_stock',applied=apply)
    if long_replenishment_budget(base)<=0:return report
    path=base/'.automation/seed_growth_scan.json'
    scan=json.loads(path.read_text()) if path.exists() else {}
    if (scan.get('verification_cooldown') or {}).get('until',0)>now:return report
    book=scan.setdefault('known_long_refresh',dict(attempts=[],checks={}))
    attempts=book.setdefault('attempts',[]);checks=book.setdefault('checks',{})
    today=[r for r in attempts if r.get('date')==day]
    budget=min(PER_PASS,max(0,int(limit)),max(0,PER_DAY-len(today)))
    if not budget:return report
    state=json.loads((base/'.automation/fc_state.json').read_text())
    data=json.loads((base/'bilibili_seeds.json').read_text())
    pool=candidates(data['seeds'],state,scan,now)[:budget]
    if not pool:return report
    op=None;deadline=time.monotonic()+75
    def client():
        nonlocal op
        if op is None:
            from ci_fetch_bilibili import opener
            op=opener()
        return op
    def read(url,referer=None,timeout=12):
        import urllib.request
        left=deadline-time.monotonic()
        if left<=0:raise TimeoutError('Known long-source metadata budget exhausted')
        request=urllib.request.Request(url,headers={'Referer':referer or 'https://www.bilibili.com/'})
        with client().open(request,timeout=max(.1,min(timeout,left))) as response:
            return response.read().decode('utf-8')
    if verify is None:verify=lambda lead:verify_seed(lead,fetch=read)
    if streams is None:
        def streams(bvid,page):
            # Reuse the producer's exact page resolver/stream chooser. Never
            # infer HD from a cover thumbnail, title, metadata or output card.
            from ci_fetch_bilibili import playurl_for_page
            actual=client()
            class Bounded:
                def open(self,request,timeout=30):
                    left=deadline-time.monotonic()
                    if left<=0:raise TimeoutError('Known long-source metadata budget exhausted')
                    return actual.open(request,timeout=max(.1,min(timeout,left)))
            return playurl_for_page(Bounded(),bvid,dict(cid=page['cid'],expected_duration=page['duration']))
    for seed in pool:
        if time.monotonic()>=deadline:break
        bvid=seed['bvid'];stamp=time.time()
        attempts.append(dict(bvid=bvid,date=day,ts=stamp))
        book['attempts']=attempts[-64:]
        checks[bvid]=dict(next_check_at=stamp+1800,outcome='metadata_unknown')
        if apply:write_json(path,scan)  # An interrupted request still consumes its bounded check.
        report['checked_count']+=1
        outcome=dict(bvid=bvid,status='technical_unknown')
        try:
            fresh=verify(dict(bvid=bvid,discovery='known_unused_long_seed_refresh'))
            if fresh.get('status')!='verified_metadata':
                outcome.update(error=fresh.get('error','metadata_not_verified'))
                restricted={k:fresh[k] for k in ('http_status','api_code') if k in fresh}
                if restricted.get('http_status') in {403,412,429} or restricted.get('api_code') in {-352,-412,-509}:
                    outcome.update(restricted,provider_restricted=True)
                    if apply:pause_provider(path,restricted)
                    report['rows'].append(outcome);break
            elif fresh.get('bvid')!=bvid or fresh.get('owner_mid')!=seed['owner_mid']:
                outcome['status']='owner_identity_changed'
            elif fresh.get('no_reprint')==1:
                seed.update({k:v for k,v in fresh.items() if k not in {'status','discovery'}})
                seed.update(source_role='catalog_only',direct_dispatch=False,media_verified=False,editorial_approved=False)
                report['denied_count']+=1;outcome['status']='rights_denied'
                checks[bvid]=dict(next_check_at=stamp+86400,outcome=outcome['status'])
            elif (type(fresh.get('no_reprint')) is int and fresh['no_reprint']==0
                  and fresh.get('direct_dispatch') is True
                  and not restriction(fresh.get('url'),data['seeds'])):
                matching=[p for p in fresh.get('pages',[]) if 1200<=p.get('duration',0)<=5400]
                if (len(fresh.get('pages',[]))!=1 or len(matching)!=1
                        or matching[0]['cid']!=seed['pages'][0]['cid']):
                    outcome['status']='page_identity_or_duration_changed'
                    report['rows'].append(outcome)
                    continue
                measured=[]
                for page in matching[:2]:
                    actual=streams(bvid,page);w,h=actual.get('width'),actual.get('height')
                    valid=type(w) is int and type(h) is int and min(w,h)>0
                    measured.append(dict(page=page['page'],cid=page['cid'],width=w,height=h,
                        native_geometry_available=bool(valid and native_scene_unavailable(w,h) is None)))
                if any(m['native_geometry_available'] for m in measured):
                    seed.update({k:v for k,v in fresh.items() if k not in {'status','discovery'}})
                    seed.update(media_verified=False,editorial_approved=False,
                        long_source_preflight=dict(checked_at=stamp,pages=measured,
                            origin='producer_exact_playurl_native_dimensions',media_approved=False))
                    report['refreshed_count']+=1;outcome.update(status='native_long_metadata_refreshed',pages=measured)
                else:
                    known=all(type(m['width']) is int and type(m['height']) is int
                              and min(m['width'],m['height'])>0 for m in measured)
                    outcome.update(status='native_scene_geometry_unavailable' if known
                                   else 'native_dimensions_unknown',pages=measured)
                checks[bvid]=dict(next_check_at=stamp+(1800 if outcome['status']=='native_dimensions_unknown' else 86400),
                                  outcome=outcome['status'])
        except Exception as exc:
            outcome.update(error=type(exc).__name__)
            code=getattr(exc,'code',None)
            if code in {403,412,429}:
                outcome.update(http_status=code,provider_restricted=True)
                if apply:pause_provider(path,dict(http_status=code))
        report['rows'].append(outcome)
        if apply:
            # pause_provider may have added the shared cooldown; preserve it.
            latest=json.loads(path.read_text());latest['known_long_refresh']=book
            write_json(path,latest)
            write_json(base/'bilibili_seeds.json',data)
        if outcome.get('provider_restricted'):break
    return report


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-only',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('known-long-source-refresh.json'))
    args=parser.parse_args();result=run(apply=not args.read_only)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
