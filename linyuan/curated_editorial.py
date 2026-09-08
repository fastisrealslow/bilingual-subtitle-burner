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
        if any(a<=old_b and b>=old_a for old_a,old_b,_ in result):
            raise ValueError('Reviewed continuous arguments overlap')
        picks=[dict(start=0,end=b-a,score=8,reason=row['topic'],
                    editorial_source_sha256=source_sha,
                    editorial_title=row.get('title'),
                    editorial_subtitles=row.get('subtitle_groups'),
                    editorial_review=row.get('editorial_review'))]
        if row.get('omit'):
            from editorial_policy import reviewed_omission_matches
            cut=row['omit']
            left=[i for i in selected if cues[i]['end']<=cut['start']+.05]
            right=[i for i in selected if cues[i]['start']>=cut['end']-.05]
            spans=[dict(start=cues[left[0]]['start'],end=cues[left[-1]]['end']),
                   dict(start=cues[right[0]]['start'],end=cues[right[-1]]['end'])] if left and right else []
            if not reviewed_omission_matches(source_sha,spans):
                raise ValueError('Reviewed omission does not match actual source sentence boundaries')
            picks=[{**picks[0],'start':group[0]-a,'end':group[-1]-a}
                   for group in (left,right)]
        result.append((a,b,picks))
    return result
