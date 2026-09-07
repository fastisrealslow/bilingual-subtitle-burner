#!/usr/bin/env python3
"""Cost-free audit of locally rendered candidates; never downloads or uploads."""
import argparse,hashlib,json,re,subprocess,sys
from pathlib import Path

import cv2
import numpy as np

BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(BASE))
import editorial_policy as editorial
import produce_cn as producer


def ass_rows(path):
    rows=[]
    for line in path.read_text(encoding='utf-8-sig',errors='replace').splitlines():
        if not line.startswith('Dialogue:'):continue
        fields=line.split(',',9)
        if len(fields)<10:continue
        def seconds(value):
            h,m,s=value.split(':');return int(h)*3600+int(m)*60+float(s)
        text=re.sub(r'\{[^}]*\}','',fields[9]).replace(r'\N','')
        rows.append((seconds(fields[1]),seconds(fields[2]),text))
    return rows


def video_probe(path):
    result=subprocess.run(['ffprobe','-v','error','-show_entries',
        'format=duration:stream=codec_type','-of','json',str(path)],capture_output=True,text=True)
    if result.returncode:raise ValueError('ffprobe失败')
    data=json.loads(result.stdout)
    return float(data['format']['duration']),{x['codec_type'] for x in data.get('streams',[])}


def live_frames(path,samples=8):
    cap=cv2.VideoCapture(str(path));total=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));got=black=face=0
    detector=cv2.CascadeClassifier(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
    try:
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES,int(total*(i+.5)/samples));ok,frame=cap.read()
            if not ok:continue
            h,w=frame.shape[:2]
            if (w,h)==(720,1280):frame=frame[360:830,44:676]
            gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY);got+=1
            edge=max(8,int(gray.shape[1]*.08));dark=np.mean(gray<18,axis=0)>.92
            black+=int(dark[:edge].mean()>.45 or dark[-edge:].mean()>.45)
            found=detector.detectMultiScale(gray,1.1,4,minSize=(48,48))
            face+=int(any(y>=max(8,int(fh*.18)) and x>=8 and x+fw<=gray.shape[1]-8
                          and y+fh<=gray.shape[0]-2 for x,y,fw,fh in found))
    finally:cap.release()
    return {'sampled':got,'black_edge_frames':black,'full_face_frames':face}


def metadata(directory):
    path=directory/'meta.json'
    if not path.exists():return []
    data=json.loads(path.read_text())
    return data if isinstance(data,list) else [data]


def audit(root):
    approvals_path=BASE/'local_user_approvals.json'
    approvals=(json.loads(approvals_path.read_text()).get('approved_video_sha256',{})
               if approvals_path.exists() else {})
    seen=set();rows=[]
    for video in sorted(root.glob('*/final*.mp4')):
        digest=hashlib.sha256(video.read_bytes()).hexdigest()
        if digest in seen:continue
        seen.add(digest)
        part_match=re.search(r'final(?:_(\d+))?\.mp4$',video.name)
        part=int(part_match.group(1) or 1)
        metas=metadata(video.parent)
        meta=next((x for x in metas if int(x.get('part') or 1)==part),metas[0] if len(metas)==1 else {})
        subtitle_names=meta.get('subtitle_files') or []
        if not subtitle_names:
            subtitle_names=[f'subtitles_{part}-1.ass',f'subtitles-{part}.ass']
        subtitles=[video.parent/x for x in subtitle_names if (video.parent/x).exists()]
        cues=[row for path in subtitles for row in ass_rows(path)]
        chinese=''.join(x[2] for x in cues)
        issues=[]
        try:duration,streams=video_probe(video)
        except Exception as exc:duration=0;streams=set();issues.append(str(exc))
        if duration<120:issues.append('不足120秒')
        if not {'audio','video'}<=streams:issues.append('缺少音频或视频轨')
        if not cues:issues.append('缺少可核对字幕')
        integrity=editorial.transcript_integrity_error(chinese)
        if integrity:issues.append(integrity)
        # Keep this auditor dependency-free. These connectors cannot naturally
        # end an argument; the production gate performs the fuller word check.
        if cues and re.sub(r'[，。！？；：、,.!?;\s]+$','',cues[-1][2]).endswith(
                ('就是','但是','所以','因为','然后','这个','那个','的话','我们')):
            issues.append('结尾字幕不是完整意群')
        if cues and min(b-a for a,b,_ in cues)<.8:issues.append('存在短于0.8秒的闪屏字幕')
        mode=meta.get('render_mode') or 'unknown'
        visual=live_frames(video) if mode=='live_video_card' else {}
        if visual and visual['black_edge_frames']>=max(2,visual['sampled']//2):issues.append('明显黑区')
        if visual and visual['full_face_frames']<max(3,int(visual['sampled']*.7+.999)):issues.append('完整人脸抽帧不足')
        user_approval=approvals.get(digest)
        technical_qualified=not issues
        semantic_verified=(meta.get('text_backend')=='local'
            and isinstance(meta.get('editorial_review'),dict)
            and not editorial.review_error(meta['editorial_review']))
        if not semantic_verified and not user_approval:issues.append('尚未完成本地语义审核')
        if user_approval:
            issues=[]
        rows.append({'file':str(video),'sha256':digest,'duration_sec':round(duration,1),
            'render_mode':mode,'title':meta.get('title'),'subtitle_cues':len(cues),
            'visual':visual,'technical_qualified':technical_qualified,
            'semantic_verified':semantic_verified,
            'user_approved':bool(user_approval),'user_approval':user_approval,
            'issues':list(dict.fromkeys(issues)),'qualified':not issues})
    qualified=[x for x in rows if x['qualified']]
    live=sum(x['render_mode']=='live_video_card' for x in qualified)
    audio=sum(x['render_mode']=='audio_card' for x in qualified)
    return {'mode':'local-offline-audit','cloud_calls':0,'candidates':len(rows),
        'technical_qualified':sum(x['technical_qualified'] for x in rows),
        'qualified':len(qualified),'qualified_live':live,'qualified_audio_card':audio,
        'six_ready':len(qualified)>=6 and live>=5 and audio<=1,'videos':rows}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);parser.add_argument('--out',type=Path)
    args=parser.parse_args();result=audit(args.root);text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.out:args.out.write_text(text,encoding='utf-8')
    print(text)
