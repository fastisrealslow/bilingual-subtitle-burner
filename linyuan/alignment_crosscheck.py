"""Read-only CPU crosscheck of a failed alignment's exact raw audio window.

This produces alternate recognition evidence, never corrected production cues.
The window comes from source-bound model diagnostics, not hand-picked offsets.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import time
import wave


def load_window(audio_path, error):
    with wave.open(str(audio_path)) as stream:
        if (stream.getframerate(),stream.getnchannels(),stream.getsampwidth())!=(16000,1,2):
            raise ValueError('Expected original mono 16 kHz PCM16 evidence')
        pcm=stream.readframes(stream.getnframes())
    if hashlib.sha256(pcm).hexdigest()!=error['source_pcm_sha256']:
        raise ValueError('Diagnostic and actual PCM differ')
    a=float(error['offset']);b=a+float(error['duration'])
    if not 0<=a<b<=len(pcm)/32000:
        raise ValueError('Diagnostic window is outside actual audio')
    return pcm[int(a*16000)*2:int(b*16000)*2]


def crosscheck(audio_path, error_path, weights, out):
    import numpy as np
    from sherpa_onnx import OfflineRecognizer
    error=json.loads(Path(error_path).read_text())
    pcm=load_window(audio_path,error)
    samples=np.frombuffer(pcm,dtype=np.int16).astype(np.float32)/32768
    weights=Path(weights);model=weights/'model.int8.onnx'
    if not model.is_file():model=weights/'model.onnx'
    recognizer=OfflineRecognizer.from_paraformer(paraformer=str(model),tokens=str(weights/'tokens.txt'),
        num_threads=2,sample_rate=16000,feature_dim=80,decoding_method='greedy_search',provider='cpu')
    started=time.monotonic();rows=[]
    middle=len(samples)//2
    for label,lo,hi in [('whole',0,len(samples)),('first_half',0,middle),('second_half',middle,len(samples))]:
        stream=recognizer.create_stream();stream.accept_waveform(16000,samples[lo:hi])
        recognizer.decode_stream(stream)
        rows.append(dict(window=label,offset=error['offset']+lo/16000,duration=(hi-lo)/16000,
                         text=stream.result.text))
    proof=dict(version=1,source_video_sha256=error['source_video_sha256'],
        source_pcm_sha256=error['source_pcm_sha256'],model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
        engine='sherpa-onnx paraformer zh 2024-03-09',threads=2,device='cpu',
        runtime_version=importlib.metadata.version('sherpa-onnx'),
        networking_during_inference=False,production_cues_changed=False,
        original_diagnostic=error,alternate_recognition=rows,elapsed_seconds=round(time.monotonic()-started,2))
    Path(out).write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    return proof


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    for flag in ['audio','error','weights','out']:parser.add_argument('--'+flag,required=True)
    args=parser.parse_args()
    result=crosscheck(args.audio,args.error,args.weights,args.out)
    print(json.dumps(result,ensure_ascii=False))
