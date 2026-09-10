"""Shared presentation v1: semantic captions, native aspect, thumbnail-first covers.

No source-specific coordinates, network requests, or publishing side effects.
"""
import re
from pathlib import Path

VERSION = 2
PROTECTED = ('贵州茅台', '茅台', '五粮液', '片仔癀', '达仁堂', '林园',
             '价值投资者', '长期投资者', '价值投资', '现金流', '人工智能', '机器人', '不可能', '不会',
             '不能', '没有', '不是', '不应该', '不代表', '基础能源', '新能源', '老能源')
UNIT = re.compile(r'(?:\d+(?:\.\d+)?(?:年|月|日|倍|亿|万|元|%|％|个百分点|小时|分钟|秒))+')
CLOSE = '，。！？；：、,.!?;:%％）】》」』”’'


def word_spans(text):
    """Dictionary segmentation plus explicit entity/unit boundary protection."""
    import jieba
    for word in PROTECTED:
        jieba.add_word(word, freq=1000000)
    spans = [(start, end) for _, start, end in jieba.tokenize(text)]
    protected = [m.span() for m in UNIT.finditer(text)]
    for word in PROTECTED:
        protected.extend(m.span() for m in re.finditer(re.escape(word), text))
    boundaries = {0, len(text)} | {b for a, b in spans}
    boundaries = {b for b in boundaries if not any(a < b < z for a, z in protected)}
    boundaries = {b for b in boundaries if b in (0,len(text)) or text[b] not in CLOSE}
    points = sorted(boundaries)
    return list(zip(points, points[1:]))


def wrap_words(text, capacity):
    """Explicit lines only; never fall back to character slicing."""
    tokens = [text[a:b] for a, b in word_spans(text)]
    if any(len(t) > capacity for t in tokens):
        raise ValueError('完整词/数字单位超过字幕行容量，需要扩大字幕区域')
    if len(text) <= capacity:
        return [text]
    choices = [b for a, b in word_spans(text)
               if b < len(text) and b <= capacity and len(text)-b <= capacity]
    if not choices:
        raise ValueError('完整词句无法放入两行；必须先重新分段')
    cut = min(choices, key=lambda n: (abs(n-(len(text)/2)) -
               (2 if text[n-1] in '，；：、' else 0), n))
    return [text[:cut], text[cut:]]


def layout_for(width, height, card=False):
    width, height = int(width), int(height)
    if min(width, height) < 480:
        raise ValueError('成片短边不足480')
    if card:
        if (width, height) != (720, 1280):
            raise ValueError('人物卡画布必须为720×1280')
        mode, region, font = 'audio_card', dict(x=38,y=874,width=644,height=166), 44
    else:
        mode = 'landscape' if width > height*1.15 else ('portrait' if height > width*1.15 else 'square')
        region = dict(x=round(width*.06), y=round(height*(.73 if mode=='portrait' else .72)),
                      width=round(width*.88), height=round(height*.25))
        # Ultrawide interviews must remain readable at phone width too. Cap by
        # short edge so two lines still fit the reserved region.
        font = min(int(min(width,height)*.08),
                   max(28, round(min(width,height)*.055), round(width*.035)))
        font = min(int(min(width,height)*.10), round(font*1.20))
        font = min(font, int((region['height']-16)/(2*1.448)))
    from caption_readability import VERSION as READABILITY_VERSION
    return {'version':VERSION,'mode':mode,'canvas':{'width':width,'height':height},
            'subtitle_region':region,'subtitle_font_px':font,'subtitle_max_lines':2,
            'subtitle_vertical_alignment':'center','subtitle_layout_version':4,
            'readability_version':READABILITY_VERSION,
            'subtitle_style':'light-panel-dark-text','subtitle_font_unit':'visible-glyph-px',
            'subtitle_preferred_max_seconds':6.0,'subtitle_max_seconds':8.0,'subtitle_target_seconds':3.5,
            'word_boundary_policy':'semantic-v1',
            'terminal_punctuation_policy':'no-comma-period',
            'line_capacity':max(8, int((region['width']-32)/(font*1.05)))}


def prepare_captions(entries, layout):
    """Reconnect ASR fragments across nearby cues, then segment on words/pauses.

    Character timestamps interpolate *within each original cue*, preserving pauses
    and limiting drift; no text is paraphrased or discarded. Overlapping source
    cue end times are clipped to the next start time.
    """
    cap = layout['line_capacity']
    if entries and all(e.get('semantic_group') is True for e in entries):
        return [{**e,
                 'lines': wrap_words(e['zh'], e.get('line_capacity', cap)),
                 'font_px': e.get('font_px', layout['subtitle_font_px'])}
                for e in entries]
    entries = sorted(entries, key=lambda e: e['start_sec'])
    groups, group, last_end = [], [], None
    for i, entry in enumerate(entries):
        a, b = float(entry['start_sec']), float(entry['end_sec'])
        if i+1 < len(entries):
            b = min(b, float(entries[i+1]['start_sec']))
        text = re.sub(r'\s+', '', entry.get('zh') or '')
        if not text:
            continue
        if b <= a:
            raise ValueError('字幕时间重叠到零长度，禁止静默丢弃文本')
        # 原版只在较长停顿或上一 cue 末尾已有句末标点时分组。
        # SenseVoice 经常把口语标点省掉，于是多个 ASR cue 会被拼成一个超长
        # 字符流，再按屏幕容量机械切开，出现“邀请到的嘉宾也是我们首席 /
        # 说的新朋友来自0元投资的”这种半句话。现在把 ASR cue 本身也作为
        # 软语义边界：已有 cue 只要持续够可读，就先结束当前组；极短碎片
        # 才允许和下一 cue 合并。
        group_dur = (last_end-group[0][1]) if group and last_end is not None else 0
        unfinished = bool(group and re.search(r'(?:还更|更加|因为|所以|如果|那么|但是|而且|以及|的|把|被|与|比|要|更|还|是)$', ''.join(c[0] for c in group)))
        # An ASR cue is only a soft boundary. Reconnect 茅/台 and numeric units
        # before splitting screens; the 1.15s cutoff must not split a token.
        joined = ''.join(c[0] for c in group) + text
        boundary = len(group)
        split_word = group and any(lo < boundary < hi for lo, hi in word_spans(joined))
        if group and (a-last_end > .45 or group[-1][0] in '。！？!?' or
                      (group_dur >= 1.15 and not split_word and not unfinished)):
            groups.append(group); group=[]
        for k, char in enumerate(text):
            group.append((char, a+(b-a)*k/len(text), a+(b-a)*(k+1)/len(text)))
        last_end=b
    if group:
        groups.append(group)
    result=[]
    for group in groups:
        text=''.join(x[0] for x in group)
        stops=[b for a,b in word_spans(text)]
        start=0
        while start < len(text):
            candidates=[]
            for end in stops:
                if end <= start or end-start > 2*cap:
                    continue
                part=text[start:end]
                longest=max((b-a for a,b in word_spans(part)),default=0)
                cue_cap=max(cap,longest)
                cue_font=layout['subtitle_font_px']
                if longest>cap:
                    # A whole date/unit may exceed the enlarged default size.
                    # Fit that cue only, rather than split the entity or shrink
                    # every subtitle in the video. Never drop below 28px.
                    cue_font=min(cue_font,int((layout['subtitle_region']['width']-32)/(cue_cap*1.05)))
                    if cue_font<28:
                        continue
                try:
                    lines=wrap_words(part,cue_cap)
                except ValueError:
                    continue
                duration=group[end-1][2]-group[start][1]
                if duration <= 6 or not candidates:
                    candidates.append((end,lines,duration,cue_font))
            if not candidates:
                raise ValueError('字幕含无法安全展示的超长完整词')
            usable=[c for c in candidates if c[2] <= 6] or candidates[:1]
            strong=[c for c in usable if text[c[0]-1] in '。！？!?' and c[2]>=.8]
            soft=[c for c in usable if text[c[0]-1] in '，；：,;:' and c[2]>=1.0]
            end,lines,duration,cue_font=(strong[-1] if strong else (soft[-1] if soft else usable[-1]))
            if end<len(text) and group[-1][2]-group[end][1]<.8:
                # Do not leave a flashing "呢？" or a lone sentence ending on
                # the next screen after increasing font size.
                balanced=[c for c in usable if c[0]<end and len(text)-c[0]>=4
                          and group[-1][2]-group[c[0]][1]>=.8]
                if balanced:
                    end,lines,duration,cue_font=balanced[-1]
            if duration < .25:
                # 极短 ASR 碎片不单独闪屏：优先并入上一屏；若是首屏则
                # 扩到下一个完整词边界。保留全部原文，不放宽最终可读性。
                if result and len(result[-1]['zh']) + len(text[start:end]) <= 2*cap:
                    merged=result[-1]['zh']+text[start:end]
                    try:
                        merged_lines=wrap_words(merged,max(cap, max((b-a for a,b in word_spans(merged)),default=cap)))
                        result[-1]['zh']=merged; result[-1]['lines']=merged_lines
                        result[-1]['end_sec']=group[end-1][2]
                        start=end; continue
                    except ValueError:
                        pass
                later=[c for c in usable if c[0]>end and c[2]>=.25]
                if later:
                    end,lines,duration,cue_font=later[0]
                else:
                    raise ValueError('字幕极短碎片无法安全并入相邻完整意群')
            result.append({'start_sec':group[start][1],'end_sec':group[end-1][2],
                           'zh':text[start:end],'en':'','lines':lines,'font_px':cue_font})
            start=end
    if ''.join(e['zh'] for e in result) != ''.join(re.sub(r'\s+','',e.get('zh') or '') for e in entries):
        raise ValueError('字幕重分段改变了原文')
    return result


def write_ass(entries, path, layout, font_name):
    from caption_readability import ass_font_size
    prepared=prepare_captions(entries,layout)
    region=layout['subtitle_region']; font=layout['subtitle_font_px']
    w,h=layout['canvas']['width'],layout['canvas']['height']
    x,y=region['x']+region['width']//2,region['y']+region['height']//2
    def ts(t):
        ticks=round(t*100)
        return f'{ticks//360000}:{ticks//6000%60:02}:{ticks//100%60:02}.{ticks%100:02}'
    color='&H00422C18'
    box_style, box_outline=(1,0) if layout['mode']=='audio_card' else (3,10)
    lines=['[Script Info]','ScriptType: v4.00+','WrapStyle: 2',f'PlayResX: {w}',f'PlayResY: {h}',
           '', '[V4+ Styles]',
           'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
           f'Style: ZH,{font_name},{ass_font_size(font,font_name)},{color},&H000000FF,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,0,0,{box_style},{box_outline},0,5,0,0,0,1',
           '', '[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for cue in prepared:
        # Display-only cleanup: transcript and timing remain intact. Preserve
        # question/exclamation marks, decimals, and punctuation inside a line.
        cue['display_lines'] = [re.sub(r'[，。,．.]+(?=[”’」』）】]*$)', '', t)
                                for t in cue['lines']]
        # Literal braces/backslashes are escaped; only our explicit line breaks are ASS commands.
        rendered='\\N'.join(t.replace('\\','／').replace('{','（').replace('}','）') for t in cue['display_lines'])
        size=ass_font_size(cue['font_px'],font_name)
        lines.append(f'Dialogue: 0,{ts(cue["start_sec"])},{ts(cue["end_sec"])},ZH,,0,0,0,,{{\\an5\\pos({x},{y})\\fs{size}}}{rendered}')
    Path(path).write_text('\n'.join(lines),encoding='utf-8-sig')
    return prepared


def cover_headline(title, speaker='林园'):
    from headline_policy import cover_copy, body, compact
    # A caller may supply already-reviewed cover copy. Rendering must not
    # reinterpret it as a new title and replace its words with a topic label.
    text=body(title,speaker)
    short=text if len(compact(text))<=18 else cover_copy(title, speaker=speaker)['text']
    clauses=[part for part in re.split(r'[，,。；;]',short) if part]
    if len(clauses)==2 and all(len(part)<=9 for part in clauses):
        return clauses
    return wrap_words(''.join(clauses),9)


def select_cover_style(clean_source, title, requested='auto'):
    """Keep real-scene covers for clean footage; stable variety for audio cards."""
    import hashlib
    if requested not in ('auto','photo','light','dark'):
        raise ValueError('封面风格只支持 auto/photo/light/dark')
    if requested == 'photo' and not clean_source:
        raise ValueError('原画未通过清理，不能强制原画封面')
    if requested != 'auto':
        return requested
    # 三套封面统一进入稳定轮换：实景 photo、浅色人物 light、深色人物 dark。
    # 用标题哈希保证同一条重跑不会随机变脸，同时避免主页连续全是同一种模板。
    # photo 仅在原画通过清理时可选；脏原画仍只在 light/dark 中轮换。
    bucket = hashlib.sha256(title.encode()).digest()[0]
    if clean_source:
        return ('photo','light','dark')[bucket % 3]
    return ('light','dark')[bucket % 2]


def dark_cover(portrait_path, title, speaker, font_path, font_index=0):
    """An alternative editorial cover: rectangular real portrait, short quote."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    if not portrait_path or not Path(portrait_path).is_file():
        raise ValueError('深色封面缺少真人参考图')
    image=Image.new('RGB',(1280,720),(20,27,36))
    draw=ImageDraw.Draw(image)
    portrait=ImageOps.fit(Image.open(portrait_path).convert('RGB'),(288,448),
                          method=Image.Resampling.LANCZOS)
    image.paste(portrait,(944,170))
    draw.rectangle((48,172,64,202),fill=(246,186,57))
    tagfont=ImageFont.truetype(font_path,30,index=font_index)
    draw.text((48,75),speaker+' / 观点摘录',font=tagfont,fill=(215,220,226))
    font=ImageFont.truetype(font_path,96,index=font_index)
    lines=cover_headline(title,speaker); boxes=[]
    for i,line in enumerate(lines):
        xy=(48,228+i*134)
        draw.text(xy,line,font=font,fill=(248,249,250) if i==0 else (255,202,70))
        boxes.append(draw.textbbox(xy,line,font=font))
    draw.text((48,640),'人物资料图 · 个人观点仅供交流',font=tagfont,fill=(168,178,192))
    return image,lines,96,boxes


def cover_proof(image, path, lines, font_size, boxes, style=None):
    import json
    from PIL import Image
    if len(lines)>2 or font_size<96 or any(b[0]<0 or b[1]<0 or b[2]>1280 or b[3]>720 for b in boxes):
        raise ValueError('封面大字/边界验收失败')
    thumb=Path(path).with_name(Path(path).stem+'_list_160.jpg')
    image.resize((160,90),Image.Resampling.LANCZOS).save(thumb,quality=95)
    proof={'version':VERSION,'canvas':{'width':1280,'height':720},'headline_lines':lines,
           'font_px':font_size,'thumbnail_font_px':font_size/8,'thumbnail':thumb.name,
           'text_boxes':boxes,'no_overflow':True}
    if style:
        proof['style']=style
    Path(str(path)+'.proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    return proof


def qr_is_plausible(points, width, height):
    import cv2
    import numpy as np
    p=np.asarray(points).reshape(4,2)
    if not np.isfinite(p).all() or (p<0).any() or (p[:,0]>=width).any() or (p[:,1]>=height).any():
        return False
    edges=np.roll(p,-1,axis=0)-p
    lengths=np.linalg.norm(edges,axis=1)
    if min(lengths)<8 or max(lengths)/min(lengths)>1.8:
        return False
    angles=abs((edges*np.roll(edges,-1,axis=0)).sum(axis=1)/(lengths*np.roll(lengths,-1)))
    return bool((angles<.5).all() and cv2.isContourConvex(p.astype('float32')))


def qr_candidate_has_finders(frame, points):
    """An undecoded quad needs QR finder structure, not just four corners.

    OpenCV detects large quads across faces/bookshelves in this interview.
    Preserve undecodable/damaged codes with two surviving finder patterns;
    decoded codes and the producer's separate cropped-finder gate still reject.
    """
    import cv2
    import numpy as np
    p=np.asarray(points,dtype=np.float32).reshape(4,2)
    target=np.array([[0,0],[223,0],[223,223],[0,223]],dtype=np.float32)
    gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY) if frame.ndim==3 else frame
    square=cv2.warpPerspective(gray,cv2.getPerspectiveTransform(p,target),(224,224))
    template=np.zeros((7,7),dtype=np.uint8)
    template[1:6,1:6]=255; template[2:5,2:5]=0
    for modules in range(21,178,4):
        size=max(7,round(224*7/modules))
        edge=min(112,size+max(6,round(size*.25)))
        pattern=cv2.resize(template,(size,size),interpolation=cv2.INTER_NEAREST)
        pattern=cv2.GaussianBlur(pattern,(3,3),.6)
        hits=0
        for corner in (square[:edge,:edge],square[:edge,-edge:],
                       square[-edge:,:edge],square[-edge:,-edge:]):
            _,score,_,point=cv2.minMaxLoc(cv2.matchTemplate(corner,pattern,cv2.TM_CCOEFF_NORMED))
            x,y=point
            hits+=int(score>=.65 and float(corner[y:y+size,x:x+size].std())>=35)
        if hits>=2:
            return True
    return False


def verify_render(path, layout, samples=12):
    """Check actual encoded dimensions and frames, not a hardcoded portrait window."""
    import cv2
    import numpy as np
    cap=cv2.VideoCapture(str(path))
    width,height=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if (width,height)!=(layout['canvas']['width'],layout['canvas']['height']):
        cap.release(); raise ValueError('编码尺寸与版式证明不符')
    count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    detector=cv2.QRCodeDetector(); black=0; checked=0
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES,int(count*(i+.5)/samples))
        ok,frame=cap.read()
        if not ok:
            cap.release(); raise ValueError('成片复检抽帧不足')
        checked+=1
        # In the generated card, source pixels exist only in this window.
        # Detecting across title glyphs + source pixels creates false QR quads
        # spanning unrelated layers (observed in actual final_3 on 2026-09-06).
        # Keep full-frame detection for native footage, and retain the separate
        # partial-finder checks on the moving source window in the producer.
        qr_frame=frame[360:830,44:676] if layout['mode']=='audio_card' else frame
        qh,qw=qr_frame.shape[:2]
        scaled=cv2.resize(qr_frame,(round(qw*min(1,960/qw)),round(qh*min(1,960/qw))))
        found,points=detector.detect(scaled)
        text=''
        if found and points is not None:
            # OpenCV can return a collinear false candidate, then crash during
            # decode. A zero-area quad cannot be a QR image; validate first.
            quad=np.asarray(points,dtype=np.float32).reshape(4,2)
            if not np.isfinite(quad).all() or abs(cv2.contourArea(quad))==0:
                points=None
            else:
                text,_=detector.decode(scaled,points)
        plausible=points is not None and qr_is_plausible(points,scaled.shape[1],scaled.shape[0])
        finder_match=plausible and qr_candidate_has_finders(scaled,points)
        if plausible:
            # Keep the actual candidate for review even when it lacks QR structure.
            import json
            proof=Path(path).parent/'_tmp'/'qr_review'; proof.mkdir(parents=True,exist_ok=True)
            name=f'{Path(path).stem}-frame-{i}'
            cv2.imwrite(str(proof/(name+'.jpg')),scaled)
            (proof/(name+'.json')).write_text(json.dumps(dict(points=points.tolist(),
                decoded=bool(text),finder_match=bool(finder_match))))
        if text or finder_match:
            cap.release(); raise ValueError(f'成片第{i}个抽检帧存在二维码候选')
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        # Uniform near-black outer borders, not naturally dark image content.
        edges=[gray[:max(2,height//40),:],gray[-max(2,height//40):,:],
               gray[:,:max(2,width//40)],gray[:,-max(2,width//40):]]
        black+=int(any(float(e.mean())<5 and float(e.std())<2 for e in edges))
    cap.release()
    if black>=max(2,samples//2):
        raise ValueError('成片存在持续黑色填充边')
    return {'live_region_verified':True,'no_qr_verified':True,'no_black_bars_verified':True,
            'render_checks':{'version':VERSION,'frames_checked':checked,
                             'dimensions_match':True,'qr_detected':False,'black_edge_hits':black,
                             'qr_scope':'source_window' if layout['mode']=='audio_card' else 'full_frame'}}
