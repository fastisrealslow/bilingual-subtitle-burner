#!/usr/bin/env python3
"""Report everything missing before a CPU-only production run; never uses network."""
import argparse
import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

BASE = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def inspect(source_root, asr_cache_root, manifest):
    commands = {name: bool(shutil.which(name)) for name in (
        'ffmpeg', 'ffprobe', 'yt-dlp', 'ollama')}
    modules = {name: bool(importlib.util.find_spec(name)) for name in (
        'sherpa_onnx', 'torch', 'transformers')}
    sources = []
    for record in manifest.get('sources', []):
        path = source_root / f"{record['key']}.mp4"
        cached_asr_name = record.get('cached_asr')
        cached_asr = asr_cache_root / cached_asr_name if cached_asr_name else None
        present = path.is_file()
        digest = sha256(path) if present else None
        sources.append({
            'key': record['key'],
            'url': record['url'],
            'video_path': str(path),
            'video_present': present,
            'sha256_matches': digest == record['sha256'] if present else False,
            'cached_asr_path': str(cached_asr) if cached_asr else None,
            'cached_asr_present': cached_asr.is_file() if cached_asr else False,
        })
    source_ready = sum(x['video_present'] and x['sha256_matches'] for x in sources)
    cached_asr_ready = sum(x['cached_asr_present'] for x in sources)
    render_ready = commands['ffmpeg'] and commands['ffprobe']
    local_text_ready = commands['ollama']
    asr_runtime_ready = modules['sherpa_onnx'] or (modules['torch'] and modules['transformers'])
    return {
        'mode': 'local-offline-preflight',
        'cloud_calls': 0,
        'commands': commands,
        'python_modules': modules,
        'sources': sources,
        'summary': {
            'source_videos_ready': source_ready,
            'source_videos_total': len(sources),
            'cached_asr_ready': cached_asr_ready,
            'cached_asr_total': len(sources),
            'render_ready': render_ready,
            'local_text_ready': local_text_ready,
            'asr_runtime_ready': asr_runtime_ready,
            'full_production_ready': bool(
                render_ready and local_text_ready and source_ready == len(sources)),
        },
        'note': 'yt-dlp只用于免费素材下载；是否安装不影响对已缓存母片的离线生产。',
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('source_root', type=Path)
    parser.add_argument('--asr-cache-root', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, default=BASE / 'local_source_manifest.json')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    result = inspect(args.source_root, args.asr_cache_root, manifest)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
