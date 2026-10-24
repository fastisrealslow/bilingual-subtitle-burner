"""Download actual media, never a preview image or a stale glob match."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit

from ci_fetch_bilibili import validate_media

# Some extractors expose scrubber images as playable formats (#706).
# Keep the existing resolution preference, excluding image formats in every branch.
FORMAT = ('(bv*[width<=?1920][height<=1920]+ba/b[width<=?1920][height<=1920]/b)'
          '[ext!=jpg][ext!=jpeg][ext!=png][ext!=webp][ext!=gif][ext!=mhtml]')
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.mhtml'}


def run_download(command, *, timeout, check=True, stderr=subprocess.PIPE, text=True):
    """Stop the whole downloader/merger group before the workflow timeout."""
    process=subprocess.Popen(command,start_new_session=True,stderr=stderr,text=text)
    try:
        output,error=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.communicate()
        raise TimeoutError('下载预算耗尽，保留未完成下载供后续续传') from None
    if check and process.returncode:
        raise subprocess.CalledProcessError(process.returncode,command,output=output,stderr=error)
    return subprocess.CompletedProcess(command,process.returncode,output,error)


def normalize_small_unpaired_tail(media, max_trim=5.0, minimum_kept=120.0):
    """Drop a small unmatched A/V tail without padding or re-encoding.

    Some single-file social-media MP4s legitimately end one track a few
    seconds before the other.  Rejecting before packet-tail validation makes
    those sources unrecoverable.  We only repair a bounded tail on an otherwise
    long source, then run the full media validator again on the new bytes.
    """
    media=Path(media)
    probe=subprocess.run([
        'ffprobe','-v','error','-show_entries','stream=codec_type,duration',
        '-of','json',str(media),
    ],capture_output=True,text=True,timeout=60,check=True)
    streams=json.loads(probe.stdout).get('streams') or []
    durations={}
    for row in streams:
        if row.get('codec_type') not in ('video','audio') or row.get('codec_type') in durations:
            continue
        try:durations[row['codec_type']]=float(row.get('duration') or 0)
        except (TypeError,ValueError):pass
    if set(durations)!= {'video','audio'}:
        raise RuntimeError('无法核对待修复文件的音视频时长')
    kept=min(durations.values());trim=abs(durations['video']-durations['audio'])
    if kept<minimum_kept or not 0<trim<=max_trim:
        raise RuntimeError(f'音视频尾部差异不在安全修复范围：保留{kept:.2f}s，裁切{trim:.2f}s')
    temp=media.with_name(media.stem+'.tail-normalized.mp4')
    temp.unlink(missing_ok=True)
    try:
        subprocess.run([
            'ffmpeg','-y','-loglevel','error','-i',str(media),
            '-map','0:v:0','-map','0:a:0','-c','copy','-shortest',
            '-movflags','+faststart',str(temp),
        ],check=True,timeout=300)
        validate_media(temp)
        temp.replace(media)
    finally:
        temp.unlink(missing_ok=True)
    return dict(original_durations=durations,trimmed_tail_sec=trim,kept_duration_sec=kept)


def fetch(url, directory, failure_report, *, budget_sec=3300):
    if not 1<=budget_sec<=3600:raise ValueError('Expected a bounded download budget')
    deadline=time.monotonic()+budget_sec
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'downloaded-path.txt'
    manifest.unlink(missing_ok=True)
    try:
        origin = None
        download_url, extra_args = url, []
        if urlsplit(url).hostname=='www.p5w.net':
            from p5w_source import resolve
            origin=resolve(url)
            download_url=origin['media_url']
            extra_args=['--referer',url]
        from ci_fetch_ifeng import ARTICLE_HOSTS
        if urlsplit(url).hostname in ARTICLE_HOSTS:
            from ci_fetch_ifeng import fetch as fetch_ifeng
            media=fetch_ifeng(url,directory/'video.mp4')
            origin=Path(failure_report).parent/'source_origin.json'
            origin.parent.mkdir(parents=True,exist_ok=True)
            origin.write_text(media.with_suffix('.origin.json').read_text())
            return media
        from ci_fetch_netease import PAGE_HOSTS
        if urlsplit(url).hostname in PAGE_HOSTS:
            from ci_fetch_netease import resolve
            origin = resolve(url)
            download_url = origin['media_url']
            extra_args = ['--referer', url]
        remaining=deadline-time.monotonic()
        if remaining<=0:raise TimeoutError('取源元数据已耗尽下载预算，未启动媒体下载')
        run_download([
            sys.executable, '-m', 'yt_dlp', '--no-playlist', '--continue',
            '--socket-timeout', '120', '--retries', '10', '--fragment-retries', '10',
            '--concurrent-fragments', '4', '-f', FORMAT, '--merge-output-format', 'mp4',
            '--format-sort', 'res:1080',
            '--print-to-file', 'after_move:filepath', str(manifest),
            '-o', str(directory / 'video.%(ext)s'), *extra_args, download_url,
        ], check=True, stderr=subprocess.PIPE, text=True,timeout=remaining)
        paths = manifest.read_text().splitlines()
        if len(paths) != 1:
            raise ValueError('下载器没有返回唯一的实际媒体路径')
        media = Path(paths[0]).resolve()
        if media.parent != directory or media.suffix.lower() in IMAGE_EXTENSIONS:
            raise ValueError('下载结果不是本次目录中的音视频文件，拒绝预览图')
        try:
            valid = validate_media(media)
        except RuntimeError as exc:
            if not re.search(r'合流后音视频时长漂移 [0-9.]+s',str(exc)):
                raise
            proof=normalize_small_unpaired_tail(media)
            print('[取源修复] 已裁去无配对尾部：'+json.dumps(proof,ensure_ascii=False),flush=True)
            valid = validate_media(media)
        if origin:
            if abs(valid['duration'] - origin['declared_duration']) > 3:
                provider='全景网' if origin.get('provider')=='p5w' else '网易'
                raise ValueError(provider+'实际媒体时长与正文完整清单不符')
            from ci_fetch_bilibili import sha256_file
            with media.open('rb') as handle:
                origin['sha256'] = sha256_file(handle)
            report = Path(failure_report).parent/'source_origin.json'
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(origin, ensure_ascii=False, indent=2)+'\n')
        return media
    except Exception as exc:
        report = Path(failure_report)
        report.parent.mkdir(parents=True, exist_ok=True)
        # Unsupported extractors cannot recover by retrying the identical URL.
        # Keep unknown/HTTP failures retryable; never infer a permanent ban from
        # a timeout or missing format. Persist a fixed reason, not signed URLs
        # or potentially credential-bearing downloader stderr.
        unsupported = (isinstance(exc, subprocess.CalledProcessError)
                       and re.search(r'(?im)^ERROR:\s*Unsupported URL:',
                                     exc.stderr or '') is not None)
        reason = ('下载器不支持此素材地址，需更换来源或接通专用下载器'
                  if unsupported else f'取源未完成：{type(exc).__name__}: {exc}')
        report.write_text(json.dumps(dict(passed=False, retryable=not unsupported,
            failure_stage='source-fetch', source_url=url,
            reason=reason), ensure_ascii=False, indent=2))
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--failure-report', required=True)
    parser.add_argument('--github-output', required=True)
    parser.add_argument('--budget-seconds',type=int,default=3300)
    args = parser.parse_args()
    media = fetch(args.url, args.directory, args.failure_report,budget_sec=args.budget_seconds)
    with open(args.github_output, 'a') as output:
        output.write(f'path={media}\n')


if __name__ == '__main__':
    main()
