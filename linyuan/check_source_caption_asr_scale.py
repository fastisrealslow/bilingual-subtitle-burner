"""Read-only CPU ASR comparison on exact production PCM and chunk boundaries.

A visible original subtitle at 277.9s says 地球; old ASR says 地市. This
visual reference is not human audio gold, and is not passed to either model.

Neither a more fluent transcript nor agreement is an accuracy measurement.
There is no human audio gold here. No cue corrections, alignment, stock writes,
source network requests or publishing are performed by this diagnostic.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import wave
import zipfile

SOURCE_SHA = '6f5ddecc6db4f2045e37a83f63a7d3a122f08287ee1258e9c6f085abdb2b9c2d'
PCM_SHA = 'f2e8c382f3914aafd584ebb8b608c337258c16306c5cb526ba1420a371151dac'
CORES = (0, 240, 270)
MODELS = {
    '0.6B': ('Qwen/Qwen3-ASR-0.6B', '5eb144179a02acc5e5ba31e748d22b0cf3e303b0'),
    '1.7B': ('Qwen/Qwen3-ASR-1.7B', '7278e1e70fe206f11671096ffdd38061171dd6e5'),
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)


def cut_pcm(wav, chunk, target):
    with wave.open(str(wav)) as stream:
        if (stream.getframerate(), stream.getnchannels(), stream.getsampwidth()) != (16000, 1, 2):
            raise ValueError('Unexpected production PCM format')
        first = round(chunk['offset'] * 16000)
        length = round(chunk['duration'] * 16000)
        stream.setpos(first)
        samples = stream.readframes(length)
        if len(samples) != length * 2:
            raise ValueError('Incomplete source chunk')
    with wave.open(str(target), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(samples)
    return hashlib.sha256(samples).hexdigest()


def prepare(root, mother, old_asr):
    from audio_preprocessing import policy, asr_args, clock_proof
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(old_asr) as archive:
        reports = json.loads(archive.read('asr_raw_chunks.json'))
    if not reports or any(r['source_video_sha256'] != SOURCE_SHA or
                          r['source_pcm_sha256'] != PCM_SHA or
                          r['model_id'] != MODELS['0.6B'][0] or
                          r['model_revision'] != MODELS['0.6B'][1] or
                          r['audio_preprocessing'] != 'ffmpeg-source-clock-v2' or
                          r.get('recognition_context', {}) for r in reports):
        raise ValueError('Actual baseline identity differs from this trial')
    source = root / 'mother.mp4'
    matches = []
    with zipfile.ZipFile(mother) as archive:
        for member in archive.namelist():
            if not member.lower().endswith('.mp4'):
                continue
            with archive.open(member) as src, source.open('wb') as dst:
                import shutil
                shutil.copyfileobj(src, dst)
            if sha(source) == SOURCE_SHA:
                matches.append(member)
                break
    if not matches or sha(source) != SOURCE_SHA:
        raise ValueError('Exact raw mother source missing; never substitute a render')
    wav = root / 'full-source.wav'
    audio_policy = policy(SOURCE_SHA)
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-vn', '-ar', '16000',
                    *asr_args(audio_policy), '-c:a', 'pcm_s16le', str(wav)], check=True, timeout=600)
    with wave.open(str(wav)) as stream:
        duration = stream.getnframes() / stream.getframerate()
        pcm_sha = hashlib.sha256(stream.readframes(stream.getnframes())).hexdigest()
    if pcm_sha != PCM_SHA:
        raise ValueError('Decoded PCM differs from actual production; no comparison permitted')
    clock = clock_proof(source, duration, audio_policy)
    manifest = []
    for core in CORES:
        chunks = [c for r in reports for c in r['chunks'] if c['core_start'] == core]
        if len(chunks) != 1:
            raise ValueError('Missing or ambiguous actual baseline chunk')
        chunk = chunks[0]
        name = f'core-{core}.wav'
        digest = cut_pcm(wav, chunk, root / name)
        manifest.append(dict(file=name, pcm_sha256=digest, wav_sha256=sha(root / name),
            core_start=core, offset=chunk['offset'], duration=chunk['duration'],
            actual_production_text=chunk['text']))
    # The independent complete selected audio window tests whether the small
    # model's chunk context caused this specific visible-caption mismatch.
    # This is diagnostic audio only, never a production range or approval.
    selected=dict(offset=254.76,duration=26.96)
    name='core-selected-25476.wav'
    digest=cut_pcm(wav,selected,root/name)
    manifest.append(dict(file=name,pcm_sha256=digest,wav_sha256=sha(root/name),
        core_start='selected-25476',offset=254.76,duration=26.96,
        actual_production_text=None,window_kind='complete_selected_audio_probe'))
    # Only small diagnostic PCM windows are retained in the evidence artifact.
    wav.unlink(); source.unlink()
    save(root / 'manifest.json', dict(source_sha256=SOURCE_SHA, source_pcm_sha256=pcm_sha,
        source_run_id=39989833395, mother_artifact_id=12422073680,
        raw_asr_artifact_id=12518730520, clock_proof=clock, samples=manifest))


def infer(root, size):
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from qwen_asr import Qwen3ASRModel
    from asr_cpu_benchmark import offline_only
    from qwen_cpu_transcript import retry_repetitive_transcript
    model_id, revision = MODELS[size]
    torch.set_num_threads(2); torch.set_num_interop_threads(1)
    weights = snapshot_download(model_id, revision=revision)
    manifest = json.loads((root / 'manifest.json').read_text())
    offline_only()
    started = time.monotonic()
    result = dict(model_id=model_id, model_revision=revision, device='cpu', dtype='float32',
        threads=2, source_sha256=manifest['source_sha256'], context='',
        networking_during_inference=False, diagnostic_only=True, human_audio_gold=False,
        registered_stock=False, published=False, samples=[])
    path = root / f'result-{size}.json'; save(path, result)
    model = Qwen3ASRModel.from_pretrained(weights, dtype=torch.float32, device_map='cpu',
        max_inference_batch_size=1, max_new_tokens=512)
    result['model_load_seconds'] = time.monotonic() - started
    for sample in manifest['samples']:
        wav = root / sample['file']
        if sha(wav) != sample['wav_sha256']:
            raise ValueError('Diagnostic window mutated')
        with wave.open(str(wav)) as stream:
            pcm = stream.readframes(stream.getnframes())
        if hashlib.sha256(pcm).hexdigest() != sample['pcm_sha256']:
            raise ValueError('Diagnostic PCM mutated')
        audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768
        started = time.monotonic()
        text = model.transcribe(audio=(audio, 16000), language='Chinese', context='')[0].text
        text, repair = retry_repetitive_transcript(model, audio, 16000, text, context='')
        seconds = time.monotonic() - started
        result['samples'].append(dict(**sample, text=text, repetition_repair=repair,
            seconds=seconds, real_time_factor=seconds / sample['duration'],
            peak_rss_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
        save(path, result)
        print(json.dumps(dict(size=size, core=sample['core_start'], seconds=seconds), ensure_ascii=False), flush=True)
    result['complete'] = True; save(path, result)


def compare(root):
    # Separate processes release ASR memory. No editorial model is loaded here.
    outcomes = {}
    for size in MODELS:
        try:
            run = subprocess.run([sys.executable, __file__, 'infer', '--root', str(root),
                '--size', size], timeout=900, check=False)
            outcomes[size] = dict(exit_code=run.returncode)
        except subprocess.TimeoutExpired:
            outcomes[size] = dict(error='bounded CPU inference timeout')
    reports = {size: json.loads((root / f'result-{size}.json').read_text())
               for size in MODELS if (root / f'result-{size}.json').exists()}
    complete = all(outcomes[k].get('exit_code') == 0 and reports.get(k, {}).get('complete')
                   for k in MODELS)
    save(root / 'comparison.json', dict(complete=complete, outcomes=outcomes,
        source_sha256=SOURCE_SHA, diagnostic_only=True, human_audio_gold=False,
        quality_improvement_proven=False, production_model_changed=False,
        registered_stock=False, published=False))
    if not complete:
        raise RuntimeError('ASR comparison incomplete; inspect both bounded process results')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'infer', 'compare'])
    parser.add_argument('--root', type=Path, default=Path('source-caption-asr-trial'))
    parser.add_argument('--mother', type=Path)
    parser.add_argument('--old-asr', type=Path)
    parser.add_argument('--size', choices=MODELS)
    args = parser.parse_args()
    if args.mode == 'prepare':
        try:
            prepare(args.root, args.mother, args.old_asr)
        except Exception as exc:
            args.root.mkdir(parents=True, exist_ok=True)
            save(args.root / 'preparation-failure.json', dict(complete=False, diagnostic_only=True,
                phase='exact_source_pcm_preparation', error_type=type(exc).__name__, error=str(exc),
                production_model_changed=False, registered_stock=False, published=False))
            raise
    elif args.mode == 'infer': infer(args.root, args.size)
    else: compare(args.root)
