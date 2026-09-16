"""Rank intact source answers by sampled picture risk, never approve an output."""
import json
import time
from pathlib import Path

VERSION = 1


def candidate_order(picks, observations):
    """Unknown evidence remains eligible; bad previews cannot discard a source."""
    def key(index):
        rows=observations[index]
        known=[r for r in rows if r.get('status') in ('possible','risk')]
        if not known:return (1,0,0,picks[index].get('editorial_rank',index))
        risky=sum(r['status']=='risk' for r in known)
        # A complete clean sample set is preferable to an incomplete preview.
        group=0 if not risky and len(known)==len(rows) else 2 if risky else 1
        return (group,risky/len(known),sum(r.get('text_rows',0) for r in known)/len(known),
                picks[index].get('editorial_rank',index))
    return sorted(range(len(picks)),key=key)


def rank(src,cues,picks,work,reference,models,engine,verify_boxes,threshold=.363,
         sample_budget=120,seconds_budget=120):
    import cv2
    from live_tracking import crop_box
    from scene_text import classify,rect
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    observations=[[] for _ in picks];started=time.monotonic();sampled=0;error=None
    cap=None
    try:
        detector=cv2.FaceDetectorYN.create(str(models[0]),'',(320,320),score_threshold=.8,
                                           nms_threshold=.3,top_k=5000)
        recognizer=cv2.FaceRecognizerSF.create(str(models[1]),'')
        def faces(frame):
            h,w=frame.shape[:2];scale=min(1,960/max(w,h))
            small=cv2.resize(frame,(round(w*scale),round(h*scale))) if scale<1 else frame
            detector.setInputSize((small.shape[1],small.shape[0]));_,found=detector.detect(small)
            if found is None:return []
            found=found.copy();found[:,:14]/=scale
            return found
        ref=cv2.imread(str(reference))
        if ref is None:raise ValueError('人物参考照不可读')
        found=faces(ref)
        if not len(found):raise ValueError('参考照无法核对人物')
        identity=recognizer.feature(recognizer.alignCrop(ref,max(found,key=lambda f:f[2]*f[3])))
        cap=cv2.VideoCapture(str(src));fps=cap.get(cv2.CAP_PROP_FPS)
        if not fps>0:raise ValueError('选段画面预检无法读取帧率')
        per_pick=max(3,min(6,sample_budget//max(1,len(picks))))
        for i,pick in enumerate(picks):
            start=cues[pick['start']]['start'];end=cues[pick['end']]['end']
            samples=[];matched=[]
            for n in range(per_pick):
                if sampled>=sample_budget or time.monotonic()-started>seconds_budget:break
                when=start+(end-start)*(n+.5)/per_pick
                cap.set(cv2.CAP_PROP_POS_FRAMES,round(when*fps));ok,frame=cap.read();sampled+=1
                row=dict(source_time=round(when,3),status='unknown')
                observations[i].append(row)
                if not ok:continue
                h,w=frame.shape[:2]
                small=cv2.resize(frame,(min(640,w),round(h*min(640,w)/w)))
                boxes,_=engine(small,use_det=True,use_rec=False,use_cls=False)
                samples.append(dict(frame=small,boxes=verify_boxes(small,boxes or [],engine),row=row))
                candidates=[]
                for face in faces(frame):
                    feature=recognizer.feature(recognizer.alignCrop(frame,face))
                    score=float(recognizer.match(identity,feature,cv2.FaceRecognizerSF_FR_COSINE))
                    if score>=threshold:candidates.append((score,face[:4]))
                matched.append((max(candidates,key=lambda x:x[0])[1] if candidates else None,w,h))
            kept,scene=classify(samples)
            for sample,boxes,(face,w,h) in zip(samples,kept,matched):
                row=sample['row'];sh,sw=sample['frame'].shape[:2];marks=[];covered=set()
                for box in boxes:
                    x,y,r,b=rect(box);x/=sw;r/=sw;y/=sh;b/=sh
                    if (x<.35 or r>.65) and (b<.30 or y>.70):marks.append((x,y,r,b))
                    if r-x>.35 and (y>.55 or b<.20):marks.append((0,y,1,b))
                    covered.update(range(max(0,int(y*100)),min(100,int(b*100)+1)))
                row['text_rows']=len(covered);row['source_marks']=marks
                if face is None:continue
                row['face']=list(map(float,face));row['status']='possible'
                try:row['proposed_crop']=crop_box(face,w,h,exclusions=marks)
                except ValueError as exc:row.update(status='risk',reason=str(exc))
            if len(observations[i])<per_pick:observations[i].append(dict(status='unknown',reason='preview_budget'))
            if scene:
                (work/f'scene-{i+1}.json').write_text(json.dumps(scene,ensure_ascii=False))
    except Exception as exc:
        # A preview outage never invalidates ASR or becomes a clean verdict.
        error=f'{type(exc).__name__}: {exc}'
    finally:
        if cap is not None:cap.release()
    order=candidate_order(picks,observations)
    proof=dict(version=VERSION,final_quality_approved=False,sampled_frames=sampled,
        elapsed_sec=round(time.monotonic()-started,3),error=error,
        candidates=[dict(candidate=i+1,start=cues[p['start']]['start'],end=cues[p['end']]['end'],
            editorial_rank=p.get('editorial_rank'),samples=observations[i]) for i,p in enumerate(picks)],
        production_order=[i+1 for i in order])
    (work/'visual-selection.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    return [picks[i] for i in order]
