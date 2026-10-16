"""Local, non-publishing A/B preview from an existing verified video bundle."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import caption_lines
import caption_readability
import presentation
import produce_cn


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--ffmpeg',default='ffmpeg')
    parser.add_argument('--start',type=float,default=17.3)
    parser.add_argument('--end',type=float,default=48.0)
    args=parser.parse_args()
    meta=json.loads((args.bundle/'meta.json').read_text())
    proof=json.loads((args.bundle/meta['subtitle_edit_proofs'][0]).read_text())
    caption_readability.replay_edit_proof(proof)
    entries,clean=caption_readability.clean_entries(proof['raw_entries'],policy_version=proof['version'])
    assert clean['display_text']==proof['display_text']
    layout=copy.deepcopy(meta['layout_proof'])
    groups=produce_cn.apply_semantic_groups(entries,
        produce_cn.source_caption_groups(entries,layout),layout['line_capacity'],layout['subtitle_font_px'])
    screens=caption_lines.one_line_screens(groups,layout)
    assert [a for row in screens for a in row['caption_chars']]==[a for row in groups for a in row['caption_chars']]
    assert all(len(row['lines'])==1 and row['font_px']==layout['subtitle_font_px'] for row in screens)
    selected=copy.deepcopy([row for row in screens if row['start_sec']>=args.start and row['end_sec']<=args.end])
    for row in selected:
        row['start_sec']-=args.start;row['end_sec']-=args.start
        row['caption_chars']=[(c,a-args.start,b-args.start) for c,a,b in row['caption_chars']]
    args.output.mkdir(parents=True,exist_ok=True)
    ass=args.output/'natural-preview.ass'
    rendered=presentation.write_ass(selected,ass,layout,'Noto Sans CJK SC')
    assert [row['zh'] for row in rendered]==[row['zh'] for row in selected]
    video=args.bundle/meta['final']
    region=layout['subtitle_region']
    if layout.get('mode')!='audio_card':
        raise ValueError('A/B reburn requires the existing solid caption band, never paint over source footage')
    import cv2
    capture=cv2.VideoCapture(str(video));capture.set(cv2.CAP_PROP_POS_MSEC,args.start*1000)
    ok,frame=capture.read();capture.release()
    if not ok:raise ValueError('Cannot inspect existing caption band')
    x,y,w,h=(region[k] for k in ('x','y','width','height'))
    corners=[frame[b,a].tolist() for a,b in [(x,y),(x+w-1,y),(x,y+h-1),(x+w-1,y+h-1)]]
    if any(max(row[c] for row in corners)-min(row[c] for row in corners)>4 for c in range(3)):
        raise ValueError('Caption band is not uniform; refuse destructive preview masking')
    bgr=[sorted(row[c] for row in corners)[len(corners)//2] for c in range(3)]
    color='0x'+''.join(f'{v:02x}' for v in reversed(bgr))
    box=f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t=fill"
    for name,filters in [('before.mp4',[]),('natural-preview.mp4',['-vf',box+',ass=natural-preview.ass:fontsdir=fonts'])]:
        subprocess.run([args.ffmpeg,'-y','-v','error','-ss',str(args.start),'-i',str(video),
            '-t',str(args.end-args.start),*filters,'-map','0:v:0','-map','0:a:0',
            '-c:v','libx264','-crf','18','-preset','veryfast','-c:a','aac',
            '-movflags','+faststart',name],cwd=args.output,check=True,timeout=240)
    def audio_digest(path):
        raw=subprocess.check_output([args.ffmpeg,'-v','error','-i',str(path),
            '-map','0:a:0','-c:a','copy','-f','adts','pipe:1'],timeout=60)
        return hashlib.sha256(raw).hexdigest()
    assert audio_digest(args.output/'before.mp4')==audio_digest(args.output/'natural-preview.mp4')
    report=dict(preview_only=True,published=False,source_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
        start=args.start,end=args.end,font_px=layout['subtitle_font_px'],line_capacity=layout['line_capacity'],
        source_characters_and_anchors_unchanged=True,comparison_audio_identical=True,
        captions=[dict(text=row['zh'],start=row['start_sec'],end=row['end_sec']) for row in rendered])
    (args.output/'preview-proof.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
