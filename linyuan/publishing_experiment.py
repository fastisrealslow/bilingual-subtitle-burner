"""Thirty-day, fixed-slot editorial comparison; never a randomized CTR test.

Treatment means all three observable requirements actually passed, not merely
that a job requested the profile. Normal quality/rights/dedup gates still apply.
"""
import hashlib
import json
import os
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone

VERSION = 2026101801
EXPERIMENT_ID = 'field-story-30d-20261018'
BASELINE = 'baseline'
TREATMENT = 'field-story-v1'
START = '2026-10-18'
END_EXCLUSIVE = '2026-11-17'
HOURS = (10, 14, 16, 21)
MIN_MOTHER_SECONDS = 1200
CST = timezone(timedelta(hours=8))


def profile():
    value = os.environ.get('LINYUAN_EDITORIAL_PROFILE', BASELINE)
    if value not in (BASELINE, TREATMENT):
        raise ValueError('Unknown editorial experiment profile')
    return value


def assignment(now=None, makeup_slot=None):
    stamp = time.time() if now is None else now
    local = datetime.fromtimestamp(stamp, CST)
    slot = makeup_slot or local.strftime('%Y-%m-%d %H')
    try:
        parsed = datetime.strptime(slot, '%Y-%m-%d %H')
        if parsed.strftime('%Y-%m-%d %H') != slot or parsed.hour not in HOURS:
            return None
    except (ValueError, TypeError):
        return None
    if not START <= slot[:10] < END_EXCLUSIVE:
        return None
    ordinal = HOURS.index(parsed.hour) + 1
    return dict(experiment_id=EXPERIMENT_ID, version=VERSION, slot=slot,
                slot_number=ordinal, planned= TREATMENT if ordinal in (2, 4) else BASELINE)


def preparing(now=None):
    """Prepare stock one day early; do not alter the completed day's slots."""
    local = datetime.fromtimestamp(time.time() if now is None else now, CST).date()
    return date.fromisoformat(START)-timedelta(days=1) <= local < date.fromisoformat(END_EXCLUSIVE)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def _selection_rows(cues,candidates):
    rows=[]
    for index, pick in enumerate(candidates[:12]):
        text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
        # Never truncate an answer's context to fit the classifier.
        if len(text) > 2400 or sum(len(r['text']) for r in rows)+len(text)>12000:
            continue
        rows.append(dict(index=index, text=text))
    return rows


def selection_request(cues, candidates):
    rows=_selection_rows(cues,candidates)
    schema=dict(type='object',additionalProperties=False,required=['choices'],properties={
        'choices':dict(type='array',maxItems=6,items=dict(type='object',additionalProperties=False,
            required=['index','kind','subject_quote','event_quote','detail_quote'],properties={
                'index':dict(type='integer',minimum=0,maximum=max(0,len(candidates)-1)),
                'kind':dict(type='string',enum=['personal_story','company_case']),
                **{k:dict(type='string') for k in ('subject_quote','event_quote','detail_quote')}}))})
    prompt=('从下面已经有自然起止的完整访谈段落中，挑最多6条有具体看点的片段。'
        '只能选嘉宾自己的真实经历/选择/判断失误，或点名公司的具体事例；'
        '泛泛谈认知、长期投资、行业趋势不能仅靠出现“我”或公司名入选。'
        '保留主持人有信息的问题，不改写、不拼接、不制造冲突。'
        '逐字摘录讨论对象subject_quote、具体发生的事或选择event_quote、'
        '片内不同的一条原因/经过/细节detail_quote，三者都必须在该段原文中。'
        '不能从别的片段借事实，不能把主持人的假设当嘉宾结论。'
        '按陌生观众愿意看下去的顺序排列；没有就choices=[]。\n'
        +json.dumps(rows,ensure_ascii=False))
    return prompt, schema


def validate_choices(cues, candidates, response):
    response=json.loads(response) if isinstance(response,str) else response
    rows=response.get('choices') if isinstance(response,dict) else None
    if not isinstance(rows,list) or len(rows)>6:
        raise ValueError('Invalid story selection response')
    selected=[];seen=set();allowed={r['index'] for r in _selection_rows(cues,candidates)}
    for row in rows:
        if not isinstance(row,dict) or type(row.get('index')) is not int:
            raise ValueError('Story selection lacks an exact candidate index')
        i=row['index']
        if i in seen or i not in allowed:
            raise ValueError('Repeated/out-of-range story candidate')
        seen.add(i);pick=candidates[i]
        text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
        if row.get('kind') not in ('personal_story','company_case'):
            raise ValueError('Unknown story kind')
        for key,minimum in (('subject_quote',2),('event_quote',4),('detail_quote',4)):
            quote=row.get(key)
            if not isinstance(quote,str) or not minimum<=len(quote)<=140 or quote not in text:
                raise ValueError('Story evidence is not an intact quote in this candidate: '+key)
        if row['event_quote']==row['detail_quote']:
            raise ValueError('A repeated claim is not a story detail')
        evidence={k:row[k] for k in ('kind','subject_quote','event_quote','detail_quote')}
        selected.append({**pick,'story_selection':dict(version=VERSION,**evidence,
            transcript_sha256=digest(text))})
    return selected


def attach_proof(meta, cues, selection, mother_seconds):
    text=''.join(c['text'] for c in cues)
    if not isinstance(selection,dict) or selection.get('transcript_sha256')!=digest(text):
        raise ValueError('Story evidence does not match the actual retained source text')
    for key in ('subject_quote','event_quote','detail_quote'):
        if selection.get(key) not in text:
            raise ValueError('Story evidence was lost from the retained clip')
    proof=dict(version=VERSION,profile=TREATMENT,source_duration_sec=mother_seconds,
        selection=selection,title=meta.get('title'),source_sha256=meta.get('source_sha256'),
        video_sha256=(meta.get('fingerprints') or {}).get('sha256'),
        cover_sha256=(meta.get('cover_proof') or {}).get('sha256'))
    meta={**meta,'editorial_profile':TREATMENT,'experiment_proof':proof}
    issue=proof_error(meta)
    if issue:raise ValueError(issue)
    return meta


def proof_error(meta):
    if not isinstance(meta,dict):return 'Invalid treatment metadata'
    if meta.get('editorial_profile')!=TREATMENT:
        return 'No requested treatment profile'
    p=meta.get('experiment_proof') or {};cover=meta.get('cover_proof') or {}
    if not isinstance(p,dict) or not isinstance(cover,dict):return 'Invalid treatment proof'
    s=p.get('selection') or {}
    if not isinstance(s,dict):return 'Invalid story evidence'
    try:
        if (p.get('version')!=VERSION or p.get('profile')!=TREATMENT
                or not MIN_MOTHER_SECONDS<=float(p['source_duration_sec'])<=5400
                or s.get('version')!=VERSION or s.get('kind') not in ('personal_story','company_case')
                or not all(isinstance(s.get(k),str) and minimum<=len(s[k])<=140
                           for k,minimum in (('subject_quote',2),('event_quote',4),('detail_quote',4)))
                or not re.fullmatch('[a-f0-9]{64}',str(s.get('transcript_sha256') or ''))
                or s['event_quote']==s['detail_quote']):
            return 'Missing long-mother or grounded story evidence'
    except (TypeError,ValueError,KeyError):return 'Invalid story proof fields'
    for key,actual in (('title',meta.get('title')),('source_sha256',meta.get('source_sha256')),
                       ('video_sha256',(meta.get('fingerprints') or {}).get('sha256')),
                       ('cover_sha256',cover.get('sha256'))):
        if not actual or p.get(key)!=actual or (key!='title' and not re.fullmatch('[a-f0-9]{64}',str(actual))):
            return 'Story proof binding mismatch: '+key
    if (meta.get('cover_person_image_source')!='verified_source_frame'
            or meta.get('cover_person_image_verified') is not True
            or cover.get('style')!='scene' or cover.get('no_added_text') is not True
            or cover.get('feed_safe_face') is not True):
        return 'Treatment requires an approved natural scene cover, not a stock-portrait fallback'
    return None


def actual_profile(meta):
    return TREATMENT if not proof_error(meta) else BASELINE


def rank(meta, plan):
    if not plan:return 0
    return int(actual_profile(meta)!=plan['planned'])


def workflow_input(entry):
    return {'editorial_profile':TREATMENT} if entry.get('editorial_profile')==TREATMENT else {}


def long_candidate(candidate):
    extra=candidate.get('extra') or {}
    if isinstance(extra,str):
        try:extra=json.loads(extra)
        except ValueError:return False
    try:return MIN_MOTHER_SECONDS<=float(extra.get('duration') or 0)<=5400
    except (ValueError,TypeError,AttributeError):return False


def production_deficits(inventory,state,active_runs,now=None):
    if not preparing(now) or 'experiment_ready' not in inventory:return dict(portrait=0,landscape=0)
    ready=inventory.get('experiment_ready') or {}
    needs={layout:max(0,2-int(ready.get(layout) or 0)) for layout in ('portrait','landscape')}
    latest={e.get('slug'):e for e in state.get('dispatched') or []}
    seen=set()
    for run in active_runs:
        slug=str(run.get('display_title') or '').split(' · ')[-1]
        entry=latest.get(slug) or {};layout=entry.get('output_layout')
        if (run.get('status') in ('queued','in_progress') and slug not in seen
                and entry.get('editorial_profile')==TREATMENT and layout in needs):
            needs[layout]=max(0,needs[layout]-1);seen.add(slug)
    return needs


def receipt(meta, now=None, makeup_slot=None):
    plan=assignment(now,makeup_slot)
    if not plan:return None
    actual=actual_profile(meta)
    return {**plan,'actual':actual,'compliant':actual==plan['planned'],
            'reason':None if actual==plan['planned'] else 'qualified_stock_fallback',
            'content_type':meta.get('content_type'),
            'source_duration_sec':(meta.get('experiment_proof') or {}).get('source_duration_sec'),
            'selection_kind':((meta.get('experiment_proof') or {}).get('selection') or {}).get('kind')}


def observations(items, previous, state, now=None):
    """Actual receipt-defined groups; missing views never become zero plays."""
    now=time.time() if now is None else now
    receipts={}
    for info in (state.get('published') or {}).values():
        for p in info.get('parts') or []:
            r=p.get('publishing_experiment')
            if p.get('bvid') and isinstance(r,dict) and r.get('experiment_id')==EXPERIMENT_ID:
                receipts[p['bvid']]={**r,'bvid':p['bvid'],'title':p.get('title'),
                    'published_at':p.get('ts'),'duration_sec':p.get('duration_sec'),
                    'source_url':info.get('source_url'),'source_sha256':p.get('source_sha256'),
                    'render_mode':p.get('render_mode'),'resolution':p.get('resolution')}
    old=previous.get('publishing_experiment') or {};samples={}
    for r in old.get('samples') or []:
        if r.get('bvid') in receipts and r.get('window') in (24,72,168):
            samples[(r['bvid'],r['window'])]=dict(r)
    for item in items:
        b=item.get('bvid');pub=item.get('pubdate');play=item.get('play')
        if b not in receipts or type(play) is not int or play<0 or type(pub) not in (int,float):continue
        age=(now-pub)/3600
        for window in (24,72,168):
            key=b,window
            if window<=age<window+12 and key not in samples:
                r=receipts[b]
                samples[key]=dict(bvid=b,window=window,observed_at=int(now),age_hours=round(age,3),
                    play=play,like=item.get('like'),danmaku=item.get('danmaku'),
                    planned=r['planned'],actual=r['actual'],compliant=r['compliant'],
                    slot_number=r['slot_number'],content_type=r.get('content_type'))
    groups=[]
    for planned in (BASELINE,TREATMENT):
        for window in (24,72,168):
            rows=[r for r in samples.values() if r['planned']==planned and r['window']==window
                  and r['compliant'] and r.get('content_type')!='full_interview']
            plays=[r['play'] for r in rows]
            groups.append(dict(planned=planned,window=window,count=len(rows),
                mean=round(statistics.mean(plays),2) if plays else None,
                median=statistics.median(plays) if plays else None))
    local=datetime.fromtimestamp(now,CST).date().isoformat()
    days=max(0,min(30,(date.fromisoformat(local)-date.fromisoformat(START)).days))
    by_slot={r['slot']:r for r in receipts.values()}
    daily=[]
    for day in range(30):
        value=date.fromisoformat(START)+timedelta(days=day)
        due=[f'{value.isoformat()} {h:02d}' for h in HOURS
             if datetime(value.year,value.month,value.day,h,tzinfo=CST).timestamp()<=now]
        if not due:continue
        present=[by_slot[s] for s in due if s in by_slot]
        daily.append(dict(date=value.isoformat(),slots_due=len(due),published=len(present),
                          missing_slots=[s for s in due if s not in by_slot],
                          compliant_treatment=sum(r['planned']==TREATMENT and r['compliant'] for r in present),
                          fallback=sum(not r['compliant'] for r in present)))
    return dict(experiment_id=EXPERIMENT_ID,version=VERSION,start=START,end_exclusive=END_EXCLUSIVE,
        status='scheduled' if local<START else 'collecting' if local<END_EXCLUSIVE else 'followup',
        completed_calendar_days=days,review_due=local>=END_EXCLUSIVE,
        daily=daily,
        receipts=sorted(receipts.values(),key=lambda r:r['slot']),
        fallback_count=sum(not r['compliant'] for r in receipts.values()),
        samples=sorted(samples.values(),key=lambda r:(r['bvid'],r['window'])),groups=groups,
        scope='Fixed Beijing slots 1/3 baseline, 2/4 treatment; not randomized. '
              'Search-index cumulative views at ages 24–36h / 72–84h / 168–180h; '
              'not CTR, impressions, retention or causal uplift. Missing is null. '
              'Fallbacks and full interviews excluded from primary clip groups; '
              'slot/time, layout, topic, source and duration remain confounders.')
