#!/usr/bin/env python3
"""Two review-only complete-story cuts; no production approval or publishing.

The visible caption drafts merge broken ASR words and omit stutters. The source
audio remains continuous. An ambiguous ASR sentence has no displayed caption;
this is recorded for listening review rather than silently corrected.
"""
import hashlib
import json
from pathlib import Path
import subprocess

import cv2
import imageio_ffmpeg
from PIL import Image

from preview_linyuan_formats import ROOT, SOURCE_SHA, fit, text_block

START = 2584.36
END = 2614.04
TITLE = '林园：租客半年没付房租，我先让他腾房'
CAPTIONS = [
    (0, 6, '我有一个房子\n他半年没付我租金呢'),
    (6.56, 11.44, '我问他要，让他把房子给我腾回来\n我就没跟他说租金的事'),
    (14.4, 19.36, '他已经遇到困难了\n你赶紧把房子先拿回来再说'),
    (23.76, 26.48, '人要学会自己安慰自己'),
    (26.48, 29.68, '当然我也希望他把这个钱给我'),
]


def main():
    source = ROOT / 'output/optimization-v1/production-samples/mother/video.mp4'
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('source fingerprint mismatch')
    out = ROOT / 'output/benchmark-20260921/story'
    out.mkdir(parents=True, exist_ok=True)
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    clip = out / 'source-excerpt.mp4'
    subprocess.run([ffmpeg, '-y', '-loglevel', 'error', '-ss', str(START),
                    '-i', str(source), '-t', str(END - START), '-vf', 'fps=15',
                    '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18',
                    '-c:a', 'aac', str(clip)], check=True, timeout=120)
    records = []
    for aspect, size in [('portrait', (720, 1280)), ('landscape', (1280, 720))]:
        target = out / (aspect + '.mp4')
        command = [ffmpeg, '-y', '-loglevel', 'error', '-f', 'rawvideo',
                   '-pix_fmt', 'rgb24', '-s', f'{size[0]}x{size[1]}', '-r', '15',
                   '-i', '-', '-i', str(clip), '-map', '0:v', '-map', '1:a:0',
                   '-t', str(END - START), '-c:v', 'libx264', '-preset', 'veryfast',
                   '-crf', '23', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
                   '-movflags', '+faststart', str(target)]
        cap = cv2.VideoCapture(str(clip))
        count = 0
        with (out / (aspect + '.log')).open('wb') as log:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=log)
            try:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    original = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    guest = original.crop((26, 175, 855, 634))
                    im = Image.new('RGB', size, '#0b0b0b')
                    text_block(im, '园来滚雪球 · 内容试剪',
                               (size[0]-345, 22, 315, 45), 23, '#dddddd')
                    if aspect == 'portrait':
                        text_block(im, '半年没付我房租\n我先让他把房子腾回来',
                                   (35, 135, 650, 210), 51, '#ffe54c')
                        fit(im, guest, (0, 390, 720, 410))
                        caption_box = (35, 930, 650, 200)
                    else:
                        fit(im, guest, (440, 55, 820, 530))
                        text_block(im, '半年没付房租\n先把房子\n腾回来',
                                   (25, 180, 380, 300), 51, '#ffe54c')
                        caption_box = (50, 594, 1180, 120)
                    caption = next((text for start, end, text in CAPTIONS
                                    if start <= count/15 < end), '')
                    if caption:
                        text_block(im, caption, caption_box, 39, '#ffffff')
                    if count == 105:
                        im.save(out / (aspect + '.jpg'))
                    proc.stdin.write(im.tobytes())
                    count += 1
            finally:
                cap.release()
                proc.stdin.close()
            if proc.wait(timeout=120) != 0:
                raise RuntimeError(f'render failed: {aspect}')
        subprocess.run([ffmpeg, '-v', 'error', '-i', str(target), '-map', '0:v',
                        '-map', '0:a', '-f', 'null', '-'], check=True, timeout=120)
        records.append(dict(aspect=aspect, title=TITLE, source_sha256=SOURCE_SHA,
                            source_range=[START, END], dimensions=list(size),
                            frame_count=count, video_audio_full_decode=True,
                            output_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                            review_only=True, publication_authorized=False,
                            captions=CAPTIONS, audio_continuous=True,
                            listening_review_required=True,
                            omitted_ambiguous_caption_range=[2596.76, 2598.6]))
        print(aspect, count, flush=True)
    (out / 'manifest.json').write_text(json.dumps(records, ensure_ascii=False, indent=2)+'\n')


if __name__ == '__main__':
    main()
