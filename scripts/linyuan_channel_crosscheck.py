"""Compare raw mono/left speech on fixed windows; never edit or approve cues."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from audio_signal_probe import probe

EXPECTED = '753efbca9d8f40551bf32b7b02a74c9185be33ab411499e91571b4547f530da0'
REVISION = '5eb144179a02acc5e5ba31e748d22b0cf3e303b0'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--out',type=Path,default=Path('channel-crosscheck'))
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    with args.source.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
    if digest!=EXPECTED:raise ValueError('Source differs from failed library311; never substitute bytes')
    report=dict(source_sha256=digest,run_id=os.environ.get('GITHUB_RUN_ID'),
        commit=os.environ.get('GITHUB_SHA'),signal=probe(args.source),rows=[],
        model='Qwen/Qwen3-ASR-0.6B',revision=REVISION,
        editorial_approved=False,source_accepted=False,subtitles_modified=False)
    path=args.out/'comparison.json'
    def save():path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    save()
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from asr_cpu_benchmark import offline_only
    from qwen_asr import Qwen3ASRModel
    weights=snapshot_download(report['model'],revision=REVISION)
    torch.set_num_threads(2);torch.set_num_interop_threads(1)
    offline_only()
    model=Qwen3ASRModel.from_pretrained(weights,dtype=torch.float32,
        device_map='cpu',max_inference_batch_size=1,max_new_tokens=384)
    for start in (240,1198,2156):
        for channel in ('mono','left'):
            audio=args.out/f'{start}-{channel}.wav'
            filtering=['-af','pan=mono|c0=c0'] if channel=='left' else []
            subprocess.run(['ffmpeg','-v','error','-y','-ss',str(start),'-i',str(args.source),
                '-t','18','-vn',*filtering,'-ar','16000','-ac','1','-c:a','pcm_s16le',str(audio)],
                check=True,timeout=60)
            import wave
            with wave.open(str(audio)) as wav:pcm=wav.readframes(wav.getnframes())
            begun=time.monotonic()
            text=model.transcribe(audio=(np.frombuffer(pcm,dtype='<i2').astype(np.float32)/32768,16000),
                language='Chinese')[0].text
            row=dict(start=start,duration=len(pcm)/32000,channel=channel,
                pcm_sha256=hashlib.sha256(pcm).hexdigest(),text=text,
                seconds=round(time.monotonic()-begun,2),audio_file=audio.name)
            report['rows'].append(row);save();print(json.dumps(row,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
