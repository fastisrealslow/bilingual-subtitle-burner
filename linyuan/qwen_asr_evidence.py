"""Validate actual offline hypotheses and aligned token ownership before use."""
import json
import math
from pathlib import Path
import re

WORD_OWNERSHIP_VERSION = 2026103001


def ownership_ranges(chunks):
    """Move a conflicting core seam only to a shared, aligned spoken pause.

    Every selected word and timestamp still comes verbatim from one original
    hypothesis. Neither interpolation nor a text-model correction is involved.
    An unresolved seam retains its old ownership and fails the caption gate.
    """
    boundaries=[c['core_start'] for c in chunks]+[chunks[-1]['core_end']] if chunks else []
    changes=[]
    for number,(left,right) in enumerate(zip(chunks,chunks[1:]),1):
        seam=left['core_end']
        if seam!=right['core_start']:continue
        before=[w for w in left['words'] if left['core_start']<=w['start']<seam]
        after=[w for w in right['words'] if seam<=w['start']<right['core_end']]
        if not before or not after or before[-1]['end']<=after[0]['start']+.001:continue
        candidates=[]
        rw=right['words'];lw=left['words']
        for i in range(2,len(lw)-1):
            cut=lw[i]['start']
            if not seam-2.5<=cut<=seam+2.5:continue
            for j in range(2,len(rw)-1):
                if cut!=rw[j]['start']:continue
                # Agree on two words either side, including the last observed
                # ends before a >=400ms pause. Both chunks must contain this
                # full context within their actual overlapping audio windows.
                a=lw[i-2:i+2];b=rw[j-2:j+2]
                if [w['text'] for w in a]!=[w['text'] for w in b]:continue
                if not all(content(w['text']) for w in a):continue
                if any(abs(x['start']-y['start'])>.100000001
                       or abs(x['end']-y['end'])>.100000001 for x,y in zip(a,b)):continue
                if cut-max(lw[i-1]['end'],rw[j-1]['end'])<.4:continue
                overlap_start=max(left.get('offset',seam),right.get('offset',seam))
                overlap_end=min(left.get('offset',0)+left.get('duration',0),
                                right.get('offset',0)+right.get('duration',0))
                if any(w['start']<overlap_start or w['end']>overlap_end for w in a+b):continue
                candidates.append((abs(cut-seam),cut,[w['text'] for w in a]))
        if candidates:
            _,cut,context=min(candidates)
            boundaries[number]=cut
            changes.append(dict(core_boundary=seam,selected_boundary=cut,shared_context=context))
    return list(zip(boundaries,boundaries[1:])),changes


def content(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def validated_words(reports, pcm_sha, video_sha, duration, *, model_id='Qwen/Qwen3-ASR-0.6B'):
    if model_id not in ('Qwen/Qwen3-ASR-0.6B','Qwen/Qwen3-ASR-1.7B'):
        raise ValueError('Unsupported explicit offline recognition model')
    from asr_context import matches
    if not matches(reports,source_sha=video_sha):
        raise ValueError('Offline transcript evidence has different recognition context')
    chunks=[]
    for report in reports:
        align=report.get('alignment') or {}
        if (report.get('source_pcm_sha256')!=pcm_sha
                or report.get('source_video_sha256')!=video_sha
                or report.get('device')!='cpu' or report.get('networking_during_inference') is not False
                or align.get('device')!='cpu' or align.get('networking_during_inference') is not False
                or report.get('model_id')!=model_id
                or align.get('model_id')!='Qwen/Qwen3-ForcedAligner-0.6B'
                or not report.get('model_revision') or not align.get('model_revision')):
            raise ValueError('Offline transcript evidence has different source, model or inference provenance')
        chunks.extend(report['chunks'])
    chunks.sort(key=lambda c:c['core_start'])
    cursor=0.0;owned=[]
    for chunk in chunks:
        if abs(chunk['core_start']-cursor)>.01:
            raise ValueError('Offline transcript evidence has missing or duplicated audio cores')
        cursor=chunk['core_end']
        words=chunk.get('words') or []
        if content(''.join(w['text'] for w in words))!=content(chunk['text']):
            raise ValueError('Word alignment changed or lost decoded transcript characters')
        for word in words:
            a,b=float(word['start']),float(word['end'])
            if not math.isfinite(a+b) or not 0<=a<=b<=duration+.1:
                raise ValueError('Invalid offline word alignment timestamp')
    if abs(cursor-duration)>.05:
        raise ValueError('Offline transcript does not cover the complete source audio')
    ranges,_=ownership_ranges(chunks)
    for chunk,(start,end) in zip(chunks,ranges):
        for word in chunk['words']:
            if start<=word['start']<end:
                if owned and word['start']<owned[-1]['start']:
                    raise ValueError('Offline word timestamps are not monotonic')
                owned.append(word)
    return owned


def load_reports(root):
    return [json.loads(p.read_text()) for p in sorted(Path(root).rglob('aligned.json'))]


def punctuated_words(reports, owned):
    """Restore punctuation from decoded text; the aligner emits lexical words only.

    No model is asked to rewrite a sentence. Every mark comes from the same
    chunk that owns its preceding word, and all lexical characters are retained.
    """
    suffixes={}
    chunks=sorted([c for r in reports for c in r['chunks']],key=lambda c:c['core_start'])
    ranges,_=ownership_ranges(chunks)
    for chunk,(start,end) in zip(chunks,ranges):
        raw=chunk['text']
        positions=[i for i,c in enumerate(raw) if c.isalnum()]
        cursor=0
        for word in chunk['words']:
            size=len(content(word['text']))
            if not size:
                continue
            cursor+=size
            if cursor>len(positions):
                raise ValueError('Punctuation mapping exceeds decoded characters')
            left=positions[cursor-1]+1
            right=positions[cursor] if cursor<len(positions) else len(raw)
            marks=''.join(c for c in raw[left:right] if c in '，。！？；：、,!?;:')
            if start<=word['start']<end:
                suffixes[(word['start'],word['text'])]=marks
    result=[]
    for i,word in enumerate(owned):
        result.append(dict(word))
        at=min(word['end'],owned[i+1]['start']) if i+1<len(owned) else word['end']
        for mark in suffixes.get((word['start'],word['text']),''):
            result.append(dict(text=mark,start=at,end=at))
    if content(''.join(w['text'] for w in result))!=content(''.join(w['text'] for w in owned)):
        raise ValueError('Punctuation restoration changed spoken characters')
    return result
