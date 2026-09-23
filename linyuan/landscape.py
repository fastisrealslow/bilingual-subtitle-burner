"""Landscape presentation of a verified moving window; never stretch a portrait card."""
import os
import re
import subprocess
from pathlib import Path

CANVAS = (1280, 720)
BRAND_WIDTH = 180
# Keep the 632:470 clean window's aspect ratio to within one encoded pixel.
# Final source-text inspection scans the whole live window. V2 placed our
# generated captions inside that window; all six cc5113b landscape attempts
# were then mistaken for dirty source footage. Keep every source-pixel check
# unchanged and place our captions in a separate footer.
LIVE_REGION = dict(x=270, y=0, width=740, height=550)


def source_window(meta):
    # Timeline review found moving yellow captions reaching the speaker's chin.
    # A tighter crop cannot remove these without cutting the face.
    if meta.get('fingerprints',{}).get('sha256')=='a0a1a9c3674e4620ad36595fde0b17abca69ddb44e17376a1734d25d76d302ec':
        raise ValueError('原片动态黄色大字遮挡人物，不能以裁切冒充干净横版')
    spec=meta.get('layout_proof',{})
    if spec.get('canvas')==dict(width=1280,height=720):
        # Read older quiet outputs only when both recorded regions match the
        # previous renderer exactly; never guess a crop for unknown layouts.
        if spec.get('template')=='landscape-live-v4-quiet-footer':
            if (spec.get('live_region')!=dict(x=237,y=0,width=806,height=600)
                    or spec.get('subtitle_region')!=dict(x=180,y=600,width=920,height=120)):
                raise ValueError('旧横版输入的真人窗口或字幕区域已变化')
            return dict(spec['live_region'])
        styles={layout('classic')['template']:'classic',layout('quiet')['template']:'quiet'}
        style=styles.get(spec.get('template'))
        if (not style or spec.get('live_region')!=layout(style)['live_region']
                or spec.get('subtitle_region')!=layout(style)['subtitle_region']):
            raise ValueError('横版输入的真人窗口或字幕区域不是已知的独立区域')
        return dict(spec['live_region'])
    return dict(x=44,y=360,width=632,height=470)


def selected(meta, requested='auto'):
    if requested not in {'auto', 'portrait', 'landscape'}:
        raise ValueError('未知横竖屏选择')
    if meta.get('render_mode') != 'live_video_card':
        return False  # Native footage is framed before subtitles; audio cards stay portrait.
    if requested == 'auto' and len(meta.get('subtitle_files', [])) != 1:
        return False
    if requested != 'auto':
        return requested == 'landscape'
    # Format follows verified picture geometry, not a source-hash lottery.
    # This generated portrait template holds a horizontal 632x470 window.
    # Native portrait/stage footage and unknown layouts keep their own frame.
    spec = meta.get('layout_proof') or {}
    return (meta.get('audio_card_template') == 'live_editorial_v4_readable'
            and spec.get('canvas') == dict(width=720, height=1280)
            and spec.get('live_region', dict(x=44,y=360,width=632,height=470))
                == dict(x=44,y=360,width=632,height=470))


def style_name(value=None):
    value=value or os.environ.get('LINYUAN_LANDSCAPE_STYLE','quiet')
    if value not in ('classic','quiet'):raise ValueError('未知横版画面样式')
    return value


def layout(style=None):
    from presentation import layout_for
    style=style_name(style)
    result = layout_for(*CANVAS)
    result.update(live_region=dict(LIVE_REGION), subtitle_region=dict(x=200,y=570,width=880,height=138),
                  subtitle_font_px=44, line_capacity=18, subtitle_style='white-outline',
                  template='landscape-live-v3-footer', preserve_display_text=True)
    if style=='quiet':
        # Full-video trials retain 44 visible caption pixels and two lines
        # outside the 584px picture. Keep every source pixel in the scan.
        result.update(live_region=dict(x=248,y=0,width=784,height=584),
                      subtitle_region=dict(x=180,y=584,width=920,height=136),
                      subtitle_font_px=44,line_capacity=20,
                      template='landscape-live-v5-quiet-readable-footer')
    return result


def background(path, style=None):
    from PIL import Image, ImageDraw
    if style_name(style)=='quiet':
        Image.new('RGB',CANVAS,(23,25,28)).save(path)
        return
    image = Image.new('RGB', CANVAS, (117, 34, 47))
    draw = ImageDraw.Draw(image)
    # Original understated snowball motif; no borrowed artwork or account mark.
    for x,y,r in [(65,150,22),(96,500,28),(1215,500,22)]:
        draw.ellipse((x-r,y-r,x+r,y+r),outline=(171,111,78),width=3)
    image.save(path)


def read_captions(directory, names):
    if len(names) != 1:
        raise ValueError('横版重排只接受一段连续源视频，不能猜测多段字幕偏移')
    rows=[]
    def seconds(value):
        h,m,s=value.split(':');return int(h)*3600+int(m)*60+float(s)
    for line in (Path(directory)/names[0]).read_text(encoding='utf-8-sig').splitlines():
        if not line.startswith('Dialogue:'):continue
        fields=line.split(',',9)
        text=re.sub(r'\{[^}]*\}', '', fields[9]).replace('\\N','').replace('\\n','')
        rows.append(dict(start_sec=seconds(fields[1]),end_sec=seconds(fields[2]),zh=text,
                         semantic_group=True))
    if not rows:raise ValueError('横版重排没有真实字幕')
    return rows


def optional_reframe(meta, directory, work, speaker='林园', api_key=None,
                     requested='auto'):
    """An optional format must not discard a byte-identical verified portrait.

    Explicit landscape requests still fail closed. A rejected landscape is
    never returned or admitted; only the already verified original and its
    unchanged review/subtitle assets can survive an automatic-format failure.
    """
    import copy
    import produce_cn as producer
    directory = Path(directory)
    original = copy.deepcopy(meta)
    required = ('live_region_verified', 'no_qr_verified', 'no_black_bars_verified',
                'review_assets_verified', 'subtitle_word_boundaries_verified',
                'subtitle_semantic_groups_verified', 'title_quality_verified')
    if (meta.get('render_mode') != 'live_video_card'
            or meta.get('layout_proof', {}).get('canvas') != {'width':720, 'height':1280}
            or any(meta.get(key) is not True for key in required)
            or meta.get('corner_review', {}).get('passed') is not True):
        raise producer.VisualQualityError('可选横版转换缺少已验收竖版证明')
    names = [meta.get(key) for key in ('final', 'cover', 'preview_30s', 'contact_sheet_6')]
    names += list(meta.get('subtitle_files', [])) + list(meta.get('subtitle_edit_proofs', []))
    if (not meta.get('subtitle_files') or not meta.get('subtitle_edit_proofs')
            or any(not name or Path(name).name != name for name in names)):
        raise producer.VisualQualityError('可选横版转换缺少原始交付资产')
    hashes = {name: producer._file_sha256(directory/name) for name in names}
    if (hashes[meta['final']] != meta.get('fingerprints', {}).get('sha256')
            or hashes[meta['final']] != meta['corner_review'].get('media_sha256')):
        raise producer.VisualQualityError('可选横版转换前竖版指纹与验收证明不一致')
    try:
        return reframe(copy.deepcopy(meta), directory, work, speaker, api_key)
    except producer.VisualQualityError as exc:
        if requested != 'auto':
            raise
        # reframe replaces the input only after its media checks. If a later
        # operation fails, never label the changed file with portrait proofs.
        if any(not (directory/name).is_file()
               or producer._file_sha256(directory/name) != digest
               for name, digest in hashes.items()):
            raise producer.VisualQualityError('横版失败且原交付资产已变化，禁止回退') from exc
        print('[横版] 可选转换未通过；保留指纹及验收证明未变化的竖版：'+str(exc), flush=True)
        return {**original, 'layout_fallback': dict(
            version=1, requested='auto', rejected_layout='landscape',
            retained_layout='portrait', reason=str(exc),
            retained_asset_sha256=hashes, rejected_layout_accepted=False)}


def reframe(meta, directory, work, speaker='林园', api_key=None):
    """Reuse verified source pixels/audio and timing, then verify the actual new file."""
    import produce_cn as producer
    import presentation
    from editorial_policy import subtitle_files_text, text_digest
    directory,work=Path(directory),Path(work)
    if meta.get('render_mode')!='live_video_card':raise ValueError('横版真人模板需要实际动态画面')
    if meta['layout_proof']['canvas'] not in ({'width':720,'height':1280}, {'width':1280,'height':720}):
        raise ValueError('横版重排输入不是支持的真人窗口布局')
    window=source_window(meta)  # Validate a known caption-free window before writing.
    original=directory/meta['final']
    if producer._file_sha256(original)!=meta['fingerprints']['sha256']:
        raise ValueError('横版重排输入视频指纹变化')
    work.mkdir(parents=True,exist_ok=True)
    # Existing illustrated chart footers have source-bound coordinates. Keep
    # their proven layout until a separate chart layout has been verified.
    chosen_style='classic' if (meta.get('interview_context') or {}).get('illustration_cards') else style_name()
    spec=layout(chosen_style);region=spec['live_region']
    font=os.environ.get('ZH_FONT','Noto Sans CJK SC')
    matched=subprocess.check_output(['fc-match','-f','%{family}',font],text=True)
    if font.casefold() not in matched.casefold():
        raise ValueError('横版字幕字体不可用，拒绝输出缺字方框：'+font)
    captions=read_captions(directory,meta['subtitle_files'])
    subtitle=directory/('landscape-'+meta['subtitle_files'][0])
    presentation.write_ass(captions,subtitle,spec,font)
    if text_digest(subtitle_files_text(directory,[subtitle.name])) != meta['subtitle_text_sha256']:
        raise ValueError('横版重排改变了字幕文字')
    bg=work/'landscape-background.png';background(bg,chosen_style)
    target=work/'landscape.mp4';brand=producer.brand_watermark_path()
    rate=subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=avg_frame_rate','-of','default=nw=1:nk=1',str(original)],text=True).strip()
    card_footer=''
    context=meta.get('interview_context') or {}
    # Reposition the footer on our own generated chart so overlaid captions do
    # not collide with it. The chart's values and attribution remain visible.
    if context.get('graphics_profile_source_sha256')=='67ed2a6894ef419b44607632623c02153fc724907c233a20e33629e7125cd42e':
        for card in context.get('illustration_cards',[]):
            if card.get('kind')!='source_quoted_financial_comparison':continue
            a,b=int(card['start_frame']),int(card['end_frame'])
            condition=f'gte(n,{a})*lt(n,{b})'
            card_footer+=(f",drawbox=x=0:y=570:w=iw:h=150:color=0xfafafa:t=fill:enable='{condition}'"
                f",drawtext=font='Noto Sans CJK SC':text='数据为原访谈中的表述':fontsize=22:fontcolor=0x6e747b:"
                f"x=(w-tw)/2:y=540:enable='{condition}'")
    filters=(f'[0:v]crop={window["width"]}:{window["height"]}:{window["x"]}:{window["y"]},scale={region["width"]}:{region["height"]}:flags=lanczos,setsar=1{card_footer}[v];'
             f'[1:v][v]overlay={region["x"]}:{region["y"]}:shortest=1,ass={subtitle}[base];'
             f'[2:v]format=rgba,colorchannelmixer=aa=0.68,scale={BRAND_WIDTH}:-1[brand];'
             '[base][brand]overlay=W-w-16:22:shortest=1[outv]')
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',str(original),
        '-loop','1','-framerate',rate,'-i',str(bg),'-loop','1','-framerate',rate,'-i',str(brand),'-filter_complex',filters,
        '-map','[outv]','-map','0:a:0','-c:v','libx264','-preset','veryfast','-crf','18',
        '-pix_fmt','yuv420p','-c:a','copy','-movflags','+faststart',str(target)],
        check=True,timeout=max(180,int(meta['duration_sec']*5)))
    def audio_digest(path):
        return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),
            '-map','0:a:0','-c','copy','-f','hash','-'],text=True).strip()
    audio_sha=audio_digest(original)
    if audio_digest(target)!=audio_sha:
        raise ValueError('横版音频数据包与原片不一致')
    checks=presentation.verify_render(target,spec)
    context=meta.get('interview_context') or {}
    times=context.get('target_sample_times')
    checks.update(producer.verify_live_region_after_render(target,actor_times=times or (),live_region=region))
    identity=producer.verify_final_live_identity(target,work,speaker,api_key,'-landscape',
                                                  target_times=times,live_region=region)
    # Recognition-aware final-image inspection distinguishes our generated
    # brand from third-party source text; the old shape-only source detector
    # reports our own quiet-layout wordmark as an external logo.
    external_logos=producer.detect_external_logos_after_render(target,'crop',*CANVAS)
    if external_logos:raise producer.VisualQualityError('重排成片仍有外部角标：'+str(external_logos))
    actual=float(producer.probe(target,'format=duration'))
    if abs(actual-meta['duration_sec'])>.15:raise ValueError('横版重排改变了时长')
    fingerprints=producer.build_content_fingerprints(target,subtitle_files_text(directory,[subtitle.name]))
    # Audio is stream-copied; retain the exact original transcript evidence used for dedup.
    for key in ('transcript_ngrams','transcript_simhash'):
        if key in meta['fingerprints']:fingerprints[key]=meta['fingerprints'][key]
    original_sha=meta['fingerprints']['sha256']
    target.replace(original)
    preview,sheet=producer.make_review_assets(original,directory,'-landscape',actual)
    return {**meta,**checks,'layout_proof':spec,'resolution':dict(width=1280,height=720,short_edge=720),
            'vertical':False,'duration_sec':round(actual,1),'final_live_identity':identity,
            'fingerprints':fingerprints,'subtitle_files':[subtitle.name],
            'video_title':None,'video_title_proof':None,'audio_card_template':spec['template'],
            'brand_watermark':{**meta.get('brand_watermark',{}),'width_ratio':BRAND_WIDTH/CANVAS[0]},
            'preview_30s':preview,'contact_sheet_6':sheet,
            'landscape_reframe':dict(version=5 if chosen_style=='quiet' else 3,style=chosen_style,input_sha256=original_sha,source_window=window,
                output_window=region,audio_stream_copied=True,audio_stream_sha256=audio_sha,
                source_frame_rate=rate,subtitle_timing_preserved=True)}
