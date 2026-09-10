"""Display-only editing approved with the 2026-09-10 six-minute preview.

Never correct an ASR hypothesis by guessing. Preserve raw evidence, character
times, negation, quantities and names; log every conservative disfluency edit.
"""
import hashlib
import json
import math
import re

VERSION = 2026091002
MAX_SECONDS = 6.0
TARGET_SECONDS = 3.5

# An explicit vocabulary, not a generic repeated-character deletion regex:
# 看看、人人、越来越、不是不是 and repeated amounts must survive.
STUTTERS = ('我们', '你们', '他们', '这个', '那个', '那么', '就是',
            '我', '你', '他', '它', '去', '就', '都')
PUNCTUATION = '，。！？；：、,.!?;:'


def clean_entries(entries):
    atoms = []
    point_anchors = []
    for i, entry in enumerate(entries):
        text = entry.get('zh', '')
        a, b = float(entry['start_sec']), float(entry['end_sec'])
        original_end = b
        if not math.isfinite(a + b) or b < a:
            raise ValueError('字幕原始时间无效')
        if i + 1 < len(entries):
            following = float(entries[i + 1]['start_sec'])
            if not math.isfinite(following):
                raise ValueError('字幕原始时间无效')
            b = min(b, following)
        if text and (b < a or b == a and original_end > a):
            raise ValueError('字幕时间重叠到零长度')
        if text and b == a:
            # Qwen's validated forced alignment permits point timestamps. Keep
            # them verbatim; the existing >=.8s screen planner must join them
            # to surrounding speech. Never invent a duration or drop a word.
            point_anchors.append(dict(entry=i, time=a, text=text))
        for j, char in enumerate(text):
            atoms.append((char, a + (b-a)*j/len(text), a + (b-a)*(j+1)/len(text), i))
    original = ''.join(x[0] for x in atoms)
    if atoms and atoms[-1][2] <= atoms[0][1]:
        raise ValueError('字幕时间轴没有可显示的正时长')
    keep = [True] * len(atoms)
    edits = []

    def remove(a, b, reason):
        # Never collapse repetitions across real pauses or speaker turns.
        if any(atoms[k+1][1]-atoms[k][2] > .6 for k in range(a, b-1)):
            return
        if not all(keep[a:b]):
            return
        keep[a:b] = [False] * (b-a)
        edits.append(dict(start=atoms[a][1], end=atoms[b-1][2],
                          removed=original[a:b], reason=reason))

    # Fillers only at a clause opening before a clear continuation. An isolated
    # 嗯 (an affirmative answer), monetary 额 and lexical 那么 are not removed.
    for m in re.finditer(r'(?:^|[，。！？；：、])([啊呃嗯]+[，、]?)(?=那么|这个|那个|我|你|他|它)', original):
        remove(*m.span(1), 'opening_filler')
    for word in STUTTERS:
        for m in re.finditer(r'(?<![A-Za-z0-9])(?:'+re.escape(word)+r'){2,}', original):
            # Reject a time gap between the deleted words and the kept copy too.
            if any(atoms[k+1][1]-atoms[k][2] > 3 for k in range(m.start(),m.end()-1)):
                continue
            remove(m.start(), m.end()-len(word), 'stutter')
    # Bounded verbal padding around a preserved, explicit predicate. Do not
    # delete demonstratives or conjunctions globally (e.g. 如果...那么...).
    for pattern in (
        r'(那么)(?=我(?:我)?主要|我(?:我)?对|具体到|好的企业|我们总结|我们看到)',
        r'我主要(是)(?=还是|从)',
        r'我对(这个)(?=价值投资)',
        r'从(这个)(?=行业)',
        r'(是吧|啊|呃)(?=[，。！？；])',
    ):
        for m in re.finditer(pattern, original):
            # A preceding condition makes 那么 semantically significant.
            if original[m.start(1):m.end(1)] == '那么' and re.search(
                    r'如果|假如|只要|只有|既然|除非', original[max(0,m.start()-60):m.start()]):
                continue
            remove(*m.span(1), 'verbal_padding')

    # This exact restart occurs in the accepted source. Do not generalize to
    # X的X: 朋友的朋友 and similar possessive phrases carry distinct meaning.
    for m in re.finditer('行业的行业', original):
        remove(m.start(), m.start()+3, 'restarted_industry_phrase')

    by_entry=[[] for _ in entries]
    for n,atom in enumerate(atoms):
        if keep[n]:by_entry[atom[3]].append(atom[:3])
    result=[]
    for i, entry in enumerate(entries):
        selected=by_entry[i]
        if not selected:
            continue
        item=dict(entry, zh=''.join(x[0] for x in selected),
                  caption_chars=selected)
        result.append(item)
    cleaned=''.join(e['zh'] for e in result)
    proof=dict(version=VERSION, raw_text=original, display_text=cleaned, edits=edits,
               point_anchors=point_anchors,
               raw_sha256=hashlib.sha256(original.encode()).hexdigest(),
               display_sha256=hashlib.sha256(cleaned.encode()).hexdigest(),
               policy='conservative_deletions_only', asr_corrections='source_verified_only')
    return result, proof


def write_edit_proof(path, proof):
    path.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')


def ass_font_size(glyph_px, font_name):
    # libass uses the hhea ascent/descent, not the em-square. Noto CJK's 1448
    # units per 1000 em made the old "48px" captions only ~33 visible pixels.
    factor = 1.448 if 'Noto Sans CJK' in font_name else 1.0
    return round(glyph_px * factor)
