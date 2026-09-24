"""Render a 30-second layout comparison from fixed media and existing captions.

No ASR, model, title rewrite or publication. A preview is not a delivery approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'linyuan'))
from editorial_policy import ass_dialogue_text
from presentation import footer_layout_for, footer_filter


def move_to_footer(content, layout):
    region=layout['subtitle_region']
    x=region['x']+region['width']//2
    y=region['y']+region['height']//2
    lines=[]
    style_fields=None
    for line in content.splitlines():
        if line.startswith('PlayResY:'):
            line=f'PlayResY: {layout["canvas"]["height"]}'
        elif line.startswith('Format: Name, Fontname,'):
            style_fields=[x.strip() for x in line.split(':',1)[1].split(',')]
        elif line.startswith('Style:'):
            values=line.split(':',1)[1].strip().split(',')
            if not style_fields or len(values)!=len(style_fields):
                raise ValueError('Unknown ASS style format')
            updates=dict(PrimaryColour='&H00FFFFFF',OutlineColour='&H00000000',
                         BackColour='&H00000000',BorderStyle='1',Outline='2.5',Shadow='0')
            for field,value in updates.items():values[style_fields.index(field)]=value
            line='Style: '+','.join(values)
        elif line.startswith('Dialogue:'):
            line,count=re.subn(r'\\pos\([\d.]+,[\d.]+\)',lambda _:f'\\pos({x},{y})',line)
            if count!=1:raise ValueError('Expected one explicit position per caption')
        lines.append(line)
    result='\n'.join(lines)+'\n'
    assert ass_dialogue_text(result)==ass_dialogue_text(content), 'Caption text changed'
    before=[line.split(',',3)[:3] for line in content.splitlines() if line.startswith('Dialogue:')]
    after=[line.split(',',3)[:3] for line in result.splitlines() if line.startswith('Dialogue:')]
    assert before==after, 'Caption timing changed'
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    meta_paths=list(args.evidence.rglob('meta.json'))
    if len(meta_paths)!=1:raise ValueError('Expected one historical metadata file')
    metas=json.loads(meta_paths[0].read_text())
    meta=next(m for m in metas if m['part']==3)
    source_hash=hashlib.sha256(args.source.read_bytes()).hexdigest()
    if source_hash!=meta['source_sha256']:raise ValueError('Mother media fingerprint changed')
    if len(meta['segments'])!=1 or len(meta['subtitle_files'])!=1:
        raise ValueError('Preview requires one continuous source range')
    ass_paths=list(args.evidence.rglob(meta['subtitle_files'][0]))
    if len(ass_paths)!=1:raise ValueError('Historical captions missing')
    original=ass_paths[0].read_text(encoding='utf-8-sig')
    w,h=meta['resolution']['width'],meta['resolution']['height']
    layout=footer_layout_for(w,h)
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    (out/'before.ass').write_text(original,encoding='utf-8-sig')
    (out/'footer.ass').write_text(move_to_footer(original,layout),encoding='utf-8-sig')
    clean=meta['native_context_proof']['video_filter']
    start=meta['segments'][0]['start']
    reports=[]
    for name,pad in [('before','null'),('footer',footer_filter(layout))]:
        subprocess.run(['ffmpeg','-y','-v','error','-ss',str(start),'-i',str(args.source.resolve()),
            '-t','30','-vf',f'{clean},setsar=1,{pad},ass={name}.ass',
            '-c:v','libx264','-preset','veryfast','-crf','20','-r','30',
            '-c:a','aac','-b:a','192k',name+'.mp4'],cwd=out,check=True,timeout=240)
        probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries',
            'format=duration:stream=codec_type,width,height','-of','json',str(out/(name+'.mp4'))]))
        video=next(s for s in probe['streams'] if s['codec_type']=='video')
        expected=(w,h) if name=='before' else (w,layout['canvas']['height'])
        assert (video['width'],video['height'])==expected
        assert any(s['codec_type']=='audio' for s in probe['streams'])
        assert abs(float(probe['format']['duration'])-30)<.2
        subprocess.run(['ffmpeg','-y','-v','error','-ss','12','-i',str(out/(name+'.mp4')),
                        '-frames:v','1',str(out/(name+'.jpg'))],check=True,timeout=30)
        reports.append(dict(file=name+'.mp4',dimensions=expected,
                            sha256=hashlib.sha256((out/(name+'.mp4')).read_bytes()).hexdigest()))
    (out/'preview-proof.json').write_text(json.dumps(dict(review_only=True,
        publication_authorized=False,source_sha256=source_hash,source_start=start,
        duration_seconds=30,captions_and_timings_unchanged=True,
        layout=layout,outputs=reports),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
