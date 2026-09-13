"""Recover checked mother media when the shared Actions cache evicts it.

An artifact is a download checkpoint, never a quality approval. The regular
fetcher reselects the current representation and the source gate runs again.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


FILES = ('video.mp4', 'video.mp4.source.json')


def verified(directory, source_url):
    directory = Path(directory)
    video, manifest = (directory / name for name in FILES)
    try:
        saved = json.loads(manifest.read_text())
        if (saved['identity']['source'] != source_url
                or saved['size'] != video.stat().st_size or saved['size'] <= 0):
            return False
        with video.open('rb') as stream:
            return saved['sha256'] == hashlib.file_digest(stream, 'sha256').hexdigest()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def transfer(origin, target, source_url):
    origin, target = Path(origin), Path(target)
    if not verified(origin, source_url):
        return False
    target.mkdir(parents=True, exist_ok=True)
    # No old source-gate, title, subtitle or identity approval can be restored.
    for name in FILES:
        temporary = target / (name + '.restore')
        shutil.copyfile(origin / name, temporary)
        temporary.replace(target / name)
    return True


def restore(source_url, target, repo):
    if verified(target, source_url):
        print('Mother media: validated download cache already present')
        return True
    name = 'mother-media-' + hashlib.sha256(source_url.encode()).hexdigest()
    try:
        result = subprocess.run(['gh', 'api',
            f'repos/{repo}/actions/artifacts?name={name}&per_page=5'],
            check=True, capture_output=True, text=True, timeout=60)
        rows = json.loads(result.stdout)['artifacts']
        rows = sorted((r for r in rows if r.get('name') == name and not r.get('expired')),
                      key=lambda r: r['id'], reverse=True)
        for row in rows[:2]:
            with tempfile.TemporaryDirectory(prefix='mother-media-') as directory:
                try:
                    subprocess.run(['gh', 'run', 'download', str(row['workflow_run']['id']),
                        '--repo', repo, '--name', name, '--dir', directory], check=True,
                        capture_output=True, timeout=300)
                    if transfer(directory, target, source_url):
                        print('Mother media: restored source-bound artifact; quality checks still required')
                        return True
                except (subprocess.SubprocessError, OSError, ValueError, KeyError):
                    continue
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        pass
    print('Mother media: no valid artifact; use normal resumable source download')
    return False


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', default='_src')
    args = parser.parse_args()
    restore(args.source, args.target, os.environ['GITHUB_REPOSITORY'])
