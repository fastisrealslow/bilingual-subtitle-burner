"""Landscape presentation of a verified moving window; never stretch a portrait card."""
import hashlib
import os
import re
import subprocess
from pathlib import Path

CANVAS = (1280, 720)
# Keep the 632:470 clean window's aspect ratio to within one encoded pixel.
LIVE_REGION = dict(x=252, y=0, width=774, height=576)


def selected(meta, requested='auto'):
    if requested not in {'auto', 'portrait', 'landscape'}:
        raise ValueError('未知横竖屏选择')
    if meta.get('render_mode') != 'live_video_card':
        return False  # Native footage keeps its original aspect; audio cards stay portrait.
    if requested == 'auto' and len(meta.get('subtitle_files', [])) != 1:
        return False
    if requested != 'auto':
        return requested == 'landscape'
    key = str(meta.get('source_sha256')) + repr(meta.get('segments'))
    return hashlib.sha256(key.encode()).digest()[0] % 3 == 0


def layout():
    from presentation import layout_for
    result = layout_for(*CANVAS)
    result.update(live_region=dict(LIVE_REGION), subtitle_region=dict(x=80,y=578,width=1120,height=138),
                  subtitle_font_px=44, line_capacity=23, subtitle_style='white-outline',
                  template='landscape-live-v1', preserve_display_text=True)
    return result


def background(path):
    from PIL import Image, ImageDraw
    image = Image.new('RGB', CANVAS, (117, 34, 47))
    draw = ImageDraw.Draw(image)
    # Original understated snowball motif; no borrowed artwork or account mark.
    for x,y,r in [(75,110,23),(170,440,36),(1120,150,32),(1220,470,20)]:
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


def reframe(meta, directory, work, speaker='林园', api_key=None):
    """Reuse verified source pixels/audio and timing, then verify the actual new file."""
    import produce_cn as producer
    import presentation
    from editorial_policy import subtitle_files_text, text_digest
    directory,work=Path(directory),Path(work)
    if meta.get('render_mode')!='live_video_card':raise ValueError('横版真人模板需要实际动态画面')
    if meta['layout_proof']['canvas']!={'width':720,'height':1280}:
        raise ValueError('横版重排输入不是已验证的竖版真人窗口')
    original=directory/meta['final']
    if producer._file_sha256(original)!=meta['fingerprints']['sha256']:
        raise ValueError('横版重排输入视频指纹变化')
    work.mkdir(parents=True,exist_ok=True)
    spec=layout();region=spec['live_region']
    captions=read_captions(directory,meta['subtitle_files'])
    subtitle=directory/('landscape-'+meta['subtitle_files'][0])
    presentation.write_ass(captions,subtitle,spec,os.environ.get('ZH_FONT','Noto Sans CJK SC'))
    if text_digest(subtitle_files_text(directory,[subtitle.name])) != meta['subtitle_text_sha256']:
        raise ValueError('横版重排改变了字幕文字')
    bg=work/'landscape-background.png';background(bg)
    target=work/'landscape.mp4';brand=producer.brand_watermark_path()
    filters=(f'[0:v]crop=632:470:44:360,scale={region["width"]}:{region["height"]}:flags=lanczos,setsar=1[v];'
             f'[1:v][v]overlay={region["x"]}:{region["y"]}:shortest=1,ass={subtitle}[base];'
             '[2:v]format=rgba,colorchannelmixer=aa=0.68,scale=166:-1[brand];'
             '[base][brand]overlay=W-w-22:22:shortest=1[outv]')
    subprocess.run(['ffmpeg','-y','-loglevel','error','-i',str(original),
        '-loop','1','-i',str(bg),'-loop','1','-i',str(brand),'-filter_complex',filters,
        '-map','[outv]','-map','0:a:0','-c:v','libx264','-preset','veryfast','-crf','18',
        '-pix_fmt','yuv420p','-c:a','copy','-t',str(meta['duration_sec']),'-movflags','+faststart',str(target)],
        check=True,timeout=max(180,int(meta['duration_sec']*5)))
    checks=presentation.verify_render(target,spec)
    context=meta.get('interview_context') or {}
    times=context.get('target_sample_times')
    checks.update(producer.verify_live_region_after_render(target,actor_times=times or (),live_region=region))
    identity=producer.verify_final_live_identity(target,work,speaker,api_key,'-landscape',
                                                  target_times=times,live_region=region)
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
            'video_title':None,'video_title_proof':None,'audio_card_template':'landscape-live-v1',
            'preview_30s':preview,'contact_sheet_6':sheet,
            'landscape_reframe':dict(version=1,input_sha256=original_sha,source_window=dict(x=44,y=360,width=632,height=470),
                output_window=region,audio_stream_copied=True,subtitle_timing_preserved=True)}
