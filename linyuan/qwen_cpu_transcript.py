"""CPU-only ASR and independent word alignment, with raw overlap evidence.

Recognition and alignment run in separate processes to release model memory.
No transcript is accepted merely because a newer model produced it.
"""
import argparse
import hashlib
import json
import math
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


IDENTITY_FIELDS=('version','source_pcm_sha256','source_video_sha256','model_id',
                 'model_revision','device','threads','networking_during_inference',
                 'core_range','duration')
CHUNK_FIELDS=('offset','duration','core_start','core_end','text')


def resume_recognition(report, checkpoint):
    """Reuse only a contiguous prefix from this exact audio and model."""
    try:
        old=json.loads(Path(checkpoint).read_text())
        if any(old.get(k)!=report.get(k) for k in IDENTITY_FIELDS):return 0
        first,last=report['core_range'];duration=report['duration'];chunks=[]
        for chunk in old['chunks']:
            core=first+len(chunks)*CORE_SECONDS
            a=max(0,core-OVERLAP_SECONDS);b=min(duration,core+CORE_SECONDS+OVERLAP_SECONDS)
            if (core>=last or chunk['core_start']!=core or chunk['core_end']!=min(core+CORE_SECONDS,last)
                    or chunk['offset']!=a or chunk['duration']!=b-a or not isinstance(chunk['text'],str)):
                return 0
            chunks.append({k:chunk[k] for k in CHUNK_FIELDS})
        report['chunks']=chunks
        return len(chunks)
    except (OSError,ValueError,KeyError,TypeError):
        return 0


def resume_alignment(report, checkpoint, revision):
    """Copy valid aligned words, never an incomplete transcript approval."""
    from qwen_asr_evidence import content
    try:
        old=json.loads(Path(checkpoint).read_text());alignment=old.get('alignment') or {}
        if any(old.get(k)!=report.get(k) for k in IDENTITY_FIELDS):return 0
        expected=dict(model_id='Qwen/Qwen3-ForcedAligner-0.6B',model_revision=revision,
                      device='cpu',threads=2,networking_during_inference=False)
        if any(alignment.get(k)!=v for k,v in expected.items()):return 0
        if len(old['chunks'])!=len(report['chunks']):return 0
        restored=[]
        for current,prior in zip(report['chunks'],old['chunks']):
            if any(current.get(k)!=prior.get(k) for k in CHUNK_FIELDS):return 0
            if 'words' not in prior:continue
            words=prior['words'];a=current['offset'];b=a+current['duration']
            if (not isinstance(words,list) or content(''.join(w['text'] for w in words))!=content(current['text'])
                    or any(not math.isfinite(w['start']+w['end']) or not a<=w['start']<=w['end']<=b+.1 for w in words)
                    or any(x['start']>y['start'] for x,y in zip(words,words[1:]))):
                continue
            restored.append((current,words))
        for current,words in restored:current['words']=words
        if restored:report['alignment']={**alignment,**expected}
        return len(restored)
    except (OSError,ValueError,KeyError,TypeError):
        return 0


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
    partial=output.parent/'_partial_qwen_cpu'
    if args.mode=='decode':
        report={'version':1,'source_pcm_sha256':pcm_sha,
                'source_video_sha256':args.source_video_sha or (SOURCE_VIDEO_SHA if args.part is not None else None),
                'model_id':model_id,'model_revision':weights.name,'device':'cpu','threads':2,
                'networking_during_inference':False,'core_range':[first,last],'duration':duration,'chunks':[]}
        resumed=resume_recognition(report,partial/'recognition.json')
        print(json.dumps({'resumed_recognition_cores':resumed}),flush=True)
        save(report_path,report)
        if report['chunks'] and report['chunks'][-1]['core_end']==last:return
        from qwen_asr import Qwen3ASRModel
        model=Qwen3ASRModel.from_pretrained(str(weights),dtype=torch.float32,
            device_map='cpu',max_inference_batch_size=1,max_new_tokens=512)
        for core in range(first+resumed*CORE_SECONDS,int(last)+1,CORE_SECONDS):
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
        report=json.loads(report_path.read_text())
        resumed=resume_alignment(report,partial/'aligned.json',weights.name)
        print(json.dumps({'resumed_alignment_cores':resumed}),flush=True)
        if resumed:save(output/'aligned.json',report)
        if resumed and all('words' in c for c in report['chunks']):return
        from qwen_asr import Qwen3ForcedAligner
        model=Qwen3ForcedAligner.from_pretrained(str(weights),dtype=torch.float32,device_map='cpu')
        for chunk in report['chunks']:
            if 'words' in chunk:continue
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
