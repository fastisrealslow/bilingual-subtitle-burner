"""Paired raw-audio experiment. Outputs are hypotheses, NEVER approved captions."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import wave

SOURCE_SHA = '8f626ee870b69646e39c3ed3cd91ae56e286903a2704485dedd4571b37a698ce'
SOURCE_PCM_SHA = '5adffff175cb862d519dbb6f43c269a52b126e3d11ffbd9e777cb70adafcaa4c'
SAMPLES = [(828, 30, 'topic-control'), (1150, 30, 'medical-control'),
           (1167, 36, 'production-original-core'), (1176, 33, 'military-asr-suspect')]
PROFILES = {
    'current': ('Qwen/Qwen3-ASR-0.6B', '5eb144179a02acc5e5ba31e748d22b0cf3e303b0', False),
    'finance-context': ('Qwen/Qwen3-ASR-0.6B', '5eb144179a02acc5e5ba31e748d22b0cf3e303b0', True),
    'larger': ('Qwen/Qwen3-ASR-1.7B', '7278e1e70fe206f11671096ffdd38061171dd6e5', False),
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def save(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def validate_samples(root):
    rows = json.loads((root / 'manifest.json').read_text())
    if not isinstance(rows, list) or len(rows) != len(SAMPLES):
        raise ValueError('Missing paired audio samples')
    for item, (start, duration, ident) in zip(rows, SAMPLES):
        if (item.get('id') != ident or item.get('file') != ident + '.wav'
                or item.get('source_sha256') != SOURCE_SHA
                or item.get('source_pcm_sha256') != SOURCE_PCM_SHA
                or item.get('offset') != start or item.get('duration') != duration
                or sha(root / item['file']) != item.get('sha256')):
            raise ValueError('Paired audio identity changed')
        with wave.open(str(root / item['file'])) as audio:
            if (audio.getframerate() != 16000 or audio.getnchannels() != 1
                    or audio.getsampwidth() != 2
                    or audio.getnframes() != duration * 16000):
                raise ValueError('Unexpected PCM geometry or source duration')
    return rows


def prepare(source, output):
    if sha(source) != SOURCE_SHA:
        raise ValueError('Mother hash differs from the actual failed production source')
    output.mkdir(parents=True, exist_ok=True)
    full = output / 'source-clock.wav'
    from audio_preprocessing import DEFAULT, asr_args
    # First reproduce the production PCM exactly. Segment-local resampling
    # could otherwise confound a comparison with the original ASR failure.
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-vn', '-ar', '16000',
                    *asr_args(DEFAULT), '-c:a', 'pcm_s16le', str(full)], check=True, timeout=600)
    with wave.open(str(full)) as audio:
        pcm = audio.readframes(audio.getnframes())
    if hashlib.sha256(pcm).hexdigest() != SOURCE_PCM_SHA:
        raise ValueError('Full production PCM changed; do not attribute a difference to the ASR model')
    rows = []
    for start, duration, ident in SAMPLES:
        wav = output / (ident + '.wav')
        # Slice the exact samples fed to production; no resampling or filtering here.
        with wave.open(str(wav), 'wb') as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(16000)
            audio.writeframes(pcm[start * 32000:(start + duration) * 32000])
        rows.append(dict(id=ident, file=wav.name, source_sha256=SOURCE_SHA,
                         source_pcm_sha256=SOURCE_PCM_SHA,
                         offset=start, duration=duration, sha256=sha(wav)))
    save(output / 'manifest.json', rows)
    validate_samples(output)


def infer(root, output, profile):
    rows = validate_samples(root)
    model_id, revision, with_context = PROFILES[profile]
    context = ''
    if with_context:
        words = json.loads(Path(__file__).with_name('asr_finance_vocabulary.json').read_text())
        context = '相关词汇：' + '、'.join(words) + '。仅供识别参考，不添加音频中没有的内容。'
    import torch
    from huggingface_hub import snapshot_download
    from qwen_asr import Qwen3ASRModel
    from asr_cpu_benchmark import offline_only
    weights = snapshot_download(model_id, revision=revision)
    torch.set_num_threads(2)
    torch.set_num_interop_threads(1)
    offline_only()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    model = Qwen3ASRModel.from_pretrained(weights, dtype=torch.float32,
        device_map='cpu', max_inference_batch_size=1, max_new_tokens=512)
    report = dict(version=1, profile=profile, source_sha256=SOURCE_SHA,
                  source_pcm_sha256=SOURCE_PCM_SHA,
                  model_id=model_id, model_revision=revision, context=context,
                  device='cpu', threads=2, networking_during_inference=False,
                  model_load_seconds=time.monotonic() - started,
                  audio_verified=False, editorial_approved=False,
                  reference_kind='no human gold reference', results=[])
    save(output / 'results.json', report)
    for item in rows:
        started = time.monotonic()
        result = model.transcribe(audio=str(root / item['file']), language='Chinese', context=context)[0]
        report['results'].append(dict(item, text=result.text,
                                     elapsed_seconds=time.monotonic() - started))
        save(output / 'results.json', report)
        print(json.dumps(report['results'][-1], ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['prepare', 'infer'])
    parser.add_argument('--source', type=Path)
    parser.add_argument('--audio', type=Path, default=Path('failure-audio'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=PROFILES)
    args = parser.parse_args()
    if args.mode == 'prepare':
        if not args.source: parser.error('prepare needs --source')
        prepare(args.source, args.output)
    else:
        if not args.profile: parser.error('infer needs --profile')
        infer(args.audio, args.output, args.profile)


if __name__ == '__main__':
    main()
