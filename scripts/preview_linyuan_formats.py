#!/usr/bin/env python3
"""Render 16 review-only layout trials from one fingerprinted own source.

The manually inspected crop is specific to this 12.2 s source excerpt, never a
production framing rule. Reference-account images are not used in the outputs.
"""
import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
from editorial_cover import font_path, font_face_index
from presentation import word_spans

SOURCE_SHA='4c711fb59cdec677f1eacc7f6603f27d5b4231f6ab841fc4d421a0079b5d2846'
START=2489.48
DURATION=12.2
# These are preview concepts based on this excerpt, not auto-approved title proofs.
COPY=[('医药股也有经营不好的','医药股也有经营不好的，别乱买'),
      ('什么生意都有不好的','什么生意都有不好的，医药股也一样'),
      ('别买经营不好的医药股','医药股也有经营不好的，你别买错了'),
      ('先看公司经营得好不好','买医药股，先看经营好不好')]
STYLES=[
 ('p01','portrait','黑底观点条', 'black','window'),
 ('p02','portrait','原话两句','black','quote'),
 ('p03','portrait','暖白访谈','cream','window'),
 ('p04','portrait','放大真人','navy','large'),
 ('p05','portrait','上下问答','black','context'),
 ('p06','portrait','单句留白','cream','large'),
 ('p07','portrait','人物音频卡','cream','still'),
 ('p08','portrait','现场与人物拼图','black','collage'),
 ('l01','landscape','近景原画','black','native'),
 ('l02','landscape','下方字幕带','navy','footer'),
 ('l03','landscape','暖白字幕带','cream','footer'),
 ('l04','landscape','上方原话条','black','header'),
 ('l05','landscape','左右观点与真人','navy','split'),
 ('l06','landscape','保留两人语境','black','context'),
 ('l07','landscape','现场配短句','cream','split'),
 ('l08','landscape','原话双行开场','black','double_quote'),
]


@lru_cache(maxsize=80)
def font(size):
    path=font_path()
    return ImageFont.truetype(str(path),size,index=font_face_index(path))

def text_block(im,text,box,size,color,outline=0,align='center'):
    d=ImageDraw.Draw(im);x,y,w,h=box
    for px in range(size,21,-2):
        f=font(px);lines=[]
        for paragraph in text.split('\n'):
            line=''
            for a,b in word_spans(paragraph):
                token=paragraph[a:b]
                if line and d.textlength(line+token,font=f)>w:lines.append(line);line=token
                else:line+=token
            if line:lines.append(line)
        if len(lines)*(px*1.38)<=h:break
    if len(lines)*(px*1.38)>h:raise ValueError('preview text overflow')
    for n,line in enumerate(lines):
        xx=x+(w-d.textlength(line,font=f))/2 if align=='center' else x
        d.text((xx,y+n*px*1.38),line,font=f,fill=color,stroke_width=outline,stroke_fill='black')


def fit(im,picture,box):
    x,y,w,h=box;pic=ImageOps.contain(picture,(w,h),Image.Resampling.LANCZOS)
    im.paste(pic,(x+(w-pic.width)//2,y+(h-pic.height)//2))


def render(frame,style,copy_index,caption,still,illustration=None):
    key,aspect,label,theme,kind=style
    size=(720,1280) if aspect=='portrait' else (1280,720)
    bg={'black':'#090909','cream':'#eee9df','navy':'#111d2c'}[theme]
    ink='#18202a' if theme=='cream' else '#ffffff';accent='#a82324' if theme=='cream' else '#ffe54c'
    im=Image.new('RGB',size,bg)
    # Inspected own-source guest window; excludes the source's hard subtitle row.
    guest=frame.crop((135,270,720,634) if kind=='large' else (26,175,855,634));heading=COPY[copy_index][0]
    if aspect=='portrait':
        text_block(im,heading,(38,130,644,170),58,accent)
        if kind=='quote':text_block(im,'别买经营不好的医药股',(40,270,640,95),35,ink)
        if kind=='context':fit(im,frame.crop((0,160,1280,634)),(0,345,720,500))
        elif kind=='collage':
            fit(im,still.crop((26,175,855,634)),(38,340,644,290))
            fit(im,frame.crop((0,160,1280,634)),(38,650,644,240))
        elif kind=='still':
            if illustration is not None:fit(im,illustration,(100,315,520,600))
            else:fit(im,still.crop((26,175,855,634)),(30,385,660,470))
        else:fit(im,guest,(0,330 if kind=='large' else 405,720,570 if kind=='large' else 405))
        text_block(im,caption,(35,945,650,185),43,ink,0 if theme=='cream' else 2)
        if kind=='still':text_block(im,'AI人物插画 · 原声片段' if illustration is not None else '人物资料画面 · 原声片段',(40,1150,640,60),23,ink)
    else:
        if kind=='split':
            fit(im,guest,(475,40,785,565));text_block(im,heading,(32,190,405,270),60,accent)
        elif kind=='context':fit(im,frame.crop((0,160,1280,634)),(0,0,1280,570))
        elif kind=='double_quote':
            text_block(im,'医药股也有经营不好的',(40,8,1200,64),48,'#ffe54c')
            text_block(im,'什么生意都有不好的',(40,73,1200,56),39,'#ffffff')
            fit(im,guest,(0,150,1280,420))
        elif kind=='header':
            text_block(im,heading,(30,16,1220,105),54,accent);fit(im,guest,(0,130,1280,460))
        elif kind=='footer':fit(im,guest,(0,0,1280,570))
        else:fit(im,guest,(0,0,1280,680))
        text_block(im,caption,(65,596,1150,115),43,ink,0 if theme=='cream' else 2)
    # Visible test label avoids confusing simulated crops with published output.
    text_block(im,'园来滚雪球 · 样式试片',(size[0]-315,25,285,45),24,ink)
    return im


def caption_at(t):
    if t<5.6:return '医药股里边也有很多经营不好的\n什么生意都有不好的'
    return '你不要去买了个经营不好的'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out',type=Path,default=ROOT/'output/benchmark-20260921')
    ap.add_argument('--source',type=Path,default=ROOT/'output/optimization-v1/production-samples/mother/video.mp4')
    ap.add_argument('--ffmpeg');ap.add_argument('--only',nargs='*');a=ap.parse_args()
    if a.ffmpeg:ffmpeg=a.ffmpeg
    else:
        import imageio_ffmpeg
        ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    if hashlib.sha256(a.source.read_bytes()).hexdigest()!=SOURCE_SHA:raise ValueError('source fingerprint mismatch')
    out=a.out/'variants';out.mkdir(parents=True,exist_ok=True);clip=out/'source-excerpt.mp4'
    subprocess.run([ffmpeg,'-y','-loglevel','error','-ss',str(START),'-i',str(a.source),'-t',str(DURATION),'-vf','fps=15','-c:v','libx264','-preset','ultrafast','-crf','18','-c:a','aac','-b:a','128k',str(clip)],check=True,timeout=120)
    cap=cv2.VideoCapture(str(clip));ok,f=cap.read();cap.release()
    if not ok:raise ValueError('sample decode failed')
    still=Image.fromarray(cv2.cvtColor(f,cv2.COLOR_BGR2RGB));results=[]
    if a.only and (out/'manifest.json').exists():results=json.loads((out/'manifest.json').read_text())
    art=a.out/'assets/linyuan-illustration-v1.png'
    illustration=Image.open(art).convert('RGB') if art.exists() else None
    for n,style in enumerate(STYLES):
        key,aspect,label,theme,kind=style
        if a.only and key not in a.only:continue
        size=(720,1280) if aspect=='portrait' else (1280,720)
        target=out/(key+'.mp4');cap=cv2.VideoCapture(str(clip));count=0
        cmd=[ffmpeg,'-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{size[0]}x{size[1]}','-r','15','-i','-','-i',str(clip),'-map','0:v','-map','1:a:0','-t',str(DURATION),'-c:v','libx264','-preset','veryfast','-crf','23','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-movflags','+faststart',str(target)]
        with (out/(key+'.log')).open('wb') as log:
            proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=log)
            try:
                while True:
                    ok,raw=cap.read()
                    if not ok:break
                    frame=Image.fromarray(cv2.cvtColor(raw,cv2.COLOR_BGR2RGB));im=render(frame,style,n%4,caption_at(count/15),still,illustration)
                    if count==30:im.save(out/(key+'.jpg'))
                    proc.stdin.write(im.tobytes());count+=1
            finally:
                cap.release();proc.stdin.close()
            if proc.wait(timeout=120)!=0:raise RuntimeError(f'render failed: {key}')
        proof=dict(id=key,aspect=aspect,label=label,theme=theme,kind=kind,
                   title='林园：'+COPY[n%4][1],cover_text=COPY[n%4][0],duration=count/15,
                   dimensions=list(size),source_sha256=SOURCE_SHA,source_start=START,
                   review_only=True,publication_authorized=False,
                   note='固定12.2秒排版试片；字幕合并原断行并省去口头填充，保留观点；人物裁切为此片段人工检查坐标。')
        if kind=='still':proof['illustration']='assets/linyuan-illustration-v1.png' if illustration is not None else None
        results=[r for r in results if r['id']!=key]+[proof];results.sort(key=lambda r:next(i for i,s in enumerate(STYLES) if s[0]==r['id']))
        (out/'manifest.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
        print(key,label,count,flush=True)
    # Cover trials are distinct from the video frame/title treatment.
    portrait=still.crop((26,175,855,634));cover=ImageOps.fit(portrait,(1280,720))
    for idx in range(4):
        im=cover.copy()
        if idx==1:text_block(im,'医药股也有\n经营不好的',(38,40,500,240),66,'#ffe54c',3,align='left')
        if idx==2:
            im=Image.new('RGB',(1280,720),'#eee9df');fit(im,portrait,(600,50,650,610));text_block(im,'什么生意\n都有不好的',(40,180,530,300),68,'#a82324',align='left')
        if idx==3:
            im=Image.new('RGB',(1280,720),'#111d2c');fit(im,portrait,(420,0,860,620));text_block(im,'别买经营不好的医药股',(35,595,1210,120),61,'#ffe54c')
        im.save(out/f'cover-{idx}.jpg')
        thumb=im.resize((160,90),Image.Resampling.LANCZOS);thumb.save(out/f'cover-{idx}-160.jpg')

if __name__=='__main__':main()
