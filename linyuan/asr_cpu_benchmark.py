"""Reproducible CPU-only ASR comparison on production audio; never calls an ASR API.

prepare downloads our existing artifacts. infer downloads public weights once,
then disconnects Python networking before loading and decoding local files.
Results are hypotheses, not gold transcripts or a measured character error rate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import socket
import subprocess
import time
import urllib.request
import wave
import zipfile


SOURCES = [
    (9987024252, 'linyuan-verified-live.mp4', 'interview', [0, 23, 47], 24),
    (9986057664, '_tmp/audio_16k.wav', 'shareholder', [0, 31, 76], 24),
    (9986607829, 'final_1.mp4', 'event1', [0, 21, 42], 20),
    (9986607829, 'final_2.mp4', 'event2', [15, 55], 24),
    (9986607829, 'final_3.mp4', 'event3', [75], 24),
]


def write_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    manifest = []
    for artifact, member, name, starts, duration in SOURCES:
        archive = root / f'{artifact}.zip'
        if not archive.exists():
            # curl strips Authorization when following a redirect to another host.
            subprocess.run(['curl', '-fsSL', '--retry', '3', '--max-time', '180',
                '-H', 'Authorization: Bearer ' + os.environ['GH_TOKEN'],
                f'https://api.github.com/repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{artifact}/zip',
                '-o', str(archive)], check=True, timeout=600)
        source = root / (name + Path(member).suffix)
        with zipfile.ZipFile(archive) as z:
            source.write_bytes(z.read(member))
        for index, start in enumerate(starts):
            wav = root / f'{name}-{index+1:02d}.wav'
            subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', str(start),
                '-i', str(source), '-t', str(duration), '-vn', '-ar', '16000',
                '-ac', '1', '-c:a', 'pcm_s16le', str(wav)], check=True, timeout=60)
            with wave.open(str(wav)) as w:
                seconds = w.getnframes() / w.getframerate()
            manifest.append(dict(id=wav.stem, file=wav.name, artifact=artifact,
                member=member, offset=start, duration=seconds,
                sha256=hashlib.sha256(wav.read_bytes()).hexdigest()))
        source.unlink()
    for archive in root.glob('*.zip'):
        archive.unlink()
    write_json(root / 'manifest.json', manifest)


def download_weights(backend, root):
    from huggingface_hub import snapshot_download, hf_hub_download
    if backend == 'qwen3-0.6b':
        return Path(snapshot_download('Qwen/Qwen3-ASR-0.6B'))
    model_id = {
        'sensevoice-int8': 'csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17',
        'paraformer-int8': 'csukuangfj/sherpa-onnx-paraformer-zh-2023-03-28',
    }[backend]
    root.mkdir(parents=True, exist_ok=True)
    for filename in ['model.int8.onnx', 'tokens.txt']:
        hf_hub_download(model_id, filename, local_dir=root)
    return root


def offline_only():
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    def denied(*args, **kwargs):
        raise RuntimeError('Network disabled during local ASR inference')
    socket.socket.connect = denied
    socket.create_connection = denied


def infer(args):
    import numpy as np
    args.output.mkdir(parents=True, exist_ok=True)
    weights = download_weights(args.backend, args.output / 'weights')
    offline_only()
    started = time.perf_counter()
    if args.backend == 'qwen3-0.6b':
        import torch
        from qwen_asr import Qwen3ASRModel
        torch.set_num_threads(args.threads)
        torch.set_num_interop_threads(1)
        model = Qwen3ASRModel.from_pretrained(str(weights), dtype=torch.float32,
            device_map='cpu', max_inference_batch_size=1, max_new_tokens=256)
        def decode(wav):
            result = model.transcribe(audio=str(wav), language='Chinese')[0]
            return dict(text=result.text, tokens=[], timestamps=[])
    else:
        from sherpa_onnx import OfflineRecognizer
        kwargs = dict(model=str(weights/'model.int8.onnx'), tokens=str(weights/'tokens.txt'),
                      num_threads=args.threads, provider='cpu')
        if args.backend == 'sensevoice-int8':
            model = OfflineRecognizer.from_sense_voice(**kwargs, use_itn=True)
        else:
            model = OfflineRecognizer.from_paraformer(**kwargs)
        def decode(wav):
            with wave.open(str(wav)) as w:
                sr = w.getframerate()
                audio = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)/32768
            stream = model.create_stream()
            stream.accept_waveform(sr, audio)
            model.decode_stream(stream)
            return dict(text=stream.result.text, tokens=list(stream.result.tokens),
                        timestamps=list(stream.result.timestamps))
    report = dict(backend=args.backend, device='cpu', threads=args.threads,
        model_load_seconds=time.perf_counter()-started, platform=platform.platform(),
        cpu_count=os.cpu_count(), cpu_info=Path('/proc/cpuinfo').read_text().split('\n')[:12],
        networking_during_inference=False, reference_kind='no human gold reference', results=[])
    manifest = json.loads((args.audio/'manifest.json').read_text())
    for item in manifest:
        start = time.perf_counter()
        try:
            result = decode(args.audio/item['file'])
            result.update(item, seconds=time.perf_counter()-start, status='ok')
            result['rtf'] = result['seconds']/item['duration']
        except Exception as exc:
            result = dict(item, status='error', error=f'{type(exc).__name__}: {exc}',
                          seconds=time.perf_counter()-start)
        result['peak_rss_mb'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
        report['results'].append(result)
        write_json(args.output/'results.json', report)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    subprocess.run(['python', '-m', 'pip', 'freeze'], stdout=(args.output/'requirements.txt').open('w'), check=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['prepare', 'infer'])
    p.add_argument('--backend', choices=['sensevoice-int8', 'paraformer-int8', 'qwen3-0.6b'])
    p.add_argument('--audio', type=Path, default=Path('benchmark-audio'))
    p.add_argument('--output', type=Path, default=Path('benchmark-result'))
    p.add_argument('--threads', type=int, default=2)
    args = p.parse_args()
    prepare(args.audio) if args.mode == 'prepare' else infer(args)
