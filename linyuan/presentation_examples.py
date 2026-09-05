"""Offline, non-publishing multi-layout regression using two supplied sources.

Source-specific crops below are TEST FIXTURES ONLY, never production selection.
Portrait/square fixtures are derived from the interview, not native-camera claims.
"""
import argparse
import json
import os
import subprocess
from pathlib import Path

import presentation as V
import produce_cn as P


def run(*args):
    subprocess.run([str(a) for a in args],check=True)


def build(args):
    if getattr(P, 'PRESENTATION_RULES_VERSION', 0) != V.VERSION:
        raise RuntimeError('通用规则尚未激活；禁止拿旧渲染器结果冒充新版样例')
    out=Path(args.out); out.mkdir(parents=True,exist_ok=True)
    P.SENSEVOICE_DIR=args.model
    native=out/'interview-source-30s.mp4'
    run('ffmpeg','-y','-loglevel','error','-ss','340','-i',args.interview,
        '-t','30','-c','copy',native)
    work=out/'asr'; work.mkdir(exist_ok=True)
    raw=P.transcribe(native,work)
    entries=[dict(start_sec=c['start'],end_sec=c['end'],zh=c['text'],en='') for c in raw]
    # These coordinates belong only to this reproducible fixture source. The
    # shared renderer gets already prepared landscape/portrait/square inputs.
    clean='delogo=x=1270:y=48:w=360:h=105,delogo=x=865:y=635:w=195:h=36'
    profiles=[('landscape',1640,620,clean+',crop=1640:620:0:48'),
              ('portrait-derived',480,620,clean+',crop=480:620:1140:48'),
              ('square-derived',620,620,clean+',crop=620:620:1000:48')]
    proof=[]
    for name,w,h,vf in profiles:
        layout=V.layout_for(w,h)
        ass=out/(name+'.ass')
        captions=V.write_ass(entries,ass,layout,os.environ.get('ZH_FONT','Noto Sans CJK SC'))
        final=out/(name+'-30s.mp4')
        run('ffmpeg','-y','-loglevel','error','-i',native,'-vf',vf+',setsar=1,ass='+str(ass),
            '-c:v','libx264','-preset','fast','-crf','20','-c:a','aac','-t','30',final)
        checks=V.verify_render(final,layout)
        _,sheet=P.make_review_assets(final,out,'-'+name,30)
        proof.append(dict(file=final.name,layout=layout,checks=checks,captions=captions,
                          contact_sheet=sheet,derived=name!='landscape'))
    # Replay the exact ASR split reported by the user, with its original audio.
    start,end=304.0,334.0
    raw=json.loads(Path(args.cues).read_text())
    cues=[dict(start_sec=max(0,c['start']-start),end_sec=min(30,c['end']-start),
               zh=c['text'],en='') for c in raw if c['end']>start and c['start']<end]
    layout=V.layout_for(720,1280,True)
    ass=out/'audio-card.ass'
    captions=V.write_ass(cues,ass,layout,os.environ.get('ZH_FONT','Noto Sans CJK SC'))
    title='林园：电视机整天在降价，酒在涨价，两个相反的方向'
    card=out/'audio-card.png'
    P.make_audio_card(card,'林园',title,portrait_path=Path(args.portrait),require_portrait=True)
    final=out/'audio-card-30s.mp4'
    run('ffmpeg','-y','-loglevel','error','-loop','1','-i',card,
        '-ss',start,'-i',args.tv,'-map','0:v','-map','1:a',
        '-vf','ass='+str(ass),'-c:v','libx264','-preset','fast','-crf','20',
        '-pix_fmt','yuv420p','-c:a','aac','-t','30','-shortest',final)
    checks=V.verify_render(final,layout)
    _,sheet=P.make_review_assets(final,out,'-audio-card',30)
    proof.append(dict(file=final.name,layout=layout,checks=checks,captions=captions,contact_sheet=sheet))
    P.make_audio_card(out/'cover.jpg','林园',title,width=1280,height=720,
                      portrait_path=Path(args.portrait),require_portrait=True)
    for style in ('light','dark'):
        P.make_audio_card(out/('cover-'+style+'.jpg'),'林园',title,width=1280,height=720,
                         portrait_path=Path(args.portrait),require_portrait=True,cover_style=style)
    # Native-photo cover uses the same shortened headline at >=104px.
    P.make_cover(native,2,20,'林园：投资要看供需关系，长期持有好企业',
                 '林园',out/'native-cover.jpg',preferred_time=5,
                 video_filter=clean+',crop=480:620:1140:48')
    (out/'verification.json').write_text(json.dumps({'presentation_version':1,
        'test_only':True,'no_publication':True,'samples':proof,
        'sources':[args.interview,args.tv]},ensure_ascii=False,indent=2))
    print(json.dumps({'samples':len(proof),'output':str(out)},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    for name in ('interview','tv','cues','portrait','model','out'):
        p.add_argument('--'+name,required=True)
    build(p.parse_args())
