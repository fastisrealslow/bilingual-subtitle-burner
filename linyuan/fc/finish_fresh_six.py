"""Reproducible editorial cuts from immutable, actually rendered source windows.

No ASR, rewriting, or inferred corrections. Preserve every subtitle character
inside each selected complete argument; reject cuts through spoken captions.
"""
import copy
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import produce_cn as p
import presentation as pres

SLUG = 'ly-fresh-six-0906-05'
PARENT_RUN = 34030288401
ACCEPTED_CHECKPOINT_RUN = 34031908071
RECIPES = [
    (1, 0, 29.60, '林园：金额很小，比存款利息好一点', '11cc3a7093f2200afe99409dee332c01e0dc2d51f6258b2126968d513be32371'),
    (2, 2.20, 33.10, '林园：打新不是我们主要的策略', '96384f9fa696e6404c7c0a5f5e00b9f5e32200df66488b833c090138df0d3ba1'),
    (15, 0, 16.60, '林园：我们说创新一定是人的力量', '400f54bcf1575d585148581e16c898e72318093c84b8e5f68c1484c3eb60905a'),
    (16, 0, 18.80, '林园：读书能力不代表创新能力', 'c14cca1a8fb2804207e7975cc07bdfd312c76ccfa0ce8cfe0d90d205f72cd07e'),
    (17, 11.15, 27.20, '林园：我问喝酒的人，你们把酒戒掉', 'b87d107c5950a65372d0f6a9de7aa7d600065016e13e32165bde62b6880b4a9b'),
    (21, 0, 20.05, '林园：港股和A股实际上是同步的', 'a41aeec4a01fdb525578fe9f8b8796508680815546595feb8e3d810c74f951a9'),
]


def seconds(value):
    h, m, s = map(float, value.split(':'))
    return h * 3600 + m * 60 + s


def captions(source, number, start, end):
    entries = []
    for line in (source / f'subtitles_{number}-1.ass').read_text('utf-8-sig').splitlines():
        if not line.startswith('Dialogue:'):
            continue
        fields = line.split(',', 9)
        a, b = seconds(fields[1]), seconds(fields[2])
        if b <= start or a >= end:
            continue
        if a < start - .011 or b > end + .011:
            raise ValueError(f'Cut crosses a spoken caption: {number}, {a}, {b}')
        text = re.sub(r'\{[^}]*\}', '', fields[9]).replace('\\N', '')
        entries.append(dict(start_sec=a-start, end_sec=b-start, zh=text, semantic_group=True))
    before = ''.join(e['zh'] for e in entries)
    # Rejoin the actual complete sentence, whose original screen ended with 往.
    if number == 21:
        i = next(i for i,e in enumerate(entries) if e['zh'].endswith('趋势往'))
        entries[i:i+2] = [{**entries[i], 'zh': entries[i]['zh']+entries[i+1]['zh'],
                          'end_sec': entries[i+1]['end_sec']}]
    assert before == ''.join(e['zh'] for e in entries)
    for e in entries:
        for font in range(48,37,-1):
            capacity = int(612/(font*1.05))
            try:
                pres.wrap_words(e['zh'],capacity)
            except ValueError:
                continue
            e.update(font_px=font,line_capacity=capacity)
            break
        else:
            raise ValueError('Complete caption cannot fit at 38 px')
    return entries


def main():
    source = Path('fresh-six-original')
    if not (source/'meta.json').exists():
        subprocess.run(['gh','run','download',str(PARENT_RUN),'--repo',
            'fastisrealslow/bilingual-subtitle-burner','--name','deliver-'+SLUG,
            '--dir',str(source)],check=True)
    original = json.loads((source/'meta.json').read_text())
    out = Path('fresh-six-finished')
    if not (out/'meta.json').exists():
        subprocess.run(['gh','run','download',str(ACCEPTED_CHECKPOINT_RUN),'--repo',
            'fastisrealslow/bilingual-subtitle-burner','--name','deliver-'+SLUG,
            '--dir',str(out)],check=True)
    work = out/'review'; work.mkdir(exist_ok=True)
    reference = p._download_speaker_reference('林园',work)
    portrait = p.extract_audio_card_portrait(reference,work/'portrait.png')
    result=json.loads((out/'meta.json').read_text())
    for number,start,end,title,pinned_sha in RECIPES:
        existing=next((m for m in result if m['final']==f'final_{number}.mp4'),None)
        if existing:
            if (existing['title']!=title or p._file_sha256(out/existing['final'])!=existing['fingerprints']['sha256']
                    or existing['editorial_provenance']['parent_sha256']!=pinned_sha):
                raise ValueError('Accepted checkpoint changed')
            print(json.dumps({'retained':existing['final'],'sha256':existing['fingerprints']['sha256']}),flush=True)
            continue
        try:
            parent = next(m for m in original if m['final']==f'final_{number}.mp4')
            video = source/parent['final']
            parent_sha = p._file_sha256(video)
            if parent_sha != parent['fingerprints']['sha256'] or (pinned_sha and parent_sha != pinned_sha):
                raise ValueError('Immutable production input hash changed')
            entries = captions(source,number,start,end)
            title_error=p.title_quality_error(title,'林园',''.join(e['zh'] for e in entries))
            if title_error: raise ValueError(title_error)
            suffix=f'_{number}'; duration=end-start
            layout=pres.layout_for(720,1280,True)
            ass=out/f'subtitles{suffix}-1.ass'
            rendered=pres.write_ass(entries,ass,layout,'Noto Sans CJK SC')
            assert ''.join(e['zh'] for e in rendered)==''.join(e['zh'] for e in entries)
            card=p.make_audio_card(work/f'card{suffix}.png','林园',title,
                portrait_path=portrait,require_portrait=True)
            # Label this actual moving interview correctly, retaining its date.
            from PIL import Image,ImageDraw,ImageFont
            im=Image.open(card); draw=ImageDraw.Draw(im)
            for y in range(1070,1118):
                blend=y/1279
                draw.line((0,y,720,y),fill=(int(238-15*blend),int(237-14*blend),int(232-12*blend)))
            font='/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'
            draw.text((48,1080),'2025年8月21日公开访谈 · 原声节选',
                font=ImageFont.truetype(font,24,index=p._sc_face_index(font)),fill=(89,94,99))
            im.save(card)
            final=out/f'final{suffix}.mp4'
            vf=f'[1:v]crop=632:470:44:360,setpts=PTS-STARTPTS[live];[0:v][live]overlay=44:360,ass={ass}[outv]'
            subprocess.run(['ffmpeg','-y','-v','error','-loop','1','-framerate','30','-i',str(card),
                '-ss',str(start),'-t',str(duration),'-i',str(video),'-filter_complex',vf,
                '-map','[outv]','-map','1:a:0','-af','asetpts=PTS-STARTPTS',
                '-c:v','libx264','-preset','veryfast','-crf','18','-c:a','aac','-b:a','192k',
                '-r','30','-t',str(duration),'-movflags','+faststart',str(final)],check=True,timeout=180)
            subprocess.run(['ffmpeg','-v','error','-i',str(final),'-f','null','-'],check=True,timeout=90)
            meta=copy.deepcopy(parent)
            meta.update(pres.verify_render(final,layout))
            meta.update(p.verify_live_region_after_render(final))
            meta['final_live_identity']=p.verify_final_live_identity(final,work,'林园',p.load_key(),suffix)
            cover=out/f'cover{suffix}.jpg'
            p.make_audio_card(cover,'林园',title,1280,720,portrait_path=portrait,require_portrait=True)
            proof=json.loads(Path(str(cover)+'.proof.json').read_text())
            actual_duration=float(p.probe(final,'format=duration'))
            preview,sheet=p.make_review_assets(final,out,suffix,actual_duration)
            meta.update(title=title,desc='2025年8月21日公开访谈原声节选。观点和市场语境属于原发言日期。',
                cover=cover.name,cover_proof=proof,final=final.name,
                preview_30s=preview,contact_sheet_6=sheet,subtitle_files=[ass.name],
                layout_proof=layout,duration_sec=actual_duration,
                fingerprints=p.build_content_fingerprints(final,''.join(e['zh'] for e in entries)),
                generated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                segments=[{'start':parent['segments'][0]['start']+start,
                           'end':parent['segments'][0]['start']+end,
                           'reason':'逐句复核的完整观点；排除下一话题及含歧义识别的后段'}],
                editorial_provenance={'parent_run':PARENT_RUN,'parent_sha256':parent_sha,
                    'input_range':[start,end],'subtitle_characters_preserved':True,
                    'asr_api_calls':0,'reverified_encoded_output':True})
            result.append(meta)
            (out/'meta.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            print(json.dumps({'accepted':final.name,'seconds':actual_duration,
                'sha256':meta['fingerprints']['sha256'],'title':title},ensure_ascii=False),flush=True)
        except Exception as exc:
            print(json.dumps({'rejected':number,'error':str(exc)},ensure_ascii=False),flush=True)
            continue
    assert len(result)==6


if __name__=='__main__':
    main()
