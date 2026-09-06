"""Validate actual offline hypotheses and aligned token ownership before use."""
import json
import math
from pathlib import Path
import re


def content(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def validated_words(reports, pcm_sha, video_sha, duration):
    chunks=[]
    for report in reports:
        align=report.get('alignment') or {}
        if (report.get('source_pcm_sha256')!=pcm_sha
                or report.get('source_video_sha256')!=video_sha
                or report.get('device')!='cpu' or report.get('networking_during_inference') is not False
                or align.get('device')!='cpu' or align.get('networking_during_inference') is not False
                or report.get('model_id')!='Qwen/Qwen3-ASR-0.6B'
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
            if chunk['core_start']<=a<chunk['core_end']:
                if owned and a<owned[-1]['start']:
                    raise ValueError('Offline word timestamps are not monotonic')
                owned.append(word)
    if abs(cursor-duration)>.05:
        raise ValueError('Offline transcript does not cover the complete source audio')
    return owned


def load_reports(root):
    return [json.loads(p.read_text()) for p in sorted(Path(root).rglob('aligned.json'))]


def punctuated_words(reports, owned):
    """Restore punctuation from decoded text; the aligner emits lexical words only.

    No model is asked to rewrite a sentence. Every mark comes from the same
    chunk that owns its preceding word, and all lexical characters are retained.
    """
    suffixes={}
    for report in reports:
        for chunk in report['chunks']:
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
                if chunk['core_start']<=word['start']<chunk['core_end']:
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
