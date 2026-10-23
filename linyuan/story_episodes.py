"""Literal past-event tails inside complete Q&A; never a media/story approval."""
import hashlib,json,math,re

VERSION=1
START=re.compile(r'^(?:[啊嗯呃][，,\s]*)?(?:有一次|有一回|当年有一次|我记得有一次)')
RISK=re.compile(r'假设|假如|如果|只要|比方|设想|假定|假想|虚构|寓言|角色扮演|想象|不是真实|并非实际|不是真事|'
    r'(?:他|她|别人)(?:说|讲|的经历)|'
    r'(?:我|我们)[^。！？?]{0,12}(?:并没有|从没|从未|没有真的)(?:买|持|投)')
PAST=r'当时|当年|曾经|那时候|那个时候|以前|之前'
ACTION=r'买入|买了|买过|持有|卖出|卖了|退出|投资'
ACTUAL=re.compile(rf'(?:(?:我|我们)[^。！？?]{{0,18}}(?:{PAST})|(?:{PAST})[^。！？?]{{0,18}}(?:我|我们))[^。！？?]{{0,18}}(?:{ACTION})')
FIELDS={'version','parent_text','parent_sha256','child_offset','parent_start_cue','parent_end_cue',
        'parent_start_sec','child_start_sec','parent_end_sec'}


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def actual_event(text):
    return bool(isinstance(text,str) and ACTUAL.search(text) and not re.search(
        r'[？?]|假设|假如|如果|只要|没有|从未|从没|未曾|不曾|未来|以后|将来|可能|也许|或许|打算|计划|准备',text))


def context_error(context,child=None):
    try:
        if not isinstance(context,dict) or set(context)!=FIELDS or type(context['version']) is not int or context['version']!=VERSION:
            return 'Invalid past-episode context fields'
        parent=context['parent_text'];offset=context['child_offset']
        if (not isinstance(parent,str) or not 1<=len(parent)<=6000 or type(offset) is not int
                or not 0<offset<len(parent) or digest(parent)!=context['parent_sha256']
                or RISK.search(parent)):
            return 'Past episode lost its real nonhypothetical parent context'
        tail=parent[offset:]
        if (not START.match(tail) or not re.search(r'[。！!][”’」』\"]?\s*$',tail)
                or not ACTUAL.search(tail) or (child is not None and tail!=child)):
            return 'Past episode is not the intact source narrative through its original ending'
        if any(type(context[k]) not in (int,float) for k in ('parent_start_sec','child_start_sec','parent_end_sec')):
            return 'Invalid past-episode numeric source clock'
        a,c,b=(float(context[k]) for k in ('parent_start_sec','child_start_sec','parent_end_sec'))
        if not all(math.isfinite(v) for v in (a,c,b)) or not 0<=a<c<b or not 20<=b-c<=180:
            return 'Invalid continuous past-episode source clock'
        if (type(context['parent_start_cue']) is not int or type(context['parent_end_cue']) is not int
                or not 0<=context['parent_start_cue']<context['parent_end_cue']):
            return 'Invalid complete parent source range'
    except (ValueError,TypeError,KeyError):return 'Malformed past-episode context'
    return None


def propose(cues,parents,units):
    """Use literal sentence/parent endings before any ASR quality verdict.

    No typo, title, company name or manually reviewed range is an input.
    Keep the original parent candidates; nested hypotheses/denials stay blocked.
    """
    found=[];seen=set()
    for parent in parents:
        if parent.get('selection_method')!='source_question_answer_v2':continue
        lo,hi=parent['start'],parent['end']
        text=''.join(c['text'] for c in cues[lo:hi+1])
        if RISK.search(text):continue
        for unit in units:
            a=unit['start']
            if not lo<a<=hi or not START.match(unit['text']) or (a,hi) in seen:continue
            tail=''.join(c['text'] for c in cues[a:hi+1])
            context=dict(version=VERSION,parent_text=text,parent_sha256=digest(text),
                child_offset=len(''.join(c['text'] for c in cues[lo:a])),
                parent_start_cue=lo,parent_end_cue=hi,parent_start_sec=cues[lo]['start'],
                child_start_sec=cues[a]['start'],parent_end_sec=cues[hi]['end'])
            if context_error(context,tail):continue
            seen.add((a,hi))
            found.append(dict(start=a,end=hi,score=7,selection_method='source_past_episode_v1',
                reason='原问答内自然引出的过去事件，连续保留至原回答结束；仍须独立观点、角色、字幕及媒体验收',
                past_episode_context=context))
    return found


def overlaps_episode(segments,selection,accepted):
    episode=bool(selection.get('past_episode_context'))
    for part in accepted:
        previous=((part.get('experiment_proof') or {}).get('selection') or {})
        if not episode and not previous.get('past_episode_context'):continue
        if any(min(a['end'],b['end'])-max(a['start'],b['start'])>.05
               for a in segments for b in part.get('segments',[])):
            return True
    return False


def binding_error(context,meta,transcript_sha):
    problem=context_error(context)
    if problem:return problem
    tail=context['parent_text'][context['child_offset']:]
    spans=meta.get('segments')
    if digest(tail)!=transcript_sha or not isinstance(spans,list) or len(spans)!=1:
        return 'Past episode lost its continuous source transcript binding'
    try:
        a,b=(spans[0][k] for k in ('start','end'))
        if (type(a) not in (int,float) or type(b) not in (int,float)
                or not math.isfinite(a+b) or abs(a-context['child_start_sec'])>.05
                or abs(b-context['parent_end_sec'])>.05):
            return 'Past episode lost its source media clock'
    except (TypeError,KeyError):return 'Invalid past-episode media range'
    return None
