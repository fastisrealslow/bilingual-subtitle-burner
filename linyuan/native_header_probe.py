"""Bounded native dimensions for an exact legacy stream; never media approval."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import urllib.request

MAX_BYTES=1024*1024


def probe(op,url,referer,deadline):
    """Read one CDN prefix, without mirrors, full downloads or remote ffprobe."""
    def supported(target):
        try:
            if not isinstance(target,str):return False
            parsed=urlsplit(target)
            return (parsed.scheme=='https' and (parsed.hostname or '').endswith('.bilivideo.com')
                and not parsed.username and not parsed.password and parsed.port in (None,443))
        except ValueError:return False
    if not supported(url) or not shutil.which('ffprobe'):return None
    stop=min(deadline,time.monotonic()+12)
    if stop-time.monotonic()<1:return None
    request=urllib.request.Request(url,headers={'Referer':referer,'Accept-Encoding':'identity',
        'Range':f'bytes=0-{MAX_BYTES-1}'})
    with op.open(request,timeout=min(3,stop-time.monotonic())) as response:
        if hasattr(response,'geturl') and not supported(response.geturl()):return None
        status=getattr(response,'status',None) or response.getcode()
        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range') or '')
        if (status!=206 or not match or int(match[1])!=0 or not 0<int(match[2])<MAX_BYTES
                or int(match[3])<=int(match[2])):return None
        expected=int(match[2])+1
        length=response.headers.get('Content-Length')
        if length is not None and (not length.isdigit() or int(length)!=expected):return None
        chunks=[];received=0
        while received<expected:
            if time.monotonic()>=stop:raise TimeoutError('Native header byte budget expired')
            chunk=response.read(min(65536,expected-received))
            if not chunk:break
            if len(chunk)>expected-received:return None
            chunks.append(chunk);received+=len(chunk)
    if received!=expected:return None
    data=b''.join(chunks)
    if not (data.startswith(b'FLV') or data[4:8]==b'ftyp'):return None
    left=stop-time.monotonic()
    if left<=0:return None
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'prefix.media';path.write_bytes(data)
        try:
            result=subprocess.run(['ffprobe','-v','error','-protocol_whitelist','file,pipe',
                '-analyzeduration','0','-probesize',str(MAX_BYTES),'-select_streams','v:0',
                '-show_entries','stream=width,height,sample_aspect_ratio:stream_side_data=rotation',
                '-of','json',str(path)],capture_output=True,text=True,timeout=min(5,left))
            if result.returncode:return None
            streams=json.loads(result.stdout).get('streams') or []
            if not isinstance(streams,list) or len(streams)!=1 or not isinstance(streams[0],dict):return None
            stream=streams[0];width,height=stream.get('width'),stream.get('height')
            if (type(width) is not int or type(height) is not int or not 0<min(width,height)<=max(width,height)<=8192
                    or stream.get('sample_aspect_ratio') not in (None,'1:1','0:1')
                    or any(side.get('rotation',0)!=0 for side in stream.get('side_data_list',[]))):return None
        except (ValueError,TypeError,AttributeError,subprocess.TimeoutExpired,OSError):return None
    return dict(width=width,height=height,method='exact_stream_prefix_ffprobe_v1',
        prefix_sha256=hashlib.sha256(data).hexdigest(),bytes_read=received,byte_limit=MAX_BYTES,
        media_approved=False)
