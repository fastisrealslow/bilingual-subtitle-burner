"""Independent observable boundary events; never accept new bad positions."""
from functools import lru_cache
import re


def compact(text):
    return re.sub(r'[\s，。！？；：、]','',text)


@lru_cache(maxsize=256)
def lexical_context(source):
    import jieba
    import jieba.posseg as posseg
    spans=tuple((a,b) for _,a,b in jieba.tokenize(source))
    starts={};ends={};offset=0
    for word,tag in posseg.cut(source):
        starts[offset]=(word,tag);offset+=len(word);ends[offset]=(word,tag)
    return spans,starts,ends


def boundary_events(rows,entries):
    source=compact(''.join(row['zh'] for row in rows))
    entities=('贵州茅台','五粮液','片仔癀','可转债市场','港股市场','美股市场',
              '大概率事件','大概率','金融危机','现金流','新能源')
    spans=[m.span() for term in entities for m in re.finditer(re.escape(term),source)]
    from caption_quantities import quantity_spans
    spans += quantity_spans(source)
    spans += [m.span() for m in re.finditer(r'[A-Za-zＡ-Ｚａ-ｚ](?:企业|公司|股)',source)]
    words,starts,ends=lexical_context(source)
    stops=set();clauses=set();offset=0
    for entry in entries:
        for end,mark in entry.get('caption_boundaries',[]):
            at=offset+len(compact(entry['zh'][:end]))
            if mark in ('。','！','？'):stops.add(at)
            if mark in ('，','；','：'):clauses.add(at)
        offset+=len(compact(entry['zh']))
    offset=0
    for char in ''.join(e['zh'] for e in entries):
        if char in '。！？':stops.add(offset)
        elif char in '，；：':clauses.add(offset)
        elif compact(char):offset+=1
    clauses-=stops
    events={k:set() for k in ('entity_splits','lexical_splits','mixed_sentences','clause_mixes',
        'dangling_tails','dependent_starts','subject_predicate_splits',
        'flashes','short_fragments','silence_crossings')}
    dangling={'因为','如果','但是','而且','或者','除了','一个','这个','一些','各个',
              '把','被','不会','不能','没有','是','要','就是说','更加'}
    pronouns={'我','你','他','她','它','我们','你们','他们','大家'}
    offset=0
    for row in rows:
        text=compact(row['zh']);end=offset+len(text);duration=row['end_sec']-row['start_sec']
        events['mixed_sentences'].update(stop for stop in stops if offset<stop<end)
        events['clause_mixes'].update(stop for stop in clauses if stop-offset>=4 and end-stop>=4)
        if duration<.65:events['flashes'].add((offset,end))
        if len(text)<4 and duration<1:events['short_fragments'].add((offset,end))
        if re.match(r'^(?:的|地|得|的话)',text):events['dependent_starts'].add(offset)
        for a,b in zip(row['caption_chars'],row['caption_chars'][1:]):
            if b[1]-a[2]>.8:events['silence_crossings'].add((a[2],b[1]))
        if end<len(source):
            if any(a<end<b for a,b in spans):events['entity_splits'].add(end)
            if any(a<end<b for a,b in words) and end not in stops|clauses:events['lexical_splits'].add(end)
            tail,tag=ends.get(end,('',''));following,next_tag=starts.get(end,('',''))
            if tail in dangling and not text.endswith('只有一个'):events['dangling_tails'].add(end)
            if tail in pronouns and next_tag.startswith(('v','d')) and end not in stops|clauses:
                events['subject_predicate_splits'].add(end)
        offset=end
    return events


def metrics(rows,entries,layout=None):
    return {key:len(value) for key,value in boundary_events(rows,entries).items()}


def no_new_bad_boundaries(previous,proposed,entries):
    before=boundary_events(previous,entries);after=boundary_events(proposed,entries)
    return all(after[key]<=before[key] for key in before)
