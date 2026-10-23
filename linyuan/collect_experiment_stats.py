"""Bounded own-creator observations; never upload or infer missing plays."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
from bili_archive_status import archive_status

import publishing_experiment as experiment

TOLERANCE_HOURS=2
WINDOWS=(24,72,168)
OWNER_MID=275211725
ROOT=Path(__file__).resolve().parents[1]


def number(value):
    return type(value) in (int,float) and math.isfinite(value)


def age_fits(age, window):
    return number(age) and window<=age<window+TOLERANCE_HOURS


def reconcile(items, previous, state, now=None):
    now=time.time() if now is None else now
    result=experiment.observations([],previous,state,now)
    old=previous.get('publishing_experiment') or {}
    # Keep legacy search samples AND their old groups unchanged. The new
    # clock_samples are a separate protocol, not a relabelling/backfill/reset.
    result['legacy_groups']=result['groups'];result['legacy_by_slot']=result['by_slot']
    result['legacy_scope']=result['scope']
    receipts={r['bvid']:r for r in result['receipts']}
    samples={(r.get('experiment_id'),r['bvid'],r['window']):dict(r) for r in old.get('clock_samples',[])
             if isinstance(r,dict) and r.get('bvid') and r.get('window') in WINDOWS}
    for item in items:
        bvid=item.get('bvid');pub=item.get('pubdate');observed=item.get('observed_at');play=item.get('play')
        if (bvid not in receipts or item.get('observation_source')!='bilibili_creator_archive'
                or item.get('publication_clock_basis') not in ('creator_pubdate','creator_ptime')
                or not number(pub) or pub<=0 or not number(observed) or not 0<observed<=now
                or now-observed>600 or type(play) is not int or play<0):continue
        age=(observed-pub)/3600
        for window in WINDOWS:
            key=(experiment.EXPERIMENT_ID,bvid,window)
            if key in samples or not age_fits(age,window):continue
            receipt=receipts[bvid]
            samples[key]=dict(experiment_id=experiment.EXPERIMENT_ID,bvid=bvid,window=window,observed_at=observed,
                pubdate=pub,age_hours=age,play=play,like=item.get('like'),danmaku=item.get('danmaku'),
                publication_clock_basis=item['publication_clock_basis'],
                planned=receipt['planned'],actual=receipt['actual'],compliant=receipt['compliant'],
                slot_number=receipt['slot_number'],content_type=receipt.get('content_type'),
                observation_source='bilibili_creator_archive')
    for row in samples.values():
        timing=(row.get('window') in WINDOWS and age_fits(row.get('age_hours'),row['window'])
                and number(row.get('observed_at')) and 0<row['observed_at']<=now)
        row['comparable']=(timing and type(row.get('play')) is int and row['play']>=0
            and row.get('experiment_id')==experiment.EXPERIMENT_ID
            and row.get('observation_source')=='bilibili_creator_archive'
            and row.get('publication_clock_basis') in ('creator_pubdate','creator_ptime')
            and row.get('compliant') is True and row.get('actual')==row.get('planned')
            and row.get('planned') in (experiment.BASELINE,experiment.TREATMENT)
            and row.get('content_type')!='full_interview')
        row['comparison_exclusion']=None if row['comparable'] else (
            'outside_observation_clock' if not timing else 'not_compliant_clip_or_unknown_plays')
    result['clock_samples']=sorted(samples.values(),key=lambda r:(r['bvid'],r['window']))
    valid=[r for r in samples.values() if r['comparable']]
    def group(rows, **labels):
        plays=[r['play'] for r in rows]
        return dict(**labels,count=len(plays),mean=round(statistics.mean(plays),2) if plays else None,
                    median=statistics.median(plays) if plays else None)
    result['groups']=[group([r for r in valid if r['planned']==p and r['window']==w],planned=p,window=w)
        for p in (experiment.BASELINE,experiment.TREATMENT) for w in WINDOWS]
    result['by_slot']=[group([r for r in valid if r['slot_number']==s and r['window']==w],slot_number=s,window=w)
        for s in (1,2,3,4) for w in WINDOWS]
    result.update(observation_protocol=2,sampling_age_tolerance_hours=TOLERANCE_HOURS,
        scope='Fixed Beijing slots, not randomized. Primary groups use actual cumulative views '
              'observed at ages 24–26h / 72–74h / 168–170h, not exact target-hour counts. '
              'Only receipt-bound authenticated own-creator counters enter primary groups. '
              'Legacy search samples/groups remain visible separately, excluded from primary groups. '
              'Missing stays unknown; no retrospective backfill. Not CTR, retention or causal uplift. '
              'Slot/time, layout, topic, source and duration remain confounders.')
    if old.get('sampling_health'):result['sampling_health']=old['sampling_health']
    return result


def due_receipts(previous,state,now):
    base=experiment.observations([],previous,state,now)
    seen={(r['bvid'],r['window']) for r in (previous.get('publishing_experiment') or {}).get('clock_samples',[])
          if r.get('experiment_id')==experiment.EXPERIMENT_ID}
    due=[]
    for receipt in base['receipts']:
        published=receipt.get('published_at')
        if not number(published) or published<=0:continue
        age=(now-published)/3600
        # Submission and public clocks may differ. Query near the window,
        # but only the actual public pubdate may qualify a measurement.
        if any(w-1<=age<w+TOLERANCE_HOURS+1 and (receipt['bvid'],w) not in seen for w in WINDOWS):
            due.append(receipt['bvid'])
    return list(dict.fromkeys(due))[:12]


def parse_creator(data,bvid):
    if not isinstance(data,dict):raise ValueError('Creator video response is not an object')
    counters=data.get('statistics')
    if (not isinstance(counters,dict) or data.get('statistics_origin')!='authenticated-creator-read'
            or data.get('bvid')!=bvid or str(data.get('mid'))!=str(OWNER_MID)
            or data.get('public') is not True or type(data.get('state')) is not int or data['state']!=0):
        raise ValueError('Authenticated own-public-video identity unavailable or mismatched')
    play=counters.get('view');pub=data.get('pubdate');observed=data.get('observed_at')
    ptime=(data.get('publication_clock_candidates') or {}).get('ptime')
    if number(pub) and pub>0 and number(ptime) and ptime>0 and pub!=ptime:
        raise ValueError('Creator publication clocks conflict; no inferred clock allowed')
    basis='creator_pubdate'
    if not number(pub) or pub<=0:
        # Actual authenticated Archive.ptime equals public view.pubdate.
        # Archive.ctime was 62 seconds earlier in the real probe; never use it.
        pub=ptime;basis='creator_ptime'
    if (type(play) is not int or play<0 or not number(pub) or pub<=0
            or not number(observed) or observed<=0):
        raise ValueError('Creator play count or publication/observation clock is unknown')
    return dict(bvid=bvid,play=play,pubdate=pub,title=data.get('title'),observed_at=observed,
        publication_clock_basis=basis,
        observation_source='bilibili_creator_archive',like=counters.get('like'),danmaku=counters.get('danmaku'))


def fetch_views(bvids):
    if len(bvids)>12 or any(not re.fullmatch(r'BV[A-Za-z0-9]{10}',b) for b in bvids):
        raise ValueError('Invalid or unbounded receipt video IDs')
    cookies=os.environ.get('BILIBILI_COOKIES')
    if not cookies:raise ValueError('Existing own-creator credentials unavailable')
    result=archive_status(bvids,OWNER_MID,cookies,include_statistics=True)
    rows={r['bvid']:r for r in result.get('videos',[]) if r.get('bvid') in bvids}
    items=[];errors=[]
    for bvid in bvids:
        row=rows.get(bvid,{})
        try:items.append(parse_creator(row,bvid))
        except ValueError as exc:
            errors.append(dict(bvid=bvid,error_type=type(exc).__name__,
                api_code=result.get('api_code'),http_status=result.get('http_status'),
                available_stat_fields=row.get('available_stat_fields',[]),
                available_archive_fields=row.get('available_archive_fields',[]),
                publication_clock_candidates=row.get('publication_clock_candidates',{})))
    return items,errors


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe-bvid',action='append')
    args=parser.parse_args()
    if args.probe_bvid:
        items,errors=fetch_views(args.probe_bvid)
        if errors:raise ValueError(json.dumps(errors))
        print(json.dumps(dict(passed=True,diagnostic_only=True,sample_registered=False,videos=items)))
        return
    out=ROOT/'site/stats.json';state_path=ROOT/'linyuan/.automation/fc_state.json'
    previous=json.loads(out.read_text());state=json.loads(state_path.read_text());now=time.time()
    due=due_receipts(previous,state,now)
    if not due:
        # Receipt/group projection must not wait until a video is 24h old.
        # This imports actual publication receipts only: no counter request,
        # fabricated zero, late backfill or observation-clock changes.
        result=reconcile([],previous,state,now)
        changed=previous.get('publishing_experiment')!=result
        if changed:
            previous['publishing_experiment']=result
            out.write_text(json.dumps(previous,ensure_ascii=False,indent=1))
        print(json.dumps(dict(due=0,changed=changed)));return
    try:items,errors=fetch_views(due)
    except (OSError,ValueError,TypeError) as exc:
        items=[];errors=[dict(bvid=b,error_type=type(exc).__name__) for b in due]
    completed=time.time()
    result=reconcile(items,previous,state,completed)
    result['sampling_health']=dict(observed_at=int(completed),attempted=len(due),fetched=len(items),
        errors=errors,missing_is_zero=False)
    previous['publishing_experiment']=result
    out.write_text(json.dumps(previous,ensure_ascii=False,indent=1))
    print(json.dumps(dict(due=len(due),fetched=len(items),errors=errors,
        sample_count=len(result['clock_samples']),comparable=sum(r['comparable'] for r in result['clock_samples']))))
    if errors:
        raise RuntimeError('Creator counters unavailable for some receipts; prior observations retained, no zero/backfill')


if __name__=='__main__':main()
