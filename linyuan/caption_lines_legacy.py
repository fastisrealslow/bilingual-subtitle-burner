"""One-line screens retain source character anchors rather than stretching cues."""
import math
import re


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
    from presentation import word_spans
    capacity = int(layout['line_capacity'])
    result = []
    groups=[]; current=[]
    for entry in entries:
        anchored=character_anchors(entry)
        if not anchored:
            continue
        if (entry.get('semantic_group') and len(entry.get('zh',''))<=capacity
                and .25<=anchored[-1][2]-anchored[0][1]<=6):
            if current:groups.append(current);current=[]
            groups.append(anchored)
            continue
        if (current and current[-1][2]-current[0][1] >= .25
                and anchored[0][1]-current[-1][2] > .8):
            groups.append(current); current=[]
        current.extend(anchored)
    if current:
        groups.append(current)
    for atoms in groups:
        if not atoms:
            continue
        text = ''.join(c[0] for c in atoms)
        bounds = sorted({0, len(text)} | {b for _, b in word_spans(text)})
        # Punctuation records real phrase boundaries even when dictionary
        # tokenization joins the surrounding words.
        bounds = sorted(set(bounds) | {i+1 for i, c in enumerate(text) if c in '，。！？；：、'})
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
                gap = atoms[end][1]-atoms[end-1][2] if end < len(atoms) else 0
                boundary = 0 if part[-1] in '，。！？；：、' or gap >= .3 else 3
                if end < len(text) and unfinished_caption_tail(spoken):
                    boundary += 25
                if dependent_caption_start(spoken):
                    boundary += 25
                cost = best[start][0] + boundary + pauses*100 + abs(len(spoken)-min(12, capacity))*.35
                cost += abs(duration-2.5) + (12 if duration < .65 else 0)
                if end not in best or cost < best[end][0]:
                    best[end] = (cost, best[start][1]+[(start, end)])
        if len(text) not in best:
            frontier=max(best)
            raise ValueError('原文无法在保持词界和时间的前提下排成单行字幕：'+text[max(0,frontier-8):frontier+30])
        for start, end in best[len(text)][1]:
            part = text[start:end]
            result.append({'zh': part, 'en': '', 'lines': [part],
                           'start_sec': atoms[start][1], 'end_sec': atoms[end-1][2],
                           'caption_chars': atoms[start:end],
                           'font_px': layout['subtitle_font_px'],
                           'line_capacity': capacity, 'semantic_group': True})
    if ''.join(e['zh'] for e in result) != ''.join(e.get('zh') or '' for e in entries):
        raise ValueError('单行字幕分屏改变了原文')
    return result


def unfinished_caption_tail(text):
    from presentation import word_spans
    spans=word_spans(text)
    if not spans:return False
    if re.search(r'(?:(?:我|我们)(?:主要)?|主要)(?:是)?从(?:这个)?行业$',text):return True
    a,b=spans[-1]
    return bool(re.search(r'(?:还更|一个|这个|这种|一些|那些|这些|虽然|即使|尽管|无论|(?:我|你|他|她|们|人|企业|公司)会|(?:他|它|你|我)跟银行)$',text)) or text[a:b] in {
        '更加','因为','所以','如果','那么','但是','而且','以及','把','被',
        '来自','甚至','跟','向','主要','与','比','是','要','会','能','将','对','愿意','暂时','就是'}


def dependent_caption_start(text):
    from presentation import word_spans
    spans=word_spans(text)
    return bool(spans and text[spans[0][0]:spans[0][1]] in {'的','地','得'})
