#!/usr/bin/env python3
"""Populate a persistent local mother-video cache from the source manifest."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fetch(records, destination, only=None, fetcher=None):
    """Download missing sources directly; no LLM/VLM/ASR or paid API is used."""
    destination.mkdir(parents=True, exist_ok=True)
    fetcher = fetcher or BASE / 'ci_fetch_bilibili.py'
    results = []
    for record in records:
        if only and record['key'] not in only:
            continue
        final = destination / f"{record['key']}.mp4"
        expected = record['sha256']
        if final.is_file() and sha256(final) == expected:
            results.append({'key': record['key'], 'status': 'reused', 'path': str(final)})
            continue
        partial = destination / f"{record['key']}.part.mp4"
        partial.unlink(missing_ok=True)
        subprocess.run([
            sys.executable, str(fetcher), '--url', record['url'], '--out', str(partial)
        ], check=True)
        actual = sha256(partial)
        if actual != expected:
            partial.unlink(missing_ok=True)
            raise ValueError(f"{record['key']}下载文件哈希不匹配，禁止替换母片")
        partial.replace(final)
        results.append({'key': record['key'], 'status': 'downloaded', 'path': str(final)})
    return {'mode': 'direct-local-source-cache', 'cloud_calls': 0, 'sources': results}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--destination', type=Path, default=BASE / 'local_mothers')
    parser.add_argument('--manifest', type=Path, default=BASE / 'local_source_manifest.json')
    parser.add_argument('--only', action='append')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    result = fetch(manifest['sources'], args.destination, set(args.only or []))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding='utf-8')
    print(text)


if __name__ == '__main__':
    main()
