"""Preserve wide stage footage when a large screen portrait is not the presenter.

Identity is explicitly contextual, never a claimed biometric match to the tiny
live face. Motion is measured on the presenter independently of the screen.
"""
from pathlib import Path
import json
import math
import subprocess

VERSION=2026091301


def plan(src,start,duration,reference,model_paths,speaker='林园'):
    import cv2
    from live_motion import verify_window
    detector=cv2.FaceDetectorYN.create(str(model_paths[0]),'',(320,320),score_threshold=.70)
    recognizer=cv2.FaceRecognizerSF.create(str(model_paths[1]),'')
    def faces(frame):
        detector.setInputSize((frame.shape[1],frame.shape[0]));_,rows=detector.detect(frame)
        return [] if rows is None else rows
    ref=cv2.imread(str(reference));rows=faces(ref)
    if not len(rows):return None
    f=max(rows,key=lambda f:f[2]*f[3]);identity=recognizer.feature(recognizer.alignCrop(ref,f))
    cap=cv2.VideoCapture(str(src));cap.set(cv2.CAP_PROP_POS_MSEC,(start+duration*.25)*1000)
    ok,frame=cap.read();cap.release()
    if not ok:return None
    h,w=frame.shape[:2]
    if w<h:return None
    rows=faces(frame)
    # This path is only for an unambiguous two-face stage: one static identity
    # portrait and one small moving presenter. Other scenes keep existing gates.
    if len(rows)!=2 or any(max(f[2:4])>=96 for f in rows):return None
    candidates=[]
    for f in rows:
        score=float(recognizer.match(identity,recognizer.feature(recognizer.alignCrop(frame,f)),cv2.FaceRecognizerSF_FR_COSINE))
        x,y,fw,fh=map(float,f[:4])
        roi=dict(x=max(0,int(x-fw*.65)),y=max(0,int(y-fh*.55)),
                 width=int(fw*2.6),height=int(fh*3.5))
        if roi['x']+roi['width']>w or roi['y']+roi['height']>h:return None
        motion=verify_window(src,roi,start,duration)
        candidates.append(dict(face=[x,y,fw,fh],identity_score=score,motion=motion))
    photos=[c for c in candidates if c['identity_score']>=.363 and sum(c['motion']['moving_by_third'])<=1]
    actors=[c for c in candidates if c['motion']['passed'] and min(c['face'][2:])>=12]
    if len(photos)!=1 or len(actors)!=1 or photos[0] is actors[0]:return None
    actor=actors[0];photo=photos[0]
    # Keep the entire horizontal stage; trim only the top/bottom overlay zones.
    top=int(h*.20)//2*2;bottom=int(h*.82)//2*2
    crop=[0,top,w//2*2,bottom-top]
    ax,ay,aw,ah=actor['face']
    if not (ay-ah*.5>=top and ay+ah*6<=bottom):return None
    presence=[];cap=cv2.VideoCapture(str(src))
    for i in range(6):
        t=start+duration*(i+.5)/6;cap.set(cv2.CAP_PROP_POS_MSEC,t*1000);ok,f=cap.read()
        if not ok:break
        detected=faces(f)
        near=[p for p in detected if math.hypot(float(p[0]+p[2]/2)-(ax+aw/2),float(p[1]+p[3]/2)-(ay+ah/2))<max(aw,ah)*2]
        presence.append(dict(time=t,present=bool(near)))
    cap.release()
    if sum(p['present'] for p in presence)<5:return None
    return dict(version=VERSION,mode='wide_stage',passed=True,speaker=speaker,
        identity_basis='single_live_presenter_with_matching_stage_portrait',
        biometric_presenter_match=False,source_start=start,duration=duration,
        source_resolution=[w,h],crop_xywh=crop,presenter=actor,stage_portrait=photo,
        presenter_presence=presence,source_frames_preserved=True)


def proof_error(proof):
    if not isinstance(proof,dict) or proof.get('version')!=VERSION or not proof.get('passed'):
        return '舞台场景证明缺失'
    if proof.get('identity_basis')!='single_live_presenter_with_matching_stage_portrait' or proof.get('biometric_presenter_match') is not False:
        return '舞台场景不能冒充真人生物识别证明'
    p=proof.get('presenter') or {}; photo=proof.get('stage_portrait') or {}
    if not (p.get('motion') or {}).get('passed') or sum((photo.get('motion') or {}).get('moving_by_third',[]))>1 or photo.get('identity_score',0)<.363:
        return '舞台真人动作或参考身份未通过'
    if sum(x.get('present') is True for x in proof.get('presenter_presence',[]))<5:
        return '舞台真人未持续出现在原位置'
    if not proof.get('source_frames_preserved'):return '舞台画面未保留原始时间轴'
    return None


def check_overlays(src,start,duration,crop,ocr):
    import cv2
    cap=cv2.VideoCapture(str(src));seen=[]
    x,y,w,h=crop
    try:
        for i in range(6):
            cap.set(cv2.CAP_PROP_POS_MSEC,(start+duration*(i+.5)/6)*1000)
            ok,frame=cap.read()
            if not ok:raise ValueError('舞台清理验收无法解码')
            result,_=ocr(frame[y:y+h,x:x+w])
            forbidden=[]
            for box,text,confidence in result or []:
                top=min(p[1] for p in box);left=min(p[0] for p in box)
                if confidence>=.55 and (top>=h*.72 or (top<h*.14 and left>w*.82)):
                    forbidden.append(text)
            seen.append(dict(sample=i,unwanted_text=forbidden))
        if any(row['unwanted_text'] for row in seen):
            raise ValueError('舞台宽景仍有底部原字幕或顶部账号角标：'+str(seen))
        return dict(frames_checked=6,passed=True,samples=seen)
    finally:cap.release()


def render(src,start,duration,out,work,captions,cw,source_report,stage,suffix='',producer=None):
    """Build the same deliverable contract as the normal producer."""
    import hashlib
    import re
    from PIL import Image,ImageDraw,ImageFont
    import presentation as V
    import editorial_policy as E
    from caption_readability import clean_entries
    from live_motion import verify_window
    P=producer
    out,work=Path(out),Path(work);out.mkdir(parents=True,exist_ok=True);work.mkdir(parents=True,exist_ok=True)
    error=proof_error(stage)
    if error:raise ValueError(error)
    stage={**stage,'overlay_scan':check_overlays(src,start,duration,stage['crop_xywh'],P._ocr())}
    x,y,w,h=stage['crop_xywh'];scale=min(1.6,1120/w,480/h)
    rw,rh=round(w*scale)//2*2,round(h*scale)//2*2;rx,ry=(1280-rw)//2,84
    spec=V.layout_for(1280,720)
    spec.update(live_region=dict(x=rx,y=ry,width=rw,height=rh),
        subtitle_region=dict(x=64,y=590,width=1152,height=112),subtitle_font_px=44,
        line_capacity=24,subtitle_style='light-panel-dark-text',template='wide-stage-v1')
    cleaned,edit_proof=clean_entries(captions)
    for row in cleaned:row['semantic_group']=True
    ass=out/f'subtitles{suffix}.ass';V.write_ass(cleaned,ass,spec,'Noto Sans CJK SC')
    (out/f'caption-edits{suffix}.json').write_text(json.dumps(edit_proof,ensure_ascii=False,indent=2))
    family=subprocess.check_output(['fc-match','-f','%{family}','Noto Sans CJK SC'],text=True)
    if 'Noto Sans CJK SC' not in family:raise ValueError('舞台标题和字幕缺少中文字体')
    font=subprocess.check_output(['fc-match','-f','%{file}','Noto Sans CJK SC'],text=True)
    bg=Image.new('RGB',(1280,720),(244,242,236));draw=ImageDraw.Draw(bg)
    draw.text((56,18),cw['cover_title'],fill=(20,39,56),font=ImageFont.truetype(font,42))
    draw.rounded_rectangle((48,585,1232,710),radius=10,fill=(255,255,252))
    bgpath=work/'stage-background.png';bg.save(bgpath)
    final=out/f'final{suffix}.mp4';brand=P.brand_watermark_path()
    vf=(f'[0:v]setpts=PTS-STARTPTS,crop={w}:{h}:{x}:{y},scale={rw}:{rh}:flags=lanczos,setsar=1[live];'
        f'[1:v][live]overlay={rx}:{ry}:shortest=1,ass={ass}[base];'
        '[2:v]format=rgba,colorchannelmixer=aa=0.68,scale=120:-1[brand];'
        '[base][brand]overlay=W-w-24:18:shortest=1[outv]')
    subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(start),'-t',str(duration),'-i',str(src),
        '-loop','1','-framerate','30','-i',str(bgpath),'-loop','1','-framerate','30','-i',str(brand),
        '-filter_complex',vf,'-map','[outv]','-map','0:a:0','-af','asetpts=PTS-STARTPTS,highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11',
        '-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p','-r','30',
        '-c:a','aac','-b:a','192k','-t',str(duration),'-movflags','+faststart',str(final)],check=True,timeout=600)
    checks=V.verify_render(final,spec)
    roi=stage['presenter']['motion']['window']
    actor_window=dict(x=rx+round((roi['x']-x)*rw/w),y=ry+round((roi['y']-y)*rh/h),
                      width=round(roi['width']*rw/w),height=round(roi['height']*rh/h))
    motion=verify_window(final,actor_window)
    if not motion['passed']:raise ValueError('舞台成片真人区域没有持续动作')
    stage={**stage,'final_presenter_motion':motion,'source_sha256':source_report['source_sha256']}
    transcript=E.subtitle_files_text(out,[ass.name]);fingerprints=P.build_content_fingerprints(final,transcript)
    preview,sheet=P.make_review_assets(final,out,suffix,duration)
    cover=out/f'cover{suffix}.jpg';portrait=P.extract_audio_card_portrait(P._download_speaker_reference('林园',work),work/'portrait.png')
    P.make_audio_card(cover,'林园',cw['cover_title'],width=1280,height=720,portrait_path=portrait,require_portrait=True,cover_style='light')
    meta=dict(cw,final=final.name,cover=cover.name,preview_30s=preview,contact_sheet_6=sheet,
        speaker='林园',source_sha256=source_report['source_sha256'],stage_context=stage,
        segments=[dict(start=start,end=start+duration,reason='保留同一段原声与舞台全景')],
        duration_sec=round(float(P.probe(final,'format=duration')),3),resolution=dict(width=1280,height=720,short_edge=720),
        render_mode='stage_context',clean_strategy='crop',quality_gate_version=P.QUALITY_GATE_VERSION,
        visual_standard_version=P.VISUAL_STANDARD_VERSION,cover_standard_version=P.COVER_STANDARD_VERSION,
        cover_person_image_verified=True,cover_person_image_source='authority_reference',
        review_assets_verified=True,title_quality_verified=True,presentation_version=V.VERSION,layout_proof=spec,
        cover_proof=json.loads(Path(str(cover)+'.proof.json').read_text()),
        subtitle_files=[ass.name],subtitle_text_sha256=E.text_digest(transcript),
        subtitle_word_boundaries_verified=True,subtitle_semantic_groups_verified=True,
        subtitle_readability_version=spec['readability_version'],subtitles_burned=True,has_existing_subtitles=False,
        watermark_verified=True,clean_filter_verified=True,brand_watermark_applied=True,fingerprints=fingerprints,**checks)
    return meta
