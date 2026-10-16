"""Independent observable segmentation checks, separate from planner costs."""
import re


def compact(text):
    return re.sub(r'[\s，。！？；：、]','',text)


def metrics(rows,entries,layout):
    """Independent user-facing counts; not the planner's optimization score."""
    import jieba
    full=''.join(row['zh'] for row in rows);source=compact(full)
    cuts=[];cursor=0
    for row in rows[:-1]:cursor+=len(compact(row['zh']));cuts.append(cursor)
    entities=('贵州茅台','五粮液','片仔癀','可转债市场','港股市场','美股市场',
              '大概率事件','大概率','金融危机','现金流','新能源')
    spans=[m.span() for term in entities for m in re.finditer(re.escape(term),source)]
    spans += [m.span() for m in re.finditer(r'\d+(?:\.\d+)?(?:亿元|万元|元|年|倍|%|％)',source)]
    raw=''.join(e['zh'] for e in entries);stops=set();clauses=set();cursor=0
    hint_offset=0
    for entry in entries:
        for end,mark in entry.get('caption_boundaries',[]):
            if mark in ('。','！','？'):stops.add(hint_offset+len(compact(entry['zh'][:end])))
            if mark in ('，','；','：'):clauses.add(hint_offset+len(compact(entry['zh'][:end])))
        hint_offset+=len(compact(entry['zh']))
    for char in raw:
        if char in '。！？':stops.add(cursor)
        elif char in '，；：':clauses.add(cursor)
        elif compact(char):cursor+=1
    crossed=0;clause_mixes=0;cursor=0
    for row in rows:
        end=cursor+len(compact(row['zh']))
        crossed+=sum(cursor<stop<end for stop in stops);cursor=end
    cursor=0
    for row in rows:
        end=cursor+len(compact(row['zh']))
        clause_mixes+=sum(stop-cursor>=4 and end-stop>=4 for stop in clauses if cursor<stop<end)
        cursor=end
    dangling={'因为','如果','但是','而且','或者','除了','一个','这个','一些','各个',
              '把','被','不会','不能','没有','是','要','就是说','更加'}
    tails=0
    for row in rows[:-1]:
        words=list(jieba.cut(compact(row['zh'])))
        tails+=bool(words and words[-1] in dangling and not compact(row['zh']).endswith('只有一个'))
    return dict(entity_splits=sum(any(a<cut<b for a,b in spans) for cut in cuts),
        mixed_sentences=crossed,clause_mixes=clause_mixes,dangling_tails=tails,
        dependent_starts=sum(bool(re.match(r'^(?:的|地|得|的话)',compact(r['zh']))) for r in rows[1:]),
        flashes=sum(r['end_sec']-r['start_sec']<.65 for r in rows),
        short_fragments=sum(len(compact(r['zh']))<4 and r['end_sec']-r['start_sec']<1 for r in rows),
        silence_crossings=sum(any(b[1]-a[2]>.8 for a,b in zip(r['caption_chars'],r['caption_chars'][1:])) for r in rows))
