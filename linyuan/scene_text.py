"""Conservative CPU evidence that localized text belongs to the filmed scene.

Uncertain text stays in the overlay gate. Neither low recognition confidence
nor a fixed word is evidence of a clean picture. Two other sampled frames must
show the text following nearby background features under camera movement.
"""
import math

VERSION = 1


def rect(box):
    return (min(p[0] for p in box), min(p[1] for p in box),
            max(p[0] for p in box), max(p[1] for p in box))


def localized(box, width, height):
    x,y,r,b=rect(box)
    # Keep titles, corner watermarks, lower subtitles and large text uncertain.
    # Perspective is additional evidence, never sufficient by itself.
    slope=math.degrees(math.atan2(box[1][1]-box[0][1],box[1][0]-box[0][0]))
    return (.20*height<y<b<.78*height and r-x<.28*width
            and b-y<.10*height and 2<abs(slope)<20)


def iou(a,b):
    x,y,r,d=a;u,v,s,t=b
    intersection=max(0,min(r,s)-max(x,u))*max(0,min(d,t)-max(y,v))
    return intersection/max(1,(r-x)*(d-y)+(s-u)*(t-v)-intersection)


def classify(samples):
    """Return kept OCR boxes plus reproducible decisions; never approve media."""
    import cv2
    import numpy as np
    decisions=[]; kept=[]
    if len(samples)<3:
        return [s['boxes'] for s in samples],decisions
    orb=cv2.ORB_create(nfeatures=2500)
    features=[orb.detectAndCompute(s['frame'],None) for s in samples]
    matcher=cv2.BFMatcher(cv2.NORM_HAMMING)
    for i,sample in enumerate(samples):
        height,width=sample['frame'].shape[:2]
        candidates=[k for k,b in enumerate(sample['boxes']) if localized(b,width,height)]
        supports={k:[] for k in candidates}
        ka,da=features[i]
        if candidates and da is not None:
            # Nearby times are most likely to retain the same camera shot.
            for j in sorted((n for n in range(len(samples)) if n!=i),key=lambda n:abs(n-i)):
                kb,db=features[j]
                if db is None:continue
                pairs=matcher.knnMatch(da,db,k=2)
                good=[p[0] for p in pairs if len(p)==2 and p[0].distance<.7*p[1].distance]
                if len(good)<20:continue
                a=np.float32([ka[m.queryIdx].pt for m in good])
                b=np.float32([kb[m.trainIdx].pt for m in good])
                transform,mask=cv2.findHomography(a,b,cv2.RANSAC,2)
                if transform is None or mask is None or int(mask.sum())<20:continue
                inliers=mask.ravel().astype(bool)
                for k in candidates:
                    if len(supports[k])>=2:continue
                    box=sample['boxes'][k];x,y,r,d=rect(box)
                    projected=cv2.perspectiveTransform(np.float32([box]),transform)[0]
                    displacement=float(np.linalg.norm(projected.mean(axis=0)-np.mean(box,axis=0)))
                    if not 4<=displacement<=width*.12:continue
                    target=next((n for n,other in enumerate(samples[j]['boxes'])
                        if localized(other,width,height) and iou(rect(projected),rect(other))>=.65
                        and iou(rect(box),rect(other))<.70),None)
                    if target is None:continue
                    # Require surrounding scene features, not matches on the
                    # glyphs themselves (a moving on-screen label can do that).
                    pad=max(16,(d-y)*2)
                    near=(a[:,0]>x-pad)&(a[:,0]<r+pad)&(a[:,1]>y-pad)&(a[:,1]<d+pad)
                    outside=(a[:,0]<x-3)|(a[:,0]>r+3)|(a[:,1]<y-3)|(a[:,1]>d+3)
                    local=a[inliers&near&outside]
                    if len(local)<8 or np.ptp(local[:,0])<r-x or np.ptp(local[:,1])<d-y:continue
                    supports[k].append(dict(frame=j,target_box=target,
                        displacement_px=round(displacement,3),background_inliers=len(local)))
                if all(len(v)>=2 for v in supports.values()):break
        excluded={k for k,v in supports.items() if len(v)>=2}
        kept.append([box for k,box in enumerate(sample['boxes']) if k not in excluded])
        for k in sorted(excluded):
            decisions.append(dict(frame=i,box=sample['boxes'][k],classification='scene_text',
                evidence=supports[k],final_quality_approved=False))
    return kept,decisions
