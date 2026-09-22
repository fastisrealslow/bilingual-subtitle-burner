#!/usr/bin/env python3
"""Transcribe frozen reference videos for title/context comparison, never production."""
import argparse
from dataclasses import asdict
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import time

from linyuan_audio_crosscheck import MODEL, REVISION, ROOT, sha

CORPUS = ROOT / 'linyuan/simulations/benchmark-20260921/reference-audio-corpus.json'


def bound_video(case, media):
    matches = list(media.rglob('full-review.mp4'))
    if len(matches) != 1 or sha(matches[0]) != case['preview_sha256']:
        raise ValueError('Reference video must match the frozen full-timeline preview SHA')
    return matches[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case', type=int, required=True)
    ap.add_argument('--media', type=Path, required=True)
    ap.add_argument('--out', type=Path, default=Path('reference-audio-results'))
    args = ap.parse_args()
    case = json.loads(CORPUS.read_text())[args.case]
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / 'result.json'
    row = dict(case=case, model=MODEL, revision=REVISION, status='unresolved',
               run_id=os.environ.get('GITHUB_RUN_ID'), commit=os.environ.get('GITHUB_SHA'),
               title_used_as_prompt=False, human_audio_review=False,
               scope='Full reference ASR for analysis; not proof of speech or speaker identity', segments=[])
    def save():
        target.write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n')
    save()
    start = time.monotonic()
    try:
        video = bound_video(case, args.media)
        audio = args.out / 'reference-audio.wav'
        subprocess.run([os.environ.get('FFMPEG_BINARY', 'ffmpeg'), '-v', 'error', '-y',
                        '-i', str(video), '-vn', '-ac', '1', '-ar', '16000', str(audio)],
                       check=True, timeout=120)
        row['audio_sha256'] = sha(audio)
        from huggingface_hub import snapshot_download
        from faster_whisper import WhisperModel
        model = WhisperModel(snapshot_download(MODEL, revision=REVISION),
                             device='cpu', compute_type='int8', cpu_threads=4, num_workers=1)
        row['package_versions'] = {k: importlib.metadata.version(k)
                                   for k in ('faster-whisper', 'ctranslate2', 'huggingface-hub')}
        segments, info = model.transcribe(str(audio), language='zh', task='transcribe',
            beam_size=5, temperature=0, condition_on_previous_text=False,
            word_timestamps=True, vad_filter=False)
        if abs(info.duration - case['duration']) > 1:
            raise ValueError('Decoded audio duration differs from frozen reference timeline')
        row['duration'] = info.duration
        for segment in segments:
            row['segments'].append(asdict(segment))
            save()
        row['recognized_text'] = ''.join(s['text'] for s in row['segments'])
        row['status'] = 'transcribed'
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
    row['seconds'] = round(time.monotonic() - start, 2)
    save()
    print(json.dumps({k: v for k, v in row.items() if k not in ('segments', 'recognized_text')}, ensure_ascii=False))
    if row['status'] != 'transcribed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
