"""Shared presentation v1: semantic captions, native aspect, thumbnail-first covers.

No source-specific coordinates, network requests, or publishing side effects.
"""
import re
from pathlib import Path

VERSION = 1
PROTECTED = ('贵州茅台', '茅台', '五粮液', '片仔癀', '达仁堂', '林园',
             '价值投资者', '长期投资者', '价值投资', '现金流', '人工智能', '机器人', '不可能', '不会',
             '不能', '没有', '不是', '不应该')
UNIT = re.compile(r'(?:\d+(?:\.\d+)?(?:年|月|日|倍|亿|万|元|%|％|个百分点|小时|分钟|秒))+')
CLOSE = '，。！？；：、,.!?;:%％）】》」』'


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
        mode, region, font = 'audio_card', dict(x=38,y=874,width=644,height=166), 40
    else:
        mode = 'landscape' if width > height*1.15 else ('portrait' if height > width*1.15 else 'square')
        region = dict(x=round(width*.06), y=round(height*(.77 if mode=='portrait' else .76)),
                      width=round(width*.88), height=round(height*.17))
        # Ultrawide interviews must remain readable at phone width too. Cap by
        # short edge so two lines still fit the reserved region.
        font = min(int(min(width,height)*.08),
                   max(28, round(min(width,height)*.055), round(width*.035)))
    return {'version':VERSION,'mode':mode,'canvas':{'width':width,'height':height},
            'subtitle_region':region,'subtitle_font_px':font,'subtitle_max_lines':2,
            'subtitle_vertical_alignment':'center','subtitle_layout_version':3,
            'word_boundary_policy':'semantic-v1',
            'line_capacity':max(8, int((region['width']-32)/(font*1.05)))}


def prepare_captions(entries, layout):
    """Reconnect ASR fragments across nearby cues, then segment on words/pauses.

    Character timestamps interpolate *within each original cue*, preserving pauses
    and limiting drift; no text is paraphrased or discarded. Overlapping source
    cue end times are clipped to the next start time.
    """
    cap = layout['line_capacity']
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
        if group and (a-last_end > .65 or (group[-1][0] in '。！？!?' and last_end-group[0][1]>=1)):
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
                try:
                    lines=wrap_words(text[start:end],cap)
                except ValueError:
                    continue
                duration=group[end-1][2]-group[start][1]
                if duration <= 6 or not candidates:
                    candidates.append((end,lines,duration))
            if not candidates:
                raise ValueError('字幕含无法安全展示的超长完整词')
            usable=[c for c in candidates if c[2] <= 6] or candidates[:1]
            punct=[c for c in usable if text[c[0]-1] in '，。！？；,!?;' and c[2]>=1.5]
            end,lines,duration=(punct[-1] if punct else usable[-1])
            if duration < .25:
                raise ValueError('字幕出现不足0.25秒的闪屏，需修复转写时间')
            result.append({'start_sec':group[start][1],'end_sec':group[end-1][2],
                           'zh':text[start:end],'en':'','lines':lines})
            start=end
    if ''.join(e['zh'] for e in result) != ''.join(re.sub(r'\s+','',e.get('zh') or '') for e in entries):
        raise ValueError('字幕重分段改变了原文')
    return result


def write_ass(entries, path, layout, font_name):
    prepared=prepare_captions(entries,layout)
    region=layout['subtitle_region']; font=layout['subtitle_font_px']
    w,h=layout['canvas']['width'],layout['canvas']['height']
    x,y=region['x']+region['width']//2,region['y']+region['height']//2
    def ts(t):
        ticks=round(t*100)
        return f'{ticks//360000}:{ticks//6000%60:02}:{ticks//100%60:02}.{ticks%100:02}'
    color='&H0000D7FF' if layout['mode']=='audio_card' else '&H00FFFFFF'
    lines=['[Script Info]','ScriptType: v4.00+','WrapStyle: 2',f'PlayResX: {w}',f'PlayResY: {h}',
           '', '[V4+ Styles]',
           'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
           f'Style: ZH,{font_name},{font},{color},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,5,0,0,0,1',
           '', '[Events]','Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text']
    for cue in prepared:
        # Literal braces/backslashes are escaped; only our explicit line breaks are ASS commands.
        rendered='\\N'.join(t.replace('\\','／').replace('{','（').replace('}','）') for t in cue['lines'])
        lines.append(f'Dialogue: 0,{ts(cue["start_sec"])},{ts(cue["end_sec"])},ZH,,0,0,0,,{{\\an5\\pos({x},{y})\\fs{font}}}{rendered}')
    Path(path).write_text('\n'.join(lines),encoding='utf-8-sig')
    return prepared


def cover_headline(title, speaker='林园'):
    body=re.sub(rf'^(?:股神)?{re.escape(speaker)}[：:]\s*','',title).strip()
    body=re.sub(r'【重制试看】|\s+','',body)
    clauses=[s.strip('，。！？；：,!?; ') for s in re.split(r'[，。！？；,!?;]',body)]
    clauses=[c for c in clauses if c]
    if len(clauses)>=2 and all(len(x)<=9 for x in clauses[:2]):
        return clauses[:2]
    if clauses and len(clauses[0])<=18:
        try:
            return wrap_words(clauses[0],9)
        except ValueError:
            pass
    # Explicit quotation excerpt, never silently truncate or invent a new claim.
    ends=[b for a,b in word_spans(body) if 6<=b<=16 and body[b-1] not in CLOSE]
    for end in reversed(ends):
        try:
            return wrap_words(body[:end]+'…',9)
        except ValueError:
            pass
    raise ValueError('无法生成完整词边界的短封面标题，需重写封面文案')


def cover_proof(image, path, lines, font_size, boxes):
    import json
    from PIL import Image
    if len(lines)>2 or font_size<96 or any(b[0]<0 or b[1]<0 or b[2]>1280 or b[3]>720 for b in boxes):
        raise ValueError('封面大字/边界验收失败')
    thumb=Path(path).with_name(Path(path).stem+'_list_160.jpg')
    image.resize((160,90),Image.Resampling.LANCZOS).save(thumb,quality=95)
    proof={'version':VERSION,'canvas':{'width':1280,'height':720},'headline_lines':lines,
           'font_px':font_size,'thumbnail_font_px':font_size/8,'thumbnail':thumb.name,
           'text_boxes':boxes,'no_overflow':True}
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
        scaled=cv2.resize(frame,(round(width*min(1,960/width)),round(height*min(1,960/width))))
        text,points,_=detector.detectAndDecode(scaled)
        if text or (points is not None and qr_is_plausible(points,scaled.shape[1],scaled.shape[0])):
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
                             'dimensions_match':True,'qr_detected':False,'black_edge_hits':black}}
