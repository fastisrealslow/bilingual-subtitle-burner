"""Resolve the public official page immediately before its bounded media download."""
import argparse
import html
from pathlib import Path
import re
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit


def extract_video_url(page):
    match=re.search(r'https?://[^\s"\'<>]+?\.mp4[^\s"\'<>]*',page.replace('\\/','/'),re.I)
    if not match:
        raise ValueError('官方页面没有可下载的 MP4')
    return html.unescape(match.group(0))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    if urlsplit(args.url).hostname not in {'www.yicai.com','yicai.com'}:
        raise SystemExit('Expected an official Yicai page')
    output=Path(args.out);output.parent.mkdir(parents=True,exist_ok=True)
    deadline=time.monotonic()+600
    for attempt in range(2):
        page_url=args.url+('&' if '?' in args.url else '?')+f'_download_at={int(time.time())}'
        request=urllib.request.Request(page_url,headers={
            'User-Agent':'Mozilla/5.0','Referer':'https://www.yicai.com/',
            'Cache-Control':'no-cache'})
        with urllib.request.urlopen(request,timeout=45) as response:
            media=extract_video_url(response.read().decode('utf-8'))
        remaining=min(270,int(deadline-time.monotonic()))
        if remaining<=0:
            break
        print('Downloading official media from',urlsplit(media).hostname,flush=True)
        result=subprocess.run(['curl','-fL','--connect-timeout','25','--max-time',str(remaining),
            '--user-agent','Mozilla/5.0','--referer',args.url,
            '--output',str(output),media],timeout=remaining+10)
        if result.returncode==0 and output.exists() and output.stat().st_size>1024:
            return
        output.unlink(missing_ok=True)
        print('Official media attempt failed; resolving the public page again',flush=True)
    raise SystemExit('官方源在10分钟预算内未下载完成；不占用整批后续产能')


if __name__=='__main__':
    main()
