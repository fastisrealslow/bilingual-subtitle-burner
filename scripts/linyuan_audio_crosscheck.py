#!/usr/bin/env python3
"""Independent ASR on SHA-bound original audio windows; never auto-edit subtitles."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
CORPUS=ROOT/'linyuan/simulations/benchmark-20260921/audio-crosscheck-corpus.json'
MODEL='Systran/faster-whisper-large-v3'
REVISION='53ecf83a5bedc5597eb8c8b34eac29e5345520ff'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def extract(case,media,out):
    name=case['file']
    if Path(name).name!=name:raise ValueError('unsafe media filename')
    video=media/name
    if sha(video)!=case['final_sha256']:raise ValueError('actual audio/video does not match fixed source')
    a,b=case['window']['start'],case['window']['end']
    if not 0<=a<b or b-a>30:raise ValueError('audio diagnostic exceeds fixed 30-second window')
    target=out/'original-audio.wav'
    subprocess.run([os.environ.get('FFMPEG_BINARY','ffmpeg'),'-v','error','-y','-i',str(video),'-ss',str(a),'-t',str(b-a),
        '-vn','-ac','1','-ar','16000',str(target)],check=True,timeout=60)
    return target


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--case',type=int,required=True)
    ap.add_argument('--media',type=Path,required=True)
    ap.add_argument('--corpus',type=Path,default=CORPUS)
    ap.add_argument('--out',type=Path,default=Path('audio-crosscheck-results'))
    args=ap.parse_args();case=json.loads(args.corpus.read_text())[args.case]
    args.out.mkdir(parents=True,exist_ok=True);target=args.out/'result.json'
    row=dict(case=case,model=MODEL,revision=REVISION,commit=os.environ.get('GITHUB_SHA'),
        run_id=os.environ.get('GITHUB_RUN_ID'),status='unresolved',editorial_approved=False,
        subtitles_modified=False,scope='Second ASR is a disagreement signal, not human hearing or proof of correctness')
    def save():target.write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n')
    save();start=time.monotonic()
    try:
        audio=extract(case,args.media,args.out);row['audio_sha256']=sha(audio);save()
        from huggingface_hub import snapshot_download
        from faster_whisper import WhisperModel
        path=snapshot_download(MODEL,revision=REVISION)
        row['package_versions']={k:importlib.metadata.version(k) for k in ('faster-whisper','ctranslate2','huggingface-hub')}
        model=WhisperModel(path,device='cpu',compute_type='int8',cpu_threads=4,num_workers=1)
        # No hotword/initial prompt: the second model must not be led toward
        # the suspected primary-ASR term or our preferred correction.
        segments,info=model.transcribe(str(audio),language='zh',task='transcribe',beam_size=5,
            temperature=0,condition_on_previous_text=False,word_timestamps=True,vad_filter=False)
        row['segments']=[]
        for segment in segments:
            data=asdict(segment)
            row['segments'].append(data);save()
        row['recognized_text']=''.join(s['text'] for s in row['segments'])
        row['status']='transcribed';row['duration']=info.duration
    except Exception as exc:row['error']=f'{type(exc).__name__}: {exc}'
    row['seconds']=round(time.monotonic()-start,2);save()
    print(json.dumps({k:v for k,v in row.items() if k not in ('segments','case')},ensure_ascii=False))
    if row['status']!='transcribed':raise SystemExit(1)


if __name__=='__main__':main()
