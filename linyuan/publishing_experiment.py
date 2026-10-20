"""Thirty-day, fixed-slot editorial comparison; never a randomized CTR test.

Treatment means all three observable requirements actually passed, not merely
that a job requested the profile. Normal quality/rights/dedup gates still apply.
"""
import hashlib
import json
import math
import os
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone

VERSION = 2026101801
SELECTION_REVISION = 2026102004
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


def _substantive_quote(quote, minimum):
    plain=re.sub(r'[^\w\u4e00-\u9fff]','',quote)
    return (len(quote)>=minimum and len(plain)>=max(2,minimum-1)
        and not re.fullmatch(r'(?:嗯|啊|呃|哦|噢|好呀|好啊|好吧|好的|是吧|是的|对|对吧|对的|没错|谢谢您|谢谢|好|呀)+',plain))


def _selection_rows(cues,candidates):
    rows=[]
    for index, pick in enumerate(candidates[:12]):
        text=''.join(c['text'] for c in cues[pick['start']:pick['end']+1])
        # Never truncate an answer's context to fit the classifier.
        if len(text) > 2400 or sum(len(r['text']) for r in rows)+len(text)>12000:
            continue
        # ASR display cues can split a word ("一定的" / "水平"). They are
        # NOT grammatical evidence boundaries. Rejoin the complete answer,
        # keep natural sentences, and only split >140-character sentences at
        # their real comma/semicolon boundaries. No words/context are dropped.
        units=[]
        for match in re.finditer(r'.+?(?:[。！？!?]+[”’」』\"]*|$)',text,re.S):
            value=match.group(0)
            units.extend(re.findall(r'.+?(?:[，,；;]+|$)',value,re.S) if len(value)>140 else [value])
        sentences=[dict(id=i,text=value) for i,value in enumerate(units)]
        if not any(len(s['text'])<=140 and _substantive_quote(s['text'],4)
                   and not re.search(r'[？?]',s['text']) for s in sentences):continue
        rows.append(dict(index=index,text=text,sentences=sentences))
    return rows


def selection_request(cues, candidates):
    rows=_selection_rows(cues,candidates)
    properties={}
    for row in rows:
        properties['candidate_'+str(row['index'])]=dict(type='object',additionalProperties=False,
            required=['kind','subject_sentence','event_sentence','detail_sentence'],properties={
                'kind':dict(type='string',enum=['skip','personal_story','company_case']),
                **{k:dict(type='integer',enum=[s['id'] for s in row['sentences'] if len(s['text'])<=140
                    and _substantive_quote(s['text'],2 if k=='subject_sentence' else 4)
                    and (k=='subject_sentence' or not re.search(r'[？?]',s['text']))])
                   for k in ('subject_sentence','event_sentence','detail_sentence')}})
    schema=dict(type='object',additionalProperties=False,required=list(properties),properties=properties)
    prompt=('逐个判断下面已经有自然起止的完整访谈段落，是否有具体看点。'
        '只能选嘉宾自己的真实经历/选择/判断失误，或点名公司的具体事例；'
        '泛泛谈认知、长期投资、行业趋势不能仅靠出现“我”或公司名入选。'
        'ETF、指数或基金的一般调仓机制不是具体公司案例，必须skip；公司案例的对象原句必须点名具体公司。'
        '保留主持人有信息的问题，不改写、不拼接、不制造冲突。'
        '每个candidate_N只返回一次：kind为personal_story、company_case或skip。'
        '选择本段中的原句编号：subject_sentence交代具体对象；event_sentence是嘉宾'
        '说的具体事情或选择；detail_sentence是另一条原话原因/经过/细节，不能与event_sentence相同。'
        '不能从别的片段借事实，不能把主持人的假设当嘉宾结论。'
        '事件必须是嘉宾讲出的具体行为、经历或公司发生的事；“机会存在”等抽象总结不是事件。'
        '细节必须提供实质性原因、经过或后续选择；“嗯、好呀、是吧”等主持人应和不是细节。'
        '没有这三项就kind=skip，其余编号填任意允许值。不输出摘录文本。\n'
        +json.dumps([dict(index=r['index'],sentences=r['sentences']) for r in rows],ensure_ascii=False))
    return prompt, schema


def validate_choices(cues, candidates, response):
    response=json.loads(response) if isinstance(response,str) else response
    source_rows=_selection_rows(cues,candidates)
    if isinstance(response,dict) and 'choices' not in response:
        expected={'candidate_'+str(r['index']) for r in source_rows}
        if set(response)!=expected:raise ValueError('Story classifier omitted or invented a candidate')
        choices=[]
        for row in source_rows:
            verdict=response['candidate_'+str(row['index'])]
            if not isinstance(verdict,dict):raise ValueError('Invalid per-candidate story verdict')
            if verdict.get('kind')=='skip':continue
            units={s['id']:s['text'] for s in row['sentences'] if 2<=len(s['text'])<=140}
            choice=dict(index=row['index'],kind=verdict.get('kind'))
            for role in ('subject','event','detail'):
                index=verdict.get(role+'_sentence')
                if type(index) is not int or index not in units:
                    raise ValueError('Story sentence ID outside this candidate')
                choice[role+'_quote']=units[index]
            choices.append(choice)
        response=dict(choices=choices[:6])
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
            if not _substantive_quote(quote,minimum):
                raise ValueError('Story evidence must contain a substantive source detail, not a filler')
            if key!='subject_quote' and re.search(r'[？?]',quote):
                raise ValueError('A question is context, not guest event/detail evidence')
        if row['event_quote']==row['detail_quote']:
            raise ValueError('A repeated claim is not a story detail')
        if row['kind']=='company_case' and not title_focus(row)['subjects']:
            raise ValueError('Named company subject required; generic assets/rules are not a company case')
        evidence={k:row[k] for k in ('kind','subject_quote','event_quote','detail_quote')}
        selected.append({**pick,'story_selection':dict(version=VERSION,**evidence,
            transcript_sha256=digest(text))})
    return selected


def choose_stories(cues, candidates, call, path, *, attempts=2, budget_sec=1200):
    """Isolate candidate errors and retry only unreviewed/invalid decisions.

    Every accepted row still uses the strict source-bound validator. A bad
    neighbour, omitted row or service timeout cannot erase a valid decision.
    Raw requests/responses and unresolved rows survive partial success.
    """
    from pathlib import Path
    report=dict(revision=SELECTION_REVISION,requested_profile=TREATMENT,
        transcript_sha256=digest(cues),selected=[],rejected=[],unresolved=[],calls=[])
    def save():
        if path is not None:
            target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    # Two full answers per call keep CPU context bounded. Do not truncate any
    # answer or supply manually approved ranges/titles to repair model output.
    rows=_selection_rows(cues,candidates)
    deadline=time.monotonic()+budget_sec
    for start in range(0,len(rows),2):
        if time.monotonic()>=deadline:
            report['unresolved'].extend(dict(index=r['index'],error='selection_budget_exhausted') for r in rows[start:])
            break
        pending=[dict(index=r['index'],error=None) for r in rows[start:start+2]]
        for attempt in range(attempts):
            if time.monotonic()>=deadline:
                pending=[dict(index=r['index'],error='selection_budget_exhausted') for r in pending]
                break
            picks=[candidates[r['index']] for r in pending]
            prompt,schema=selection_request(cues,picks)
            errors=[dict(candidate=i,error=r['error']) for i,r in enumerate(pending) if r['error']]
            if errors:prompt+='\n仅重试以下未通过的候选；事件与细节必须选不同原句，否则skip。错误：'+json.dumps(errors,ensure_ascii=False)
            record=dict(indices=[r['index'] for r in pending],attempt=attempt+1,
                        prompt=prompt,schema=schema)
            report['calls'].append(record);save()
            try:
                raw=call(prompt,schema);record['response']=raw
                data=json.loads(raw) if isinstance(raw,str) else raw
                if not isinstance(data,dict) or set(data)-set(schema['properties']):
                    raise ValueError('Story classifier invented candidate keys')
            except (RuntimeError,ValueError,TypeError) as exc:
                record['error']=str(exc)
                pending=[dict(index=r['index'],error=str(exc)) for r in pending]
                save();continue
            again=[]
            for local,row in enumerate(pending):
                verdict=data.get('candidate_'+str(local))
                try:
                    # Remap one actual source candidate, not the whole batch.
                    selected=validate_choices(cues,[picks[local]],{'candidate_0':verdict})
                    if selected:report['selected'].extend(selected)
                    else:report['rejected'].append(dict(index=row['index'],reason='no_grounded_story'))
                except (ValueError,TypeError,KeyError,AttributeError) as exc:
                    again.append(dict(index=row['index'],error=str(exc)))
            pending=again;save()
            if not pending:break
        report['unresolved'].extend(pending);save()
    report['selection_completed']=not report['unresolved']
    report['retryable']=bool(report['unresolved'])
    # The existing final production limit is six accepted videos. Keep the
    # source reader bounded too, while preserving diagnostics for every row.
    report['selected']=report['selected'][:6]
    save()
    return report


def production_result(requested, metas, selection=None):
    actual=sum(actual_profile(m)==TREATMENT for m in metas)
    return dict(requested=requested,selection_revision=SELECTION_REVISION,
        experiment_accepted=actual,baseline_accepted=len(metas)-actual,
        experiment_complete=requested==TREATMENT and actual>0,
        fallback=requested==TREATMENT and bool(metas) and actual==0,
        retryable_selection=bool((selection or {}).get('unresolved')))


def operational_status(state, ready, now=None):
    """Receipt-defined execution health, not a playback-performance claim."""
    now=time.time() if now is None else now
    receipts={}
    for info in (state.get('published') or {}).values():
        for part in info.get('parts') or []:
            row=part.get('publishing_experiment')
            if (part.get('status')=='published' and part.get('bvid') and isinstance(row,dict)
                    and row.get('experiment_id')==EXPERIMENT_ID):
                receipts[row['slot']]=row
    planned=sorted((r for r in receipts.values() if r.get('planned')==TREATMENT),key=lambda r:r['slot'])
    streak=0
    for row in reversed(planned):
        if row.get('actual')==TREATMENT and row.get('compliant'):break
        streak+=1
    daily=[]
    for day in sorted({r['slot'][:10] for r in receipts.values()}):
        rows=[r for r in receipts.values() if r['slot'].startswith(day)]
        treatment=sum(r.get('planned')==TREATMENT and r.get('actual')==TREATMENT and r.get('compliant') for r in rows)
        controls=sum(r.get('planned')==BASELINE and r.get('actual')==BASELINE and r.get('compliant')
                     and r.get('content_type')!='full_interview' for r in rows)
        daily.append(dict(date=day,published=len(rows),treatment=treatment,controls=controls,
            fallback=sum(not r.get('compliant') for r in rows),valid_comparison=len(rows)==4 and treatment==2 and controls==2))
    fresh=ready is not None
    total=sum(ready.values()) if fresh else None
    alert=('unknown_stock' if not fresh else 'consecutive_fallbacks' if streak>=2 else
           'no_verified_treatment' if total==0 else
           'insufficient_treatment_by_layout' if any(int(ready.get(layout) or 0)<2
               for layout in ('portrait','landscape')) else None)
    return dict(experiment_id=EXPERIMENT_ID,selection_revision=SELECTION_REVISION,
        active=preparing(now),verified_treatment=ready,consecutive_fallbacks=streak,
        actual_treatment_published=sum(r.get('compliant') and r.get('actual')==TREATMENT for r in planned),
        valid_comparison_days=sum(d['valid_comparison'] for d in daily),daily=daily,
        status='inactive' if not preparing(now) else 'needs_attention' if alert else 'ready',alert=alert)


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


def story_title_prompt(prompt,schema,evidence):
    """Change the editorial angle, never speaker labels or factual approval.

    Normal title policy prefers a general central claim over an example. This
    experiment deliberately titles the actual example; merely appending style
    advice left the original central-point instruction in conflict.
    """
    fields=schema.get('properties',{})
    if not evidence or not any(k in fields for k in ('a_guest_answer','a_reading','c_candidates','reviews')):
        return prompt
    prompt=prompt.replace('不要把主持人的猜测或一处举例当成中心观点。',
        '不能把主持人的猜测当成嘉宾结论。本片选题就是下列具体事例，中心是嘉宾在这件事上的实际选择或判断及其原因。')
    prompt=prompt.replace('central_point抓住中心而不是举例或旁枝',
        'central_point抓住下列具体事例中嘉宾实际做了什么、为什么，不能改成别的话题或抽象自评')
    return prompt+('\n本片只为下面的具体公司事例或亲身经历拟标题，不为此前的抽象自评拟标题。'
        '通读全部上下文，所有说话人和否定条件仍须独立核对，不删掉其他嘉宾回答。'
        '阅读概括先说清这件具体事情；拟稿时三个候选都从该事情的实际对象和选择开头，'
        '再表达本人理由；理由优先保留细节原句里的关键名词，不换成别的对象的增长或爆发。'
        '拟稿像本人随口讲清一件事：具体选择加一条最直接理由，正文优先16至26字，'
        '不为了字数截断、不省略必要条件。若细节原句已明确命名理论或选择标准，'
        '直接沿用这个原词，不必把背景时点、程度描述和整条推理链都串进标题。'
        '三个候选换自然说法，不往同一句堆更多背景；避免连续“基于、前的、所导致”等书面连接。'
        '核对时事实支持、说话人归属和限定条件标准完全不变。'
        '下列线索是本段逐字原句，不是已批准的事实或预填标题；提问只提供上下文，不能当嘉宾断言。\n'
        +json.dumps(evidence,ensure_ascii=False))


def title_focus(selection):
    """Literal source-word coverage, not an entailment or speaker approval."""
    import jieba.posseg
    generic={'公司','企业','投资','理论','原因','时候','当时','地方','产品','东西','事例','故事','选择','判断','水平','前期'}
    categories={'ETF','指数','基金','指数基金','市场','股票','行业','板块','成份','成分','机制','规则','资金','差别','例子','成份股','成分股'}
    def terms(text,proper=False):
        words=list(jieba.posseg.cut(text))
        tokens=[w.word for w in words if 2<=len(w.word)<=10 and w.word not in (generic|categories if proper else generic)
                and (w.flag.startswith('n') or not proper and w.flag=='vn')]
        if proper:
            names=[w.word for w in words if w.flag in {'nt','nz','nr'} and w.word in tokens]
            return list(dict.fromkeys(names or tokens))[:6]
        # Prefer a named adjacent concept (e.g. two contiguous content words)
        # over a generic component. Never concatenate across source characters.
        compound=[a.word+b.word for a,b in zip(words,words[1:])
                  if a.word in tokens and b.word in tokens and len(a.word+b.word)<=10]
        return list(dict.fromkeys(compound or tokens))[:6]
    return dict(subjects=terms(selection.get('subject_quote',''),True)
                if selection.get('kind')=='company_case' else [],
                details=terms(selection.get('detail_quote','')),
                source=[selection.get(k,'') for k in ('subject_quote','event_quote','detail_quote')])


def title_focus_error(title,focus,speaker='林园',cover=None):
    if not isinstance(title,str):return '故事标题不是文本'
    body=title.removeprefix(speaker+'：')
    if len(body)>28:return '故事标题把过多背景压进一句；请用具体选择加一条直接理由，不能截断或删必要条件'
    if focus.get('subjects') and not any(t in body[:12] for t in focus['subjects']):
        return '故事标题前半句必须写明本次具体对象：'+','.join(focus['subjects'])
    if focus.get('details') and not any(t in body for t in focus['details']):
        return '故事标题须保留本次事例的实际细节或理由原词：'+','.join(focus['details'])
    # Real CPU drafts repeatedly collapsed an income-level background clause
    # into a new income-boom claim. Independent model reviews approved it.
    # Require an explicit original phrase for this stronger claim, not a word
    # from one clause concatenated with a growth metaphor from another.
    boom=r'收入(?:水平)?(?:将|会|要|已|即将|到了?|达(?:到)?|正处于)?爆发'
    if re.search(boom,body+'\n'+str(cover or '')) and not re.search(boom,''.join(focus.get('source') or [])):
        return '不能把收入达到某个水平的背景压成收入爆发；用本段明确说出的实际理由'
    return None


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
                or not all(isinstance(s.get(k),str) and minimum<=len(s[k])<=140 and _substantive_quote(s[k],minimum)
                           and (k=='subject_quote' or not re.search(r'[？?]',s[k]))
                           for k,minimum in (('subject_quote',2),('event_quote',4),('detail_quote',4)))
                or not re.fullmatch('[a-f0-9]{64}',str(s.get('transcript_sha256') or ''))
                or s['event_quote']==s['detail_quote']):
            return 'Missing long-mother or grounded story evidence'
    except (TypeError,ValueError,KeyError):return 'Invalid story proof fields'
    focus=title_focus(s)
    if s['kind']=='company_case' and not focus['subjects']:
        return 'A generic asset/rule is not a named company case'
    focus_issue=title_focus_error(meta.get('title'),focus,cover=meta.get('cover_title'))
    if focus_issue:return focus_issue
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


def family_metadata(source_sha256,seconds):
    if not re.fullmatch('[a-f0-9]{64}',str(source_sha256 or '')) or not MIN_MOTHER_SECONDS<=seconds<=5400:
        raise ValueError('Invalid long-mother family evidence')
    return dict(version=VERSION,mother_sha256=source_sha256,duration_sec=seconds)


def full_clip_pair(current,previous,preflight=False):
    """Only the explicit long version and a contained story excerpt may pair.

    Never exempt two clips, two fulls, equal files, unknown historical ranges,
    changed mothers or merely similar titles. All media gates still run.
    """
    a=current.get('long_story_family');b=previous.get('long_story_family')
    if not isinstance(a,dict) or a!=b or a.get('version')!=VERSION:return False
    source=a.get('mother_sha256')
    if (not re.fullmatch('[a-f0-9]{64}',str(source or ''))
            or current.get('source_sha256')!=source or previous.get('source_sha256')!=source):return False
    current_hash=(current.get('fingerprints') or {}).get('sha256')
    previous_hash=(previous.get('fingerprints') or {}).get('sha256')
    if not re.fullmatch('[a-f0-9]{64}',str(previous_hash or '')):return False
    if not (preflight and not current_hash) and not re.fullmatch('[a-f0-9]{64}',str(current_hash or '')):return False
    if current_hash==previous_hash:return False
    fulls=[p for p in (current,previous) if p.get('content_type')=='full_interview']
    if len(fulls)!=1:return False
    full=fulls[0];clip=previous if full is current else current
    if clip.get('editorial_profile')!=TREATMENT:return False
    def span(p):
        rows=p.get('segments') or p.get('source_segments') or []
        if len(rows)!=1:raise ValueError()
        lo,hi=float(rows[0]['start']),float(rows[0]['end'])
        if not math.isfinite(lo+hi) or not 0<=lo<hi:raise ValueError()
        return lo,hi
    try:
        fa,fb=span(full);ca,cb=span(clip);duration=float(a['duration_sec'])
        return (MIN_MOTHER_SECONDS<=duration<=5400 and fb<=duration+1
                and MIN_MOTHER_SECONDS<=fb-fa and 20<=cb-ca<=330
                and fa<=ca and cb<=fb and fb-fa>=3*(cb-ca))
    except (KeyError,TypeError,ValueError):return False


def comparison_state(meta,state,preflight=False):
    """Keep all history except the exact authorized cross-format partner."""
    published={};changed=False
    for slug,info in (state.get('published') or {}).items():
        rows=info.get('parts') or ([info] if info.get('bvid') else [])
        kept=[p for p in rows if not full_clip_pair(meta,p,preflight=preflight)]
        if len(kept)==len(rows):published[slug]=info
        else:
            changed=True
            if kept:published[slug]={**info,'parts':kept}
    return {**state,'published':published} if changed else state


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
    old=previous.get('publishing_experiment') or {}
    # Publication receipts are immutable observations. A temporary state-read
    # failure must not erase the month already collected by earlier runs.
    receipts={r['bvid']:dict(r) for r in old.get('receipts') or []
              if isinstance(r,dict) and r.get('bvid') and r.get('experiment_id')==EXPERIMENT_ID}
    for info in (state.get('published') or {}).values():
        for p in info.get('parts') or []:
            r=p.get('publishing_experiment')
            if p.get('bvid') and isinstance(r,dict) and r.get('experiment_id')==EXPERIMENT_ID:
                receipts[p['bvid']]={**r,'bvid':p['bvid'],'title':p.get('title'),
                    'published_at':p.get('ts'),'duration_sec':p.get('duration_sec'),
                    'source_url':info.get('source_url'),'source_sha256':p.get('source_sha256'),
                    'render_mode':p.get('render_mode'),'resolution':p.get('resolution')}
    samples={}
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
    by_slot=[]
    for slot in (1,2,3,4):
        for window in (24,72,168):
            plays=[r['play'] for r in samples.values() if r['slot_number']==slot and r['window']==window
                   and r['compliant'] and r.get('content_type')!='full_interview']
            by_slot.append(dict(slot_number=slot,window=window,count=len(plays),
                mean=round(statistics.mean(plays),2) if plays else None,
                median=statistics.median(plays) if plays else None))
    local=datetime.fromtimestamp(now,CST).date().isoformat()
    days=max(0,min(30,(date.fromisoformat(local)-date.fromisoformat(START)).days))
    receipts_by_slot={r['slot']:r for r in receipts.values()}
    daily=[]
    for day in range(30):
        value=date.fromisoformat(START)+timedelta(days=day)
        due=[f'{value.isoformat()} {h:02d}' for h in HOURS
             if datetime(value.year,value.month,value.day,h,tzinfo=CST).timestamp()<=now]
        if not due:continue
        present=[receipts_by_slot[s] for s in due if s in receipts_by_slot]
        daily.append(dict(date=value.isoformat(),slots_due=len(due),published=len(present),
                          missing_slots=[s for s in due if s not in receipts_by_slot],
                          compliant_treatment=sum(r['planned']==TREATMENT and r['compliant'] for r in present),
                          fallback=sum(not r['compliant'] for r in present)))
    return dict(experiment_id=EXPERIMENT_ID,version=VERSION,start=START,end_exclusive=END_EXCLUSIVE,
        status='scheduled' if local<START else 'collecting' if local<END_EXCLUSIVE else 'followup',
        completed_calendar_days=days,review_due=local>=END_EXCLUSIVE,
        daily=daily,
        pending_uploads=[dict(slug=e.get('slug'),upload_intent_ts=e.get('uploading_ts'),
                             assignment=e['upload_experiment'])
                         for e in state.get('dispatched') or []
                         if e.get('uploading') and e.get('upload_experiment')],
        receipts=sorted(receipts.values(),key=lambda r:r['slot']),
        fallback_count=sum(not r['compliant'] for r in receipts.values()),
        samples=sorted(samples.values(),key=lambda r:(r['bvid'],r['window'])),groups=groups,by_slot=by_slot,
        scope='Fixed Beijing slots 1/3 baseline, 2/4 treatment; not randomized. '
              'Search-index cumulative views at ages 24–36h / 72–84h / 168–180h; '
              'not CTR, impressions, retention or causal uplift. Missing is null. '
              'Fallbacks and full interviews excluded from primary clip groups; '
              'slot/time, layout, topic, source and duration remain confounders.')
