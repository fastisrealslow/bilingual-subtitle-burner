"""Bounded receipt-based public observations; never upload or infer missing plays."""
import argparse
import json
import math
from pathlib import Path
import re
import statistics
import time
import urllib.error
import urllib.request

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
        if (bvid not in receipts or item.get('observation_source')!='bilibili_public_view'
                or not number(pub) or pub<=0 or not number(observed) or not 0<observed<=now
                or now-observed>600 or type(play) is not int or play<0):continue
        age=(observed-pub)/3600
        for window in WINDOWS:
            key=(experiment.EXPERIMENT_ID,bvid,window)
            if key in samples or not age_fits(age,window):continue
            receipt=receipts[bvid]
            samples[key]=dict(experiment_id=experiment.EXPERIMENT_ID,bvid=bvid,window=window,observed_at=observed,
                pubdate=pub,age_hours=age,play=play,like=item.get('like'),danmaku=item.get('danmaku'),
                planned=receipt['planned'],actual=receipt['actual'],compliant=receipt['compliant'],
                slot_number=receipt['slot_number'],content_type=receipt.get('content_type'),
                observation_source='bilibili_public_view')
    for row in samples.values():
        timing=(row.get('window') in WINDOWS and age_fits(row.get('age_hours'),row['window'])
                and number(row.get('observed_at')) and 0<row['observed_at']<=now)
        row['comparable']=(timing and type(row.get('play')) is int and row['play']>=0
            and row.get('experiment_id')==experiment.EXPERIMENT_ID
            and row.get('observation_source')=='bilibili_public_view'
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
              'Only receipt-bound public-view counters enter primary groups. '
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


def parse_view(payload,bvid):
    if not isinstance(payload,dict):raise ValueError('Public API response is not an object')
    data=payload.get('data') or {}
    if (not isinstance(data,dict) or not isinstance(data.get('owner'),dict)
            or not isinstance(data.get('stat'),dict)):
        raise ValueError('Public API video/owner/counter shape is unavailable')
    if (type(payload.get('code')) is not int or payload['code']!=0 or data.get('bvid')!=bvid
            or type(data.get('state')) is not int or data['state']!=0
            or data['owner'].get('mid')!=OWNER_MID):
        raise ValueError('Public video/owner identity unavailable or mismatched')
    play=(data.get('stat') or {}).get('view');pub=data.get('pubdate')
    if type(play) is not int or play<0 or not number(pub) or pub<=0:
        raise ValueError('Public play count or publication clock is unknown')
    return dict(bvid=bvid,play=play,pubdate=pub,title=data.get('title'),
        like=(data.get('stat') or {}).get('like'),danmaku=(data.get('stat') or {}).get('danmaku'))


def fetch_view(bvid):
    if not re.fullmatch(r'BV[A-Za-z0-9]{10}',bvid):raise ValueError('Invalid receipt video ID')
    url='https://api.bilibili.com/x/web-interface/view?bvid='+bvid
    for attempt in range(2):
        try:
            request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(request,timeout=12) as response:
                item=parse_view(json.load(response),bvid)
                return dict(item,observed_at=time.time(),observation_source='bilibili_public_view')
        except urllib.error.HTTPError as exc:
            if exc.code<500 or attempt:raise
        except (TimeoutError,urllib.error.URLError):
            if attempt:raise
        time.sleep(1)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--probe-bvid')
    args=parser.parse_args()
    if args.probe_bvid:
        item=fetch_view(args.probe_bvid)
        print(json.dumps(dict(passed=True,diagnostic_only=True,sample_registered=False,video=item)))
        return
    out=ROOT/'site/stats.json';state_path=ROOT/'linyuan/.automation/fc_state.json'
    previous=json.loads(out.read_text());state=json.loads(state_path.read_text());now=time.time()
    due=due_receipts(previous,state,now)
    if not due:
        print(json.dumps(dict(due=0,changed=False)));return
    items=[];errors=[]
    for bvid in due:
        try:items.append(fetch_view(bvid))
        except (OSError,ValueError,TypeError) as exc:
            errors.append(dict(bvid=bvid,error_type=type(exc).__name__))
    completed=time.time()
    result=reconcile(items,previous,state,completed)
    result['sampling_health']=dict(observed_at=int(completed),attempted=len(due),fetched=len(items),
        errors=errors,missing_is_zero=False)
    previous['publishing_experiment']=result
    out.write_text(json.dumps(previous,ensure_ascii=False,indent=1))
    print(json.dumps(dict(due=len(due),fetched=len(items),errors=errors,
        sample_count=len(result['clock_samples']),comparable=sum(r['comparable'] for r in result['clock_samples']))))
    if errors:
        raise RuntimeError('Public counters unavailable for some receipts; prior observations retained, no zero/backfill')


if __name__=='__main__':main()
