"""Independent CPU-only recognition evidence; never edits or publishes a clip."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import wave


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--source',required=True)
    p.add_argument('--weights',required=True)
    p.add_argument('--out',required=True)
    args=p.parse_args()
    digest=sha(args.source)
    if digest!='9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345':
        raise ValueError('Diagnostic source differs from the compared Qwen source')
    import numpy as np
    from sherpa_onnx import OfflineRecognizer
    weights=Path(args.weights)
    model=weights/'model.int8.onnx'
    if not model.exists():model=weights/'model.onnx'
    recognizer=OfflineRecognizer.from_paraformer(paraformer=str(model),tokens=str(weights/'tokens.txt'),
        num_threads=2,sample_rate=16000,feature_dim=80,decoding_method='greedy_search',provider='cpu')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    report=dict(source_sha256=digest,model_sha256=sha(model),model_file=model.name,
        engine='sherpa-onnx paraformer zh 2024-03-09',device='cpu',threads=2,
        networking_during_inference=False,segments=[])
    started=time.monotonic()
    for start,end in [(930,1130),(1670,1860)]:
        wav=out/f'original-{start}-{end}.wav'
        subprocess.run(['ffmpeg','-y','-v','error','-ss',str(start),'-i',args.source,
            '-t',str(end-start),'-vn','-ar','16000','-ac','1','-c:a','pcm_s16le',str(wav)],check=True,timeout=120)
        with wave.open(str(wav)) as f:
            audio=np.frombuffer(f.readframes(f.getnframes()),dtype=np.int16).astype(np.float32)/32768
        for offset in range(0,end-start,20):
            lo=max(0,offset-3);hi=min(end-start,offset+23)
            stream=recognizer.create_stream();stream.accept_waveform(16000,audio[lo*16000:hi*16000])
            recognizer.decode_stream(stream);r=stream.result
            row=dict(start=start+lo,end=start+hi,core_start=start+offset,
                core_end=min(end,start+offset+20),text=r.text,tokens=list(r.tokens),timestamps=list(r.timestamps))
            report['segments'].append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)
            report['elapsed_seconds']=round(time.monotonic()-started,2)
            (out/'paraformer-evidence.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
