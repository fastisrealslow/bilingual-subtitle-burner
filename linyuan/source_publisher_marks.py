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


def corner_publisher_rects(evidence, frame):
    """Measure red calligraphy missed by OCR beside a recognized publisher.

    Only the high-confidence WEEKLY ON STOCKS corner mark enables this local
    color measurement. Red scene pixels elsewhere never create exclusions.
    """
    import numpy as np
    height,width=frame.shape[:2]
    result=[]
    for row in evidence:
        name=re.sub(r'\W+','',str(row.get('text') or '')).casefold()
        x,y,r,b=row['rect']
        if (name!='weeklyonstocks' or float(row.get('confidence') or 0)<.9
                or not 0<x<r<.25 or not 0<y<b<.16):
            continue
        # Bounded neighborhood of this known wordmark, measured on the actual
        # source frame. Its handwritten 红 extends beyond recognized letters.
        left=max(0,int((x-.14)*width));right=min(width,int((r+.02)*width)+1)
        top=max(0,int((y-.12)*height));bottom=min(height,int((b+.035)*height)+1)
        region=frame[top:bottom,left:right].astype(np.float32)
        blue,green,red=region[:,:,0],region[:,:,1],region[:,:,2]
        mask=(red>130)&(red>green*1.35)&(red>blue*1.3)
        ys,xs=np.nonzero(mask)
        if len(xs)<8:continue
        # Include the English OCR anchor and measured colored strokes. Two
        # actual source pixels provide a modest encoding/measurement margin.
        box=(max(0,min(x,(left+xs.min())/width)-2/width),
             max(0,min(y,(top+ys.min())/height)-2/height),
             min(1,max(r,(left+xs.max()+1)/width)+2/width),
             min(1,max(b,(top+ys.max()+1)/height)+2/height))
        if box not in result:result.append(box)
    return result
