"""Find changing text inside a source-only window, including central captions.

Our added titles/subtitles must be outside the inspected window. Persistent
scene signs and single-frame OCR guesses are not changing-caption evidence.
"""
import difflib
import math
import re

VERSION = 1


def changing_text_tracks(evidence):
    rows = []
    for row in evidence:
        text = ''.join(re.findall(r'[\u4e00-\u9fff]', str(row.get('text') or '')))
        box = row.get('rect') or []
        if (type(row.get('frame')) is not int or len(box) != 4
                or float(row.get('confidence') or 0) < .85 or len(text) < 3):
            continue
        x, y, right, bottom = map(float, box)
        if (not all(math.isfinite(v) and 0 <= v <= 1 for v in (x,y,right,bottom))
                or right-x < .20 or not .02 <= bottom-y <= .20):
            continue
        rows.append(dict(frame=row['frame'], text=text, rect=[x,y,right,bottom]))
    tracks = []
    seen = set()
    for anchor in rows:
        x,y,right,bottom = anchor['rect']
        nearby = []
        for row in rows:
            a,b,c,d = row['rect']
            aligned = abs(a-x) <= .045 or abs((a+c-x-right)/2) <= .045
            if aligned and abs((b+d-y-bottom)/2) <= .065:
                nearby.append(row)
        # Three different decoded frames and three materially different phrases
        # are required. OCR spelling noise on one static sign must not qualify.
        distinct = []
        for row in nearby:
            if (row['frame'] not in {r['frame'] for r in distinct}
                    and all(difflib.SequenceMatcher(None,row['text'],r['text']).ratio() < .6
                            and row['text'] not in r['text'] and r['text'] not in row['text']
                            for r in distinct)):
                distinct.append(row)
        if len(distinct) < 3:
            continue
        key = tuple(sorted((r['frame'],r['text']) for r in distinct))
        if key not in seen:
            tracks.append(dict(distinct_frames=len(distinct), evidence=distinct))
            seen.add(key)
    return tracks
