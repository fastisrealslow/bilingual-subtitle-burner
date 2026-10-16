"""One-line screens retain source character anchors rather than stretching cues."""
import math
import re


def caption_word_spans(text):
    """Caption-only compounds: do not change the title/selection dictionary."""
    from presentation import word_spans
    bounds={0,len(text)}|{b for _,b in word_spans(text)}
    protected=[m.span() for m in re.finditer('大概率事件|大概率|金融危机|可转债市场|港股市场|美股市场|[A-Za-zＡ-Ｚａ-ｚ](?:企业|公司|股)',text)]
    bounds=sorted(b for b in bounds if not any(a<b<z for a,z in protected))
    return list(zip(bounds,bounds[1:]))


def character_anchors(entry):
    text = entry.get('zh') or ''
    supplied = entry.get('caption_chars')
    if supplied is not None:
        if len(supplied) != len(text) or ''.join(c[0] for c in supplied) != text:
            raise ValueError('字幕逐字时间与原文不一致')
        atoms = [tuple(c) for c in supplied]
    else:
        start, end = float(entry['start_sec']), float(entry['end_sec'])
        atoms = [(c, start + (end-start)*i/len(text),
                  start + (end-start)*(i+1)/len(text)) for i, c in enumerate(text)]
    if any(not math.isfinite(a+b) or a > b for _, a, b in atoms):
        raise ValueError('字幕逐字时间无效')
    if any(a[1] > b[1] or a[2] > b[1]+.001 for a, b in zip(atoms, atoms[1:])):
        raise ValueError('字幕逐字时间倒退或重叠')
    return atoms


def one_line_screens(entries, layout):
    """Adopt a new segmentation only when observable quality does not regress."""
    from caption_lines_legacy import one_line_screens as legacy
    from caption_boundary_quality import metrics, no_new_bad_boundaries
    for entry in entries:
        for end,mark in entry.get('caption_boundaries',[]):
            if type(end) is not int or not 0<end<=len(entry.get('zh') or '') or mark not in ('，','。','！','？','；','：','、'):
                raise ValueError('字幕原文断句边界无效')
    try:previous=legacy(entries,layout)
    except ValueError:previous=None
    try:proposed=_natural_screens(entries,layout)
    except ValueError:
        if previous is None:raise
        return previous
    if previous is None:return proposed
    before=metrics(previous,entries,layout);after=metrics(proposed,entries,layout)
    if no_new_bad_boundaries(previous,proposed,entries) and any(after[k]<before[k] for k in before):
        return proposed
    # Shared cuts make independent replacement windows. Keep an improved
    # sentence even if a different sentence in the same clip needs fallback.
    def indexed(rows):
        result={};cursor=0
        for row in rows:result[cursor]=row;cursor+=len(row['zh'])
        return result,cursor
    old,total=indexed(previous);new,_=indexed(proposed)
    shared=sorted((set(old)&set(new))|{total})
    atoms=[atom for entry in entries for atom in character_anchors(entry)]
    hints={};cursor=0
    for entry in entries:
        hints.update({cursor+p:m for p,m in entry.get('caption_boundaries',[])})
        cursor+=len(entry.get('zh') or '')
    result=[]
    for lo,hi in zip(shared,shared[1:]):
        left=[old[p] for p in old if lo<=p<hi];right=[new[p] for p in new if lo<=p<hi]
        context=[caption_screen(atoms[lo:hi],{p-lo:m for p,m in hints.items() if lo<p<=hi},layout)]
        a=metrics(left,context,layout);b=metrics(right,context,layout)
        result.extend(right if no_new_bad_boundaries(left,right,context) and any(b[k]<a[k] for k in a) else left)
    final=metrics(result,entries,layout)
    return result if no_new_bad_boundaries(previous,result,entries) else previous


def _natural_screens(entries, layout):
    word_spans=caption_word_spans
    capacity = int(layout['line_capacity'])
    result = []
    groups=[]; current=[]; hints={}
    full_text=''.join(entry.get('zh') or '' for entry in entries)
    full_bounds={0,len(full_text)}|{b for _,b in word_spans(full_text)}
    cursor=0
    for entry in entries:
        anchored=character_anchors(entry)
        entry_start=cursor;cursor+=len(anchored)
        if not anchored:
            continue
        source_hints={}
        for end,mark in entry.get('caption_boundaries',[]):
            if type(end) is not int or not 0<end<=len(anchored) or mark not in ('，','。','！','？','；','：','、'):
                raise ValueError('字幕原文断句边界无效')
            source_hints[end]=mark
        spoken=re.sub(r'[\s，。！？；：、]','',entry.get('zh',''))
        complete=(entry.get('semantic_group') and len(anchored)<=capacity
                  and entry_start in full_bounds and cursor in full_bounds
                  and .65<=anchored[-1][2]-anchored[0][1]<=6
                  and (len(anchored) in source_hints or entry.get('zh','').endswith(('。','！','？','；')))
                  and not any(mark in '。！？' and end<len(anchored) for end,mark in source_hints.items())
                  and not any(b[1]-a[2]>.8 for a,b in zip(anchored,anchored[1:]))
                  and not unfinished_caption_tail(spoken)
                  and not dependent_caption_start(spoken))
        if complete:
            if current:groups.append((current,hints,False));current=[];hints={}
            groups.append((anchored,source_hints,True))
            continue
        if (current and current[-1][2]-current[0][1] >= .25
                and anchored[0][1]-current[-1][2] > .8):
            groups.append((current,hints,False)); current=[]; hints={}
        offset=len(current)
        for end,mark in source_hints.items():
            hints[offset+end]=mark
        current.extend(anchored)
    if current:
        groups.append((current,hints,False))
    for atoms,hints,preserve in groups:
        if not atoms:
            continue
        if preserve:
            result.append(caption_screen(atoms,hints,layout))
            continue
        text = ''.join(c[0] for c in atoms)
        bounds = sorted({0, len(text)} | {b for _, b in word_spans(text)})
        # Punctuation records real phrase boundaries even when dictionary
        # tokenization joins the surrounding words.
        bounds = sorted(set(bounds) | set(hints) |
                        {i+1 for i,c in enumerate(text) if c in '，。！？；：、'
                         and (i+1==len(text) or text[i+1] not in '”’」』）】')})
        # Re-tokenize the whole spoken context, not each previous display row:
        # a historical cut inside 大/概率 or a company name is not a word edge.
        penalties, sentence_ends = natural_boundaries(text,atoms,bounds,hints)
        clause_ends={stop for stop,mark in hints.items()
                     if mark in ('，','；','：') and penalties.get(stop,0)<20}
        best = {0: (0, [])}
        for i, start in enumerate(bounds):
            if start not in best:
                continue
            for end in bounds[i+1:]:
                if end-start > capacity:
                    break
                part = text[start:end]
                spoken = re.sub(r'[\s，。！？；：、]', '', part)
                if not spoken:
                    continue
                duration = atoms[end-1][2]-atoms[start][1]
                if not .25 <= duration <= 6:
                    continue
                # A new screen starts with the next spoken words; do not carry
                # the preceding caption across a long silent interval.
                pauses=sum(atoms[k][2]>atoms[k][1] and atoms[k+1][1]-atoms[k][2] > .8
                           for k in range(start,end-1))
                boundary = penalties.get(end,0) if end<len(text) else 0
                if dependent_caption_start(spoken):
                    boundary += 25
                # Fewer complete screens win over filling every row to 12
                # characters. A sentence that fits stays whole at the same font.
                crossed=sum(start<stop<end for stop in sentence_ends)
                clauses=sum(start<stop<end
                            and atoms[stop-1][2]-atoms[start][1]>=.8
                            and atoms[end-1][2]-atoms[stop][1]>=.8
                            for stop in clause_ends)
                cost = best[start][0] + 4 + boundary + pauses*100 + crossed*40
                cost += clauses*35
                cost += abs(len(spoken)-min(12,capacity))*.06
                cost += abs(duration-2.5)*.2 + (12 if duration < .65 else 0)
                if end not in best or cost < best[end][0]:
                    best[end] = (cost, best[start][1]+[(start, end)])
        if len(text) not in best:
            frontier=max(best)
            raise ValueError('原文无法在保持词界和时间的前提下排成单行字幕：'+text[max(0,frontier-8):frontier+30])
        for start, end in best[len(text)][1]:
            result.append(caption_screen(atoms[start:end],
                {p-start:m for p,m in hints.items() if start<p<=end},layout))
    if ''.join(e['zh'] for e in result) != ''.join(e.get('zh') or '' for e in entries):
        raise ValueError('单行字幕分屏改变了原文')
    return result


def caption_screen(atoms,hints,layout):
    text=''.join(c[0] for c in atoms)
    return dict(zh=text,en='',lines=[text],start_sec=atoms[0][1],end_sec=atoms[-1][2],
                caption_chars=atoms,caption_boundaries=sorted(hints.items()),
                font_px=layout['subtitle_font_px'],line_capacity=int(layout['line_capacity']),
                semantic_group=True)


def natural_boundaries(text, atoms, bounds, hints):
    """Source punctuation, pauses and syntax guide cuts; never edit speech."""
    import jieba.posseg as posseg
    ends={}; starts={}; offset=0
    for word,tag in posseg.cut(text):
        starts[offset]=(word,tag);offset+=len(word);ends[offset]=(word,tag)
    marks=dict(hints)
    for i,c in enumerate(text):
        if c not in '，。！？；：、':continue
        end=i+1
        while end<len(text) and text[end] in '”’」』）】':end+=1
        marks[end]=c
    penalties={}; sentence_ends=set()
    incomplete={'不','没','未','别','更','很','也','就','都','在','和','或','为',
                '有','没有','不能','不会','不是','还是','或者','除了','各个',
                '认为','觉得','感觉','应该','必须','需要','比如说','就像'}
    for end in bounds:
        if end in (0,len(text)):continue
        tail,tag=ends.get(end,('',''))
        following,next_tag=starts.get(end,('',''))
        mark=marks.get(end)
        gap=atoms[end][1]-atoms[end-1][2]
        cost=0 if mark or gap>=.3 else 6
        prefix=re.sub(r'[\s，。！？；：、]','',text[:end])
        unfinished=unfinished_caption_tail(prefix) or tail in incomplete
        if mark and prefix.endswith('只有一个'):
            unfinished=False  # “顶只有一个” is a complete quantified predicate.
        if unfinished:cost+=30
        if tail in {'认为','觉得','感觉','判断'} and following in {'现在','当时','未来','今天','今年'}:
            cost=0
        if following in {'的','地','得'} or text[end:].startswith('的话'):cost+=30
        if not mark and gap<.3:
            if (tag in {'p','c','q','u','uj','ul','d'}
                    or tag.startswith('v') and next_tag.startswith(('n','r'))
                    or tag.startswith(('a','b','n')) and next_tag.startswith('n')
                    or tag in {'t','r'} and next_tag.startswith(('v','d'))):
                cost+=12
            if re.match(r'(?:但是|所以|那么|因为|如果|而且|不过|同时|包括|我们认为|我们感觉|您觉得)',text[end:]):
                cost=max(0,cost-5)
        if text[:end].endswith(('以后','之后','之前','的时候','的话')):
            cost=0
        if mark and mark in '。！？':
            if not unfinished:sentence_ends.add(end)
        penalties[end]=cost
    return penalties,sentence_ends


def unfinished_caption_tail(text):
    from presentation import word_spans
    spans=word_spans(text)
    if not spans:return False
    if re.search(r'(?:(?:我|我们)(?:主要)?|主要)(?:是)?从(?:这个)?行业$',text):return True
    a,b=spans[-1]
    return bool(re.search(r'(?:还更|一个|这个|这种|一些|那些|这些|虽然|即使|尽管|无论|(?:我|你|他|她|们|人|企业|公司)会|(?:他|它|你|我)跟银行)$',text)) or text[a:b] in {
        '更加','因为','所以','如果','那么','但是','而且','以及','把','被',
        '来自','甚至','跟','向','主要','与','比','是','要','会','能','将','对','愿意','暂时','就是',
        '就是说','比如说','或者','还是','除了','各个','感觉','认为','觉得'}


def dependent_caption_start(text):
    from presentation import word_spans
    spans=word_spans(text)
    return bool(spans and text[spans[0][0]:spans[0][1]] in {'的','地','得'})
