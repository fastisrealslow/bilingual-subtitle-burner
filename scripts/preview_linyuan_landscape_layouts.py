#!/usr/bin/env python3
"""Isolated layout comparison using one hash-bound actual video; never production."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'linyuan'))
import landscape
import presentation
from caption_readability import display_payload_text


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(*args):
    subprocess.run([str(x) for x in args], check=True, timeout=300,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--media', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--start', type=float, required=True)
    ap.add_argument('--end', type=float, required=True)
    args = ap.parse_args()
    args.media, args.out = args.media.resolve(), args.out.resolve()
    meta = json.loads((args.media/'meta.json').read_text())
    if isinstance(meta, list):
        if len(meta) != 1:raise ValueError('Expected one actual video')
        meta = meta[0]
    source = args.media/meta['final']
    if sha(source) != meta['fingerprints']['sha256']:
        raise ValueError('Actual video hash changed')
    if not 0 <= args.start < args.end <= meta['duration_sec']:
        raise ValueError('Preview outside actual video')
    window = meta['layout_proof']['live_region']
    args.out.mkdir(parents=True, exist_ok=True)
    ff = ROOT/'output/local-tools/ffmpeg'
    font = ROOT/'output/local-tools/fonts/NotoSansCJKsc-Bold.otf'
    clip = args.out/'current.mp4'
    common = ['-c:v', 'libx264', '-threads', '2', '-preset', 'veryfast', '-crf', '18',
              '-pix_fmt', 'yuv420p', '-movflags', '+faststart']
    run(ff, '-y', '-v', 'error', '-ss', args.start, '-i', source,
        '-t', args.end-args.start, '-map', '0:v:0', '-map', '0:a:0',
        *common, '-c:a', 'aac', '-b:a', '160k', clip)
    captions = []
    for row in landscape.read_captions(args.media, meta['subtitle_files']):
        a, b = max(args.start, row['start_sec']), min(args.end, row['end_sec'])
        if a < b:
            captions.append({**row, 'start_sec': a-args.start, 'end_sec': b-args.start})
    expected_text = display_payload_text(''.join(c['zh'] for c in captions))
    variants = [
        ('quiet-footer', '深灰底，画面放大，字幕独立底栏', 806, 600, 237, 0, 606, 108, 40, False),
        ('live-overlay', '完整画面占满高度，字幕叠在底部', 968, 720, 156, 0, 598, 114, 40, True),
    ]
    rows = [dict(id='current', label='当前真实成片排版', file=str(clip.relative_to(ROOT)),
                 live_area_fraction=window['width']*window['height']/(1280*720))]
    for ident, label, w, h, x, y, sy, sh, size, overlay in variants:
        spec = landscape.layout()
        spec.update(live_region=dict(x=x, y=y, width=w, height=h),
                    subtitle_region=dict(x=180, y=sy, width=920, height=sh),
                    subtitle_font_px=size, line_capacity=22, template=ident)
        ass = args.out/(ident+'.ass')
        prepared = presentation.write_ass(captions, ass, spec, 'Noto Sans CJK SC')
        if display_payload_text(''.join(c['zh'] for c in prepared)) != expected_text:
            raise ValueError('Layout changed displayed words')
        target = args.out/(ident+'.mp4')
        vf = (f'crop={window["width"]}:{window["height"]}:{window["x"]}:{window["y"]},'
              f'scale={w}:{h}:flags=lanczos,setsar=1,pad=1280:720:{x}:{y}:color=0x17191c')
        if overlay:
            vf += ',drawbox=x=156:y=588:w=968:h=132:color=black@0.38:t=fill'
        vf += f",ass=filename='{ass}':fontsdir='{font.parent}',drawtext=fontfile='{font}':text='排版试看':fontsize=26:fontcolor=white@0.7:x=20:y=20"
        run(ff, '-y', '-v', 'error', '-i', clip, '-vf', vf, *common, '-c:a', 'copy', target)
        rows.append(dict(id=ident, label=label, file=str(target.relative_to(ROOT)),
                         live_area_fraction=w*h/(1280*720), layout=spec,
                         caption_over_source=overlay))
    def audio_hash(path):
        return subprocess.check_output([str(ff), '-v', 'error', '-i', str(path),
            '-map', '0:a:0', '-c', 'copy', '-f', 'hash', '-'], text=True).strip()
    reference_audio = audio_hash(clip)
    for row in rows:
        path = ROOT/row['file']
        if audio_hash(path) != reference_audio:raise ValueError('Variant changed clip audio')
        run(ff, '-v', 'error', '-xerror', '-i', path, '-map', '0:v', '-map', '0:a', '-f', 'null', '-')
        poster = path.with_suffix('.jpg')
        run(ff, '-y', '-v', 'error', '-ss', 12, '-i', path, '-frames:v', 1, poster)
        row.update(sha256=sha(path), poster=str(poster.relative_to(ROOT)),
                   clip_audio_sha256=reference_audio, full_av_decode=True)
    report = dict(input_sha256=sha(source), input_file=str(source.relative_to(ROOT)),
                  clip_start=args.start, clip_end=args.end,
                  original_title=meta['title'], variants=rows, editorial_approved=False,
                  production_authorized=False, source_yield_credit=0,
                  limitation='Layout-only excerpt. Overlay may obscure source details; production face/OCR gates have not run. No source or title correction. Enlargement adds no source resolution.')
    (args.out/'review.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(variants=len(rows), report=str(args.out/'review.json'))))


if __name__ == '__main__':
    main()
