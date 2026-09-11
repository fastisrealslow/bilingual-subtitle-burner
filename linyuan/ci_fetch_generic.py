"""Download actual media, never a preview image or a stale glob match."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from ci_fetch_bilibili import validate_media

# Some extractors expose scrubber images as playable formats (#706).
# Keep the existing resolution preference, excluding image formats in every branch.
FORMAT = ('(bv*[height<=1080]+ba/b[height<=1080]/b)'
          '[ext!=jpg][ext!=jpeg][ext!=png][ext!=webp][ext!=gif][ext!=mhtml]')
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.mhtml'}


def fetch(url, directory, failure_report):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'downloaded-path.txt'
    manifest.unlink(missing_ok=True)
    try:
        subprocess.run([
            sys.executable, '-m', 'yt_dlp', '--no-playlist', '--continue',
            '--socket-timeout', '120', '--retries', '10', '--fragment-retries', '10',
            '--concurrent-fragments', '4', '-f', FORMAT, '--merge-output-format', 'mp4',
            '--print-to-file', 'after_move:filepath', str(manifest),
            '-o', str(directory / 'video.%(ext)s'), url,
        ], check=True)
        paths = manifest.read_text().splitlines()
        if len(paths) != 1:
            raise ValueError('下载器没有返回唯一的实际媒体路径')
        media = Path(paths[0]).resolve()
        if media.parent != directory or media.suffix.lower() in IMAGE_EXTENSIONS:
            raise ValueError('下载结果不是本次目录中的音视频文件，拒绝预览图')
        validate_media(media)
        return media
    except Exception as exc:
        report = Path(failure_report)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(dict(passed=False, retryable=True,
            failure_stage='source-fetch', source_url=url,
            reason=f'取源未完成：{type(exc).__name__}: {exc}'), ensure_ascii=False, indent=2))
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--failure-report', required=True)
    parser.add_argument('--github-output', required=True)
    args = parser.parse_args()
    media = fetch(args.url, args.directory, args.failure_report)
    with open(args.github_output, 'a') as output:
        output.write(f'path={media}\n')


if __name__ == '__main__':
    main()
