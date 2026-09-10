"""Replace intrusive source typography with short, source-quoted topic cards.

Audio and timeline remain continuous. Real participant footage must still cover
at least 70%; source illustrations and editorial cards are counted separately.
"""
import json
from pathlib import Path
import subprocess
import cv2
# Reviewed against the exact source file, including 0.25s boundary contact sheets.
# These ranges describe visible source overlays, never a bypass of identity gates.
GRAPHICS = {
    '67ed2a6894ef419b44607632623c02153fc724907c233a20e33629e7125cd42e': {
        'reviewed_start': 329.4, 'reviewed_end': 456.6,
        'illustration_range': (398.0,418.0),
        'spans': [(336.1,340.2,'消费升级'),(352.0,355.4,'品牌消费'),
                  (359.5,361.8,'财务思维'),(390.9,393.0,'消费降级'),
                  (428.9,431.0,'价格更低')],
    }
}


def topic_card(path,text,comparison=False):
    from PIL import Image,ImageDraw,ImageFont
    fonts=[Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
           Path('/usr/local/share/fonts/noto-cjk/NotoSansCJKsc-Regular.otf')]
    font_path=next((p for p in fonts if p.is_file()),None)
    if font_path is None:raise ValueError('关键词卡缺少中文字体')
    im=Image.new('RGB',(632,470),(250,250,250));d=ImageDraw.Draw(im)
    if comparison:
        title=ImageFont.truetype(str(font_path),34)
        label=ImageFont.truetype(str(font_path),28)
        value=ImageFont.truetype(str(font_path),40)
        d.text((316,48),'访谈中的财务对比',font=title,fill=(35,48,64),anchor='mm')
        d.line((36,98,596,98),fill=(202,161,68),width=3)
        for x,year,price,profit in [(305,'2007年','199元','28亿元'),(503,'2013年','113元','151亿元')]:
            d.text((x,140),year,font=label,fill=(35,48,64),anchor='mm')
            d.text((x,226),price,font=value,fill=(35,48,64),anchor='mm')
            d.text((x,322),profit,font=value,fill=(35,48,64),anchor='mm')
        d.text((36,226),'收盘价',font=label,fill=(110,116,123),anchor='lm')
        d.text((36,322),'净利润',font=label,fill=(110,116,123),anchor='lm')
        small=ImageFont.truetype(str(font_path),22)
        d.text((316,419),'数据为原访谈中的表述',font=small,fill=(110,116,123),anchor='mm')
        im.save(path)
        return
    font=ImageFont.truetype(str(font_path),80);small=ImageFont.truetype(str(font_path),24)
    d.rounded_rectangle((280,115,352,122),3,fill=(202,161,68))
    d.text((316,225),text,font=font,fill=(35,48,64),anchor='mm')
    d.text((316,335),'林园 · 访谈原声',font=small,fill=(110,116,123),anchor='mm')
    im.save(path)


def clean_interview_graphics(video,proof,cues,source_sha256,work):
    profile=GRAPHICS.get(source_sha256)
    if not profile:return proof
    start=proof['source_start'];end=start+proof['duration']
    if start<profile['reviewed_start']-.05 or end>profile['reviewed_end']+.05:
        raise ValueError('同源访谈选段超出已复检画面范围，须重新检查原片大字')
    work=Path(work);work.mkdir(parents=True,exist_ok=True);video=Path(video)
    cap=cv2.VideoCapture(str(video));fps=cap.get(cv2.CAP_PROP_FPS)
    count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));cap.release()
    if count!=proof['frames'] or fps<=0:raise ValueError('原片大字清理帧数不匹配')
    def role(n):
        return next(r['role'] for r in proof['roles'] if r['start_frame']<=n<r['end_frame'])
    replaced=set()
    for a,b,_ in profile['spans']:
        replaced.update(range(max(0,int((a-start)*fps)),min(count,int((b-start)*fps+.999))))
    replaced_illustrations=sum(role(n)=='source_illustration' for n in replaced)
    spans=[]
    for n in sorted(replaced):
        if spans and spans[-1][1]==n:spans[-1][1]=n+1
        else:spans.append([n,n+1])
    roles=[]
    for n in range(count):
        label='editorial_card' if n in replaced else role(n)
        if roles and roles[-1]['role']==label:roles[-1]['end_frame']=n+1
        else:roles.append(dict(role=label,start_frame=n,end_frame=n+1))
    counts={k:sum(r['end_frame']-r['start_frame'] for r in roles if r['role']==k)
            for k in ('guest','participant','source_illustration','editorial_card')}
    if (counts['guest']+counts['participant'])/count<.7:
        raise ValueError('原片遮挡过多，清理后真人动态不足70%，须换选段')
    choices=[n for a,b in proof['target_reference_spans'] for n in range(a,b) if n not in replaced]
    if len(choices)<6:raise ValueError('清理后缺少六帧独立嘉宾身份验证证据')
    cards=[]
    for k,(a,b) in enumerate(spans):
        nearby=''.join(c['zh'] for c in cues if c['end_sec']>=a/fps-6 and c['start_sec']<=b/fps+3)
        topic=next(t for x,y,t in profile['spans'] if x-start<=a/fps+.05<y-start)
        if topic not in nearby:raise ValueError('关键词卡原话与当前字幕不一致')
        path=work/f'card-{k}.png';topic_card(path,topic)
        cards.append(dict(start_frame=a,end_frame=b,text=topic,path=str(path)))
    illustrations=[]
    for row in roles:
        a,b=profile.get('illustration_range',(0,0))
        if row['role']=='source_illustration' and a<=start+row['start_frame']/fps<b:
            illustrations.append(dict(start_frame=row['start_frame'],end_frame=row['end_frame']))
    if illustrations:
        transcript=''.join(c['zh'] for c in cues)
        for chinese,digits in [('一百九十九','199'),('一百一十三','113'),('二十八','28'),('一百五十一','151')]:
            if chinese not in transcript and digits not in transcript:
                raise ValueError('财务对比卡缺少对应的访谈原话数据')
        path=work/'comparison.png';topic_card(path,'',comparison=True)
        for row in illustrations:
            row.update(path=str(path),kind='source_quoted_financial_comparison',
                       values={'2007':{'price_yuan':199,'profit_yi_yuan':28},
                               '2013':{'price_yuan':113,'profit_yi_yuan':151}})
    overlays=cards+illustrations
    if overlays:
        result=video.with_name(video.stem+'-clean.mp4')
        cmd=['ffmpeg','-y','-loglevel','error','-i',str(video)]
        for card in overlays:cmd+=['-loop','1','-framerate',str(fps),'-i',card['path']]
        filters=[];previous='0:v'
        for i,card in enumerate(overlays,1):
            filters.append(f"[{previous}][{i}:v]overlay=0:0:enable='gte(t,{card['start_frame']/fps:.9f})*lt(t,{card['end_frame']/fps:.9f})'[v{i}]")
            previous=f'v{i}'
        cmd+=['-filter_complex',';'.join(filters),'-map',f'[{previous}]','-an','-frames:v',str(count),
              '-c:v','libx264','-preset','veryfast','-crf','18','-pix_fmt','yuv420p',str(result)]
        subprocess.run(cmd,check=True,capture_output=True,timeout=180)
        check=cv2.VideoCapture(str(result));actual=int(check.get(cv2.CAP_PROP_FRAME_COUNT));actual_fps=check.get(cv2.CAP_PROP_FPS);check.release()
        if abs(actual_fps-fps)>.001:raise ValueError('关键词卡处理改变了视频帧率')
        if actual!=count:raise ValueError('关键词卡处理改变了视频帧数')
        result.replace(video)
    out={**proof,'mode':'verified_interview_context_v2','roles':roles,
        'matched_frames':counts['guest'],'other_face_frames':counts['participant'],
        'editorial_card_frames':counts['editorial_card'],
        'source_timeline_preserved':True,'source_frames_preserved':not bool(overlays),
        'verified_face_ratio':(counts['guest']+counts['participant'])/count,
        'matched_ratio':counts['guest']/count,'graphics_profile_source_sha256':source_sha256,
        'editorial_cards':cards,
        'context_picture_frames':counts['source_illustration'],
        'replaced_source_illustration_frames':replaced_illustrations,
        'reformatted_illustration_frames':sum(r['end_frame']-r['start_frame'] for r in illustrations),
        'illustration_cards':illustrations,
        'target_sample_times':[(choices[min(len(choices)-1,int(len(choices)*(i+.5)/6))]+.5)/fps for i in range(6)]}
    video.with_suffix('.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
    print(f"[原片大字清理] {len(cards)}段关键词卡，共{len(replaced)/fps:.2f}秒；已核验真人动态{out['verified_face_ratio']:.1%}",flush=True)
    return out
