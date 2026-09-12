"""CPU source-face tracking; every output frame comes from the same source time."""
import json
from pathlib import Path
import subprocess
import math
from collections import deque


def frame_interval(start,duration,fps,total_frames):
    """Use one frame grid for seek and length; never request frames past EOF."""
    if not all(math.isfinite(v) for v in (start,duration,fps)) or start<0 or duration<=0 or not 0<fps<=120:
        raise ValueError('动态取景时间或帧率无效')
    first=round(start*fps)
    count=round(duration*fps)
    if total_frames>0 and first+count>total_frames:
        shortage=first+count-total_frames
        if shortage>2:raise ValueError(f'源视频比选段短{shortage/fps:.3f}秒，须重新核对音视频边界')
        count=total_frames-first  # At most two frame rounding; no padding frames.
    if count<=0:raise ValueError('选段位于源视频帧范围之外')
    return first,count


def crop_box(face, width, height, ratio=632/470, exclusions=()):
    x,y,w,h=map(float,face[:4])
    ch=min(height, h*2.1, width/ratio)
    cw=ch*ratio
    left=max(0,min(width-cw,x+w/2-cw/2))
    top=max(0,min(height-ch,y-h*.48))
    # A partial top-left station logo can evade final OCR after a wide-shot
    # crop. Use the source gate's measured rectangles, while retaining headroom.
    for bx0,by0,bx1,by1 in exclusions:
        bx0,bx1=bx0*width,bx1*width
        by0,by1=by0*height,by1*height
        if left<bx1 and left+cw>bx0 and top<by1 and top+ch>by0:
            safe_top=(int(by1)+5)//2*2
            if safe_top<=y-h*.22 and safe_top+ch<=height:
                top=max(top,safe_top)
    return tuple(int(v)//2*2 for v in (left,top,cw,ch))


def complete_face(face, width, height):
    x,y,w,h=map(float,face[:4])
    return (min(w,h)>=48 and x>=8 and y>=max(8,h*.18)
            and x+w<=width-8 and y+h<=height-2)


def render_tracked(src,start,duration,output,reference,model_paths,threshold=.363,exclusions=(),
                   context_crop=None,participant_reference=None,reference_samples=()):
    """Track the reference identity through camera cuts, with no static fallback."""
    import cv2
    cv2.setNumThreads(2)
    detector=cv2.FaceDetectorYN.create(str(model_paths[0]),'',(320,320),
        score_threshold=.8,nms_threshold=.3,top_k=5000)
    recognizer=cv2.FaceRecognizerSF.create(str(model_paths[1]),'')

    def faces(frame):
        h,w=frame.shape[:2]
        scale=min(1,960/max(w,h))
        small=cv2.resize(frame,(round(w*scale),round(h*scale))) if scale<1 else frame
        detector.setInputSize((small.shape[1],small.shape[0]))
        _,found=detector.detect(small)
        if found is None:return []
        rows=found.copy()
        rows[:,:14]/=scale
        return rows

    ref=cv2.imread(str(reference)); refs=faces(ref)
    if not len(refs):raise ValueError('动态取景参考照未检出人脸')
    rf=max(refs,key=lambda f:float(f[2]*f[3]))
    identity=recognizer.feature(recognizer.alignCrop(ref,rf))
    identities=[identity]
    for path in reference_samples:
        extra=cv2.imread(str(path));found=faces(extra)
        for face in found:
            feature=recognizer.feature(recognizer.alignCrop(extra,face))
            if float(recognizer.match(identity,feature,cv2.FaceRecognizerSF_FR_COSINE))>=threshold:
                identities.append(feature)
    participant=None
    if participant_reference is not None:
        other=cv2.imread(str(participant_reference));found=faces(other)
        if not len(found):raise ValueError('访谈另一参与者参考帧没有人脸')
        of=max(found,key=lambda f:float(f[2]*f[3]))
        participant=recognizer.feature(recognizer.alignCrop(other,of))
        if float(recognizer.match(identity,participant,cv2.FaceRecognizerSF_FR_COSINE))>=threshold:
            raise ValueError('访谈另一参与者参考帧重复使用了嘉宾身份')
    cap=cv2.VideoCapture(str(src));fps=cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps>120:raise ValueError('动态取景源帧率无效')
    first_frame,count=frame_interval(start,duration,fps,round(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    cap.set(cv2.CAP_PROP_POS_FRAMES,first_frame)
    output=Path(output); log=output.with_suffix('.ffmpeg.log')
    matched=0;missing=0;longest_missing=0;previous=None;first=None;last=None
    other_faces=0;no_face=0;blank_streak=0
    decoded=0;encoded=0;frame=None;n=0;recent=deque(maxlen=7)
    last_target_time=None
    target_times=[];roles=[];context_frames=0;picture_frames=0;unmatched_detections=0
    from stable_framing import StableFraming
    framing = StableFraming(fps)

    def evidence(error=None):
        proof=dict(engine='yunet_sface_per_frame_cpu',source_start=start,duration=duration,
            source_first_frame=first_frame,encoded_duration=encoded/fps,
            frames=count,decoded_frames=decoded,encoded_frames=encoded,
            matched_frames=matched,other_face_frames=other_faces,no_face_frames=no_face,
            longest_unmatched_seconds=longest_missing/fps,
            first_crop=first,last_crop=last,threshold=threshold,
            passed=error is None and (context_frames/count>=.7 and len(target_times)>=6
                if context_crop is not None else matched/count>=.8),matched_ratio=matched/count)
        proof['framing'] = framing.proof()
        if context_crop is not None:
            strong=[]
            for t in target_times:
                index=round(t*fps-.5)
                if strong and strong[-1][1]==index:strong[-1][1]=index+1
                else:strong.append([index,index+1])
            proof.update(mode='verified_interview_context_v1',verified_face_ratio=context_frames/count,
                source_frames_preserved=decoded==encoded==count,source_crop=list(context_crop),
                target_reference_spans=strong,
                context_picture_frames=picture_frames,unmatched_detection_frames=unmatched_detections,
                roles=roles,target_sample_times=[target_times[min(len(target_times)-1,
                    int(len(target_times)*(i+.5)/6))] for i in range(6)] if len(target_times)>=6 else [])
        if error is not None:
            directory=output.with_suffix('.evidence');directory.mkdir(exist_ok=True)
            samples=[]
            for index,jpeg in recent:
                name=f'source-{first_frame+index}.jpg'
                (directory/name).write_bytes(jpeg)
                samples.append(dict(file=name,source_time=(first_frame+index)/fps))
            if frame is not None:
                cv2.imwrite(str(directory/'failure.jpg'),frame)
            proof.update(error=str(error),failure_source_time=(first_frame+n)/fps,
                failure_relative_time=n/fps,last_target_source_time=last_target_time,
                consecutive_no_face_seconds=blank_streak/fps,
                evidence_directory=directory.name,samples=samples)
        output.with_suffix('.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
        return proof
    with log.open('wb') as errors:
        proc=subprocess.Popen(['ffmpeg','-y','-loglevel','error','-f','rawvideo',
            '-pix_fmt','bgr24','-s','632x470','-r',str(fps),'-i','-',
            '-an','-c:v','libx264','-preset','veryfast','-crf','18',
            '-pix_fmt','yuv420p',str(output)],stdin=subprocess.PIPE,stderr=errors)
        try:
            for n in range(count):
                ok,frame=cap.read()
                if not ok:raise ValueError(f'动态取景解码提前结束：实际{n}/{count}帧，起始帧{first_frame}，fps={fps}')
                decoded+=1
                framing.observe(frame, n)
                if n%max(1,round(fps/2))==0:
                    saved,jpeg=cv2.imencode('.jpg',frame)
                    if saved:recent.append((n,jpeg.tobytes()))
                height,width=frame.shape[:2]; candidates=[];other_candidates=[];role=None
                detected=faces(frame)
                for face in detected:
                    try:
                        feature=recognizer.feature(recognizer.alignCrop(frame,face))
                        score=max(float(recognizer.match(identity,feature,cv2.FaceRecognizerSF_FR_COSINE))
                                  for identity in identities)
                    except cv2.error:
                        continue
                    if score>=threshold and min(face[2:4])>=96:
                        primary=float(recognizer.match(identities[0],feature,cv2.FaceRecognizerSF_FR_COSINE))
                        candidates.append((score,face,primary))
                    elif participant is not None:
                        other_score=float(recognizer.match(participant,feature,cv2.FaceRecognizerSF_FR_COSINE))
                        if other_score>=threshold:other_candidates.append((other_score,face))
                if candidates:
                    score,face,primary=max(candidates,key=lambda row:row[0])
                    box=crop_box(face,width,height,exclusions=exclusions);matched+=1;missing=0;blank_streak=0
                    last_target_time=(first_frame+n)/fps
                    box=framing.update(box,face,width,height,n)
                    previous=box
                    role='guest';context_frames+=1
                    if primary>=threshold+.03:target_times.append((n+.5)/fps)
                else:
                    missing+=1;longest_missing=max(longest_missing,missing)
                    unknown=bool(len(detected)) and context_crop is not None and not other_candidates
                    if unknown:
                        # B-roll can contain strangers or face-like numerals.
                        # Keep the original composition, never make an unknown
                        # detection into a guest/host close-up or count it as one.
                        unmatched_detections+=1;detected=[]
                    if len(detected):
                        # Preserve a brief original interviewer reaction shot. It
                        # is explicitly NOT counted as a matched target frame;
                        # aggregate 80% and final independent 5/6 gates still apply.
                        face=(max(other_candidates,key=lambda x:x[0])[1] if context_crop is not None
                              else max(detected,key=lambda f:float(f[2]*f[3])))
                        box=crop_box(face,width,height,exclusions=exclusions)
                        box=framing.update(box,face,width,height,n);previous=box
                        other_faces+=1;blank_streak=0
                        role='participant';context_frames+=1
                    else:
                        no_face+=int(not unknown);blank_streak+=1
                        if context_crop is not None:
                            box=context_crop;role='source_illustration';picture_frames+=1
                        elif blank_streak/fps>2:
                            raise ValueError(f'动态取景连续{blank_streak/fps:.2f}秒缺少人脸')
                        elif previous is None:
                            # A cut/fade at the opening has no previous face box.
                            # Preserve the current source frame, never freeze a
                            # future portrait or shift audio; 80% identity gate
                            # and the two-second bound still apply.
                            box=(0,0,width,height);role='source_illustration'
                        else:box=previous
                x,y,w,h=box
                if role in {'guest','participant'} and (w < 316 or h < 235):
                    raise ValueError(f'真人取景源区域仅{w}x{h}像素，超过2倍放大上限，疑似远景小头像')
                region=frame[y:y+h,x:x+w]
                if region.shape[:2]!=(h,w):raise ValueError('动态取景越出源画面')
                if role=='source_illustration':
                    # Preserve the original contemporaneous illustration, with
                    # its aspect ratio. Never freeze a guest portrait over it.
                    if float(region.mean())<5 and float(region.std())<2:
                        raise ValueError('资料画面为空黑帧')
                    scale=min(632/w,470/h);rw,rh=round(w*scale),round(h*scale)
                    fitted=cv2.resize(region,(rw,rh),interpolation=cv2.INTER_LANCZOS4)
                    result=cv2.copyMakeBorder(fitted,(470-rh)//2,470-rh-(470-rh)//2,
                        (632-rw)//2,632-rw-(632-rw)//2,cv2.BORDER_CONSTANT,value=(250,250,250))
                else:
                    result=cv2.resize(region,(632,470),interpolation=cv2.INTER_LANCZOS4)
                if context_crop is not None:
                    if roles and roles[-1]['role']==role:roles[-1]['end_frame']=n+1
                    else:roles.append(dict(role=role,start_frame=n,end_frame=n+1))
                proc.stdin.write(result.tobytes())
                encoded+=1
                if first is None:first=list(box)
                last=list(box)
                if n%max(1,round(fps*30))==0:
                    print(f'[动态取景] {n/fps:.0f}/{duration:.0f}s，身份匹配{matched}/{n+1}帧',flush=True)
            proc.stdin.close()
            if proc.wait(timeout=120):raise ValueError('动态取景编码失败：'+log.read_text()[-1000:])
        except BaseException as exc:
            proc.kill();proc.wait();output.unlink(missing_ok=True)
            try:
                evidence(exc)
            except Exception as diagnostic_error:
                print(f'[取景证据] 保存失败：{diagnostic_error}',flush=True)
            raise
        finally:
            cap.release()
    proof=evidence()
    if context_crop is not None and (context_frames/count<.7 or len(target_times)<6):
        output.unlink(missing_ok=True)
        raise ValueError(f'访谈已核验人物动态不足70%：{context_frames}/{count}帧')
    if context_crop is None and matched/count<.8:
        output.unlink(missing_ok=True)
        raise ValueError(f'动态取景目标人物匹配不足80%：{matched}/{count}帧（{matched/count:.1%}），'
                         f'其他人脸{other_faces}帧，无人脸{no_face}帧；已保存取景证据')
    return proof
