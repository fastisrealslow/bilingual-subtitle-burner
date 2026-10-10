"""Resolve the public official page immediately before its bounded media download."""
import argparse
import hashlib
import html
import json
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


def partial_path(output):
    output=Path(output)
    return output.with_name(output.stem+'.part.mp4')


def media_identity(media,page_url,timeout):
    """Use the server's strong validator, not an expiring URL signature."""
    parsed=urlsplit(media)
    request=urllib.request.Request(media,method='HEAD',headers={
        'User-Agent':'Mozilla/5.0','Referer':page_url})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        headers=response.headers
        etag=headers.get('ETag','')
        size=int(headers.get('Content-Length') or 0)
        if size<=1024 or not etag or etag.startswith('W/') or headers.get('Accept-Ranges','').lower()!='bytes':
            raise ValueError('Official media lacks safe byte-range identity')
    return dict(page_url=page_url,asset_sha256=hashlib.sha256(
        (parsed.scheme+'://'+str(parsed.hostname)+parsed.path).encode()).hexdigest(),
        etag=etag,size=size)


def prepare_partial(output,identity):
    partial=partial_path(output);proof=partial.with_suffix('.identity.json')
    previous=None
    if proof.exists():
        try:previous=json.loads(proof.read_text())
        except (ValueError,OSError):pass
    if partial.exists() and (previous!=identity or partial.stat().st_size>identity['size']):
        # Only this downloader's unverified partial is removed, never a final file.
        partial.unlink()
    proof.write_text(json.dumps(identity)+'\n')
    return partial,proof


def download(page_url,output,budget=600):
    if urlsplit(page_url).hostname not in {'www.yicai.com','yicai.com'}:
        raise ValueError('Expected an official Yicai page')
    if not 1<=budget<=600:raise ValueError('Expected a bounded download budget')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    deadline=time.monotonic()+budget
    for attempt in range(2):
        remaining=deadline-time.monotonic()
        if remaining<=1:break
        refreshed=page_url+('&' if '?' in page_url else '?')+f'_download_at={int(time.time())}'
        request=urllib.request.Request(refreshed,headers={
            'User-Agent':'Mozilla/5.0','Referer':'https://www.yicai.com/',
            'Cache-Control':'no-cache'})
        with urllib.request.urlopen(request,timeout=min(45,remaining)) as response:
            media=extract_video_url(response.read().decode('utf-8'))
        remaining=deadline-time.monotonic()
        if remaining<=1:break
        try:
            identity=media_identity(media,page_url,min(30,remaining))
        except (ValueError,OSError):
            # Keep working full downloads for legacy CDNs without HEAD/ranges;
            # never append bytes whose file identity could not be verified.
            identity=None
        if identity:
            partial,proof=prepare_partial(output,identity)
        else:
            partial=output.with_name(output.stem+'.fresh.mp4');proof=None
            partial.unlink(missing_ok=True)
        if identity and partial.exists() and partial.stat().st_size==identity['size']:
            partial.replace(output);proof.unlink(missing_ok=True);return output
        remaining=min(270,int(deadline-time.monotonic()))
        if remaining<=0:
            break
        print('Downloading official media from',urlsplit(media).hostname,flush=True)
        command=['curl','-fL','--connect-timeout','25','--max-time',str(remaining),
            '--user-agent','Mozilla/5.0','--referer',page_url,
            '--output',str(partial),media]
        if identity:
            command[1:1]=['--continue-at','-','--header','If-Range: '+identity['etag']]
        result=subprocess.run(command,timeout=remaining+10)
        if (result.returncode==0 and partial.exists()
                and (partial.stat().st_size==identity['size'] if identity else partial.stat().st_size>1024)):
            partial.replace(output)
            if proof:proof.unlink(missing_ok=True)
            return output
        # The verified-identity partial survives runner/caller timeouts in cache.
        print('Official media attempt failed; resolving the public page again',flush=True)
    raise TimeoutError('Official media budget exhausted; safe partial retained')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--budget',type=int,default=600)
    args=parser.parse_args()
    download(args.url,args.out,args.budget)


if __name__=='__main__':
    main()
