"""CPU-only ASR and independent word alignment, with raw overlap evidence.

Recognition and alignment run in separate processes to release model memory.
No transcript is accepted merely because a newer model produced it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import time
import wave

CORE_SECONDS=30
OVERLAP_SECONDS=3
SOURCE_PCM_SHA='8a5b7284ffcdb6b6c5b1e080db9346b70f51663bcb45d5780bfb1ba90e9a2ceb'
SOURCE_VIDEO_SHA='9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345'


def save(path, data):
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    temp.replace(path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['decode','align'])
    parser.add_argument('--audio',required=True)
    parser.add_argument('--part',type=int,choices=[0,1,2])
    parser.add_argument('--weights')
    parser.add_argument('--source-video-sha')
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from asr_cpu_benchmark import offline_only
    torch.set_num_threads(2);torch.set_num_interop_threads(1)
    output=Path(args.out);output.mkdir(parents=True,exist_ok=True)
    with wave.open(args.audio) as w:
        sr=w.getframerate();channels=w.getnchannels();pcm=w.readframes(w.getnframes())
    pcm_sha=hashlib.sha256(pcm).hexdigest()
    if sr!=16000 or channels!=1 or (args.part is not None and pcm_sha!=SOURCE_PCM_SHA):
        raise ValueError('Audio samples differ from the verified source interview')
    audio=np.frombuffer(pcm,np.int16).astype(np.float32)/32768
    duration=len(audio)/sr
    first=args.part*630 if args.part is not None else 0
    last=min(first+630,duration) if args.part is not None else duration
    model_id='Qwen/Qwen3-ASR-0.6B' if args.mode=='decode' else 'Qwen/Qwen3-ForcedAligner-0.6B'
    weights=Path(args.weights) if args.weights else Path(snapshot_download(model_id))
    if not weights.is_dir():
        raise ValueError('Local offline model directory is missing')
    offline_only()
    started=time.monotonic()
    report_path=output/'recognition.json'
    if args.mode=='decode':
        from qwen_asr import Qwen3ASRModel
        model=Qwen3ASRModel.from_pretrained(str(weights),dtype=torch.float32,
            device_map='cpu',max_inference_batch_size=1,max_new_tokens=512)
        report={'version':1,'source_pcm_sha256':pcm_sha,
                'source_video_sha256':args.source_video_sha or (SOURCE_VIDEO_SHA if args.part is not None else None),
                'model_id':model_id,'model_revision':weights.name,'device':'cpu','threads':2,
                'networking_during_inference':False,'core_range':[first,last],'duration':duration,'chunks':[]}
        for core in range(first,int(last)+1,CORE_SECONDS):
            if core>=last:break
            a=max(0,core-OVERLAP_SECONDS);b=min(duration,core+CORE_SECONDS+OVERLAP_SECONDS)
            text=model.transcribe(audio=(audio[int(a*sr):int(b*sr)],sr),language='Chinese')[0].text
            report['chunks'].append({'offset':a,'duration':b-a,'core_start':core,
                'core_end':min(core+CORE_SECONDS,last),'text':text})
            report['elapsed_seconds']=round(time.monotonic()-started,2)
            report['peak_rss_mb']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
            save(report_path,report)
            print(json.dumps({'core':core,'text':text,'elapsed':report['elapsed_seconds']},ensure_ascii=False),flush=True)
    else:
        from qwen_asr import Qwen3ForcedAligner
        model=Qwen3ForcedAligner.from_pretrained(str(weights),dtype=torch.float32,device_map='cpu')
        report=json.loads(report_path.read_text())
        for chunk in report['chunks']:
            a=chunk['offset'];b=a+chunk['duration']
            words=model.align(audio=(audio[int(a*sr):int(b*sr)],sr),text=chunk['text'],language='Chinese')[0]
            chunk['words']=[{'text':w.text,'start':float(w.start_time)+a,'end':float(w.end_time)+a} for w in words]
            if any(not a<=w['start']<=w['end']<=b+.1 for w in chunk['words']):
                raise ValueError('Forced alignment produced invalid word timing')
            report['alignment']={'model_id':model_id,'model_revision':weights.name,
                'device':'cpu','threads':2,'networking_during_inference':False,
                'elapsed_seconds':round(time.monotonic()-started,2),
                'peak_rss_mb':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024}
            save(output/'aligned.json',report)
            print(json.dumps({'aligned_core':chunk['core_start'],'words':len(words)},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
