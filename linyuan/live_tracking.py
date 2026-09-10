"""CPU source-face tracking; every output frame comes from the same source time."""
import json
from pathlib import Path
import subprocess
import math


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


def render_tracked(src,start,duration,output,reference,model_paths,threshold=.363,exclusions=()):
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
    cap=cv2.VideoCapture(str(src));fps=cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps>120:raise ValueError('动态取景源帧率无效')
    first_frame,count=frame_interval(start,duration,fps,round(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    cap.set(cv2.CAP_PROP_POS_FRAMES,first_frame)
    output=Path(output); log=output.with_suffix('.ffmpeg.log')
    matched=0;missing=0;longest_missing=0;previous=None;first=None;last=None
    other_faces=0;no_face=0;blank_streak=0
    with log.open('wb') as errors:
        proc=subprocess.Popen(['ffmpeg','-y','-loglevel','error','-f','rawvideo',
            '-pix_fmt','bgr24','-s','632x470','-r',str(fps),'-i','-',
            '-an','-c:v','libx264','-preset','veryfast','-crf','18',
            '-pix_fmt','yuv420p',str(output)],stdin=subprocess.PIPE,stderr=errors)
        try:
            for n in range(count):
                ok,frame=cap.read()
                if not ok:raise ValueError(f'动态取景解码提前结束：实际{n}/{count}帧，起始帧{first_frame}，fps={fps}')
                height,width=frame.shape[:2]; candidates=[]
                detected=faces(frame)
                for face in detected:
                    try:
                        feature=recognizer.feature(recognizer.alignCrop(frame,face))
                        score=float(recognizer.match(identity,feature,cv2.FaceRecognizerSF_FR_COSINE))
                    except cv2.error:
                        continue
                    if score>=threshold:candidates.append((score,face))
                if candidates:
                    score,face=max(candidates,key=lambda row:row[0])
                    box=crop_box(face,width,height,exclusions=exclusions);matched+=1;missing=0;blank_streak=0
                    if previous is not None:
                        # Smooth ordinary movement, but snap to a new shot immediately.
                        delta=max(abs(box[k]-previous[k]) for k in range(4))
                        if delta<min(box[2:])*.12:
                            box=tuple(round((previous[k]*.65+box[k]*.35)/2)*2 for k in range(4))
                    previous=box
                else:
                    missing+=1;longest_missing=max(longest_missing,missing)
                    if len(detected):
                        # Preserve a brief original interviewer reaction shot. It
                        # is explicitly NOT counted as a matched target frame;
                        # aggregate 80% and final independent 5/6 gates still apply.
                        face=max(detected,key=lambda f:float(f[2]*f[3]))
                        box=crop_box(face,width,height,exclusions=exclusions);previous=box
                        other_faces+=1;blank_streak=0
                    else:
                        no_face+=1;blank_streak+=1
                        if previous is None or blank_streak/fps>2:
                            raise ValueError(f'动态取景连续{blank_streak/fps:.2f}秒缺少人脸')
                        box=previous
                x,y,w,h=box
                region=frame[y:y+h,x:x+w]
                if region.shape[:2]!=(h,w):raise ValueError('动态取景越出源画面')
                result=cv2.resize(region,(632,470),interpolation=cv2.INTER_LANCZOS4)
                proc.stdin.write(result.tobytes())
                if first is None:first=list(box)
                last=list(box)
                if n%max(1,round(fps*30))==0:
                    print(f'[动态取景] {n/fps:.0f}/{duration:.0f}s，身份匹配{matched}/{n+1}帧',flush=True)
            proc.stdin.close()
            if proc.wait(timeout=120):raise ValueError('动态取景编码失败：'+log.read_text()[-1000:])
        except BaseException:
            proc.kill();proc.wait();output.unlink(missing_ok=True);raise
        finally:
            cap.release()
    proof=dict(engine='yunet_sface_per_frame_cpu',source_start=start,duration=duration,
        source_first_frame=first_frame,encoded_duration=count/fps,
        frames=count,matched_frames=matched,other_face_frames=other_faces,no_face_frames=no_face,
        longest_unmatched_seconds=longest_missing/fps,
        first_crop=first,last_crop=last,threshold=threshold,
        passed=matched/count>=.8,matched_ratio=matched/count)
    output.with_suffix('.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    if matched/count<.8:
        output.unlink(missing_ok=True)
        raise ValueError(f'动态取景目标人物匹配不足80%：{matched}/{count}帧（{matched/count:.1%}），'
                         f'其他人脸{other_faces}帧，无人脸{no_face}帧；已保存取景证据')
    return proof
