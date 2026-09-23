"""Measured publisher lettering within a shot, not a global source crop.

Source34's central WEEKLY ON STOCKS backdrop was clipped into red fragments.
The known publisher name anchors adjacent OCR boxes; arbitrary scene text and
unrecognized red shapes are not treated as publisher marks.
"""
import re


def central_publisher_rects(evidence):
    rows=[r for r in evidence if float(r.get('confidence') or 0)>=.75
          and 0<=r['rect'][1]<r['rect'][3]<=.28]
    result=[]
    for anchor in rows:
        name=re.sub(r'\W+','',str(anchor.get('text') or '')).casefold()
        if (name!='weeklyonstocks' or float(anchor['confidence'])<.9
                or not .25<anchor['rect'][0]<anchor['rect'][2]<.75):
            continue
        group=[anchor]
        for _ in range(4):
            extra=[]
            for row in rows:
                if row in group or row.get('frame')!=anchor.get('frame'):continue
                x,y,r,b=row['rect']
                # Keep the logo local, rather than growing into a separate
                # corner mark, caption or face-adjacent text elsewhere.
                if x<anchor['rect'][0]-.23 or r>anchor['rect'][2]+.06:continue
                for other in group:
                    u,v,s,t=other['rect']
                    if max(x,u)-min(r,s)<=.015 and max(y,v)-min(b,t)<=.025:
                        extra.append(row);break
            if not extra:break
            group.extend(extra)
        if len(group)<2:continue
        box=(max(0,min(r['rect'][0] for r in group)-.004),
             max(0,min(r['rect'][1] for r in group)-.004),
             min(1,max(r['rect'][2] for r in group)+.004),
             min(1,max(r['rect'][3] for r in group)+.004))
        if box not in result:result.append(box)
    return result
