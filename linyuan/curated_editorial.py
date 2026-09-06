"""Reviewed source ranges are editing inputs, never publication approvals."""
import json
from pathlib import Path
import re


def source_ranges(cues,source_sha,profile_path=None):
    path=Path(profile_path or Path(__file__).with_name('editorial_source_profiles.json'))
    profiles=json.loads(path.read_text()).get('sources',{})
    if source_sha not in profiles:
        return None
    normalize=lambda s: re.sub(r'[\s，。！？；：、,.!?;:]','',s)
    result=[]
    for row in profiles[source_sha]:
        selected=[i for i,c in enumerate(cues)
                  if c['start']>=row['start']-.05 and c['end']<=row['end']+.05]
        if not selected:
            raise ValueError('Reviewed source range is absent from current ASR')
        a,b=selected[0],selected[-1]
        if (abs(cues[a]['start']-row['start'])>.5 or abs(cues[b]['end']-row['end'])>.5
                or not 120<=cues[b]['end']-cues[a]['start']<=330):
            raise ValueError('Reviewed source range changed duration or sentence boundary')
        beginning=normalize(''.join(c['text'] for c in cues[a:min(b+1,a+5)]))
        ending=normalize(''.join(c['text'] for c in cues[max(a,b-3):b+1]))
        if row['opening'] not in beginning or row['ending'] not in ending:
            raise ValueError('Reviewed source range no longer matches its opening or ending')
        if result and a<=result[-1][1]:
            raise ValueError('Reviewed continuous arguments overlap')
        result.append((a,b,[dict(start=0,end=b-a,score=8,reason=row['topic'])]))
    return result
