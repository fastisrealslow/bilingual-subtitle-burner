"""CI upload on the measured QN route, with a definite-before-submit fallback.

Only a positive publication BVID completes an upload. Lost submit responses
remain pending for the existing authenticated archive reconciliation.
"""
import base64
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
import io
import json
import logging
from pathlib import Path
import re
import signal
import subprocess
import time

from upload_transport import RemoteUploadPending


class UploadDeadlineExpired(RuntimeError):
    """Not TimeoutError: the SDK catches and retries that exception internally."""


class UploadNotSubmitted(RuntimeError):
    """Positive evidence that no archive creation was accepted on this route."""


def checksum(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):h.update(block)
    return h.hexdigest()


def command_values(cmd):
    i=cmd.index('upload')
    video=Path(cmd[i+1]);cookie=Path(cmd[cmd.index('-u')+1])
    tail=cmd[i+2:]
    if len(tail)%2:raise ValueError('Invalid upload argument pairs')
    values={}
    allowed={'--title','--tid','--copyright','--source','--desc','--tag','--limit','--submit','--cover'}
    for flag,value in zip(tail[::2],tail[1::2]):
        if flag not in allowed or flag in values:raise ValueError('Unexpected upload option')
        values[flag]=str(value)
    if (set(values)!=allowed or values['--tid']!='207' or values['--copyright']!='3'
            or values['--submit']!='web' or values['--limit']!='1'):
        raise ValueError('Upload does not preserve the approved publication policy')
    return video,cookie,values


@contextmanager
def deadline(seconds):
    old=signal.getsignal(signal.SIGALRM)
    def expired(*_):raise UploadDeadlineExpired('CI upload deadline exceeded')
    signal.signal(signal.SIGALRM,expired)
    signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:
        signal.setitimer(signal.ITIMER_REAL,0)
        signal.signal(signal.SIGALRM,old)


def sdk():
    # Same pinned uploader used by the successful upload-only route probes.
    from biliup.plugins.bili_webup import BiliBili,Data
    data=Data()
    return BiliBili(data),data


def execute(cmd,expected_sha256,owner_mid):
    started=time.monotonic();submission_entered=False
    old_disable=logging.root.manager.disable
    try:
        video,cookie_path,values=command_values(cmd)
        if not re.fullmatch('[a-f0-9]{64}',str(expected_sha256 or '')) or checksum(video)!=expected_sha256:
            raise ValueError('Actual video checksum differs from the reviewed delivery')
        payload=json.loads(cookie_path.read_text())
        entries=(payload.get('cookie_info') or {}).get('cookies') or payload.get('cookies') or []
        cookies={c['name']:str(c['value']) for c in entries}
        if cookies.get('DedeUserID')!=str(owner_mid) or not cookies.get('bili_jct'):
            raise ValueError('Uploader account identity mismatch')
        budget=min(600,max(90,video.stat().st_size/(500*1024)))
        with deadline(budget),redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            logging.disable(logging.CRITICAL)
            client,data=sdk()
            from requests.adapters import HTTPAdapter
            session=client._BiliBili__session
            session.mount('https://',HTTPAdapter(max_retries=0))
            client.login_by_cookies(cookies)
            owner=session.get('https://api.bilibili.com/x/web-interface/nav',timeout=10).json()
            if owner.get('code')!=0 or str((owner.get('data') or {}).get('mid'))!=str(owner_mid):
                raise ValueError('Authenticated uploader is not the intended owner')
            uploaded=client.upload_file(str(video),lines='qn',tasks=1)
            if not isinstance(uploaded,dict) or not uploaded.get('filename'):
                raise ValueError('Upload-only stage has no complete filename')
            if checksum(video)!=expected_sha256:
                raise ValueError('Reviewed video changed during transfer')
            # Preserve the exact approved image. The SDK's cover_up method
            # crops to 16:10, which invalidates the delivered cover proof.
            cover_bytes=Path(values['--cover']).read_bytes()
            cover=session.post('https://member.bilibili.com/x/vu/web/cover/up',
                data={'cover':b'data:image/jpeg;base64,'+base64.b64encode(cover_bytes),
                      'csrf':cookies['bili_jct']},timeout=30).json()
            cover_url=(cover.get('data') or {}).get('url')
            if cover.get('code')!=0 or not isinstance(cover_url,str) or not cover_url.startswith(('http://','https://','//')):
                raise ValueError('Approved cover upload was not confirmed')
            uploaded['title']=values['--title'][:80]
            data.append(uploaded)
            data.title=values['--title'];data.desc=values['--desc'];data.tag=values['--tag']
            data.tid=207;data.copyright=3;data.source=values['--source'];data.cover=cover_url
            submission_entered=True
            # One explicit web submit. Do not let the SDK switch to another
            # API or retry a request after a lost/ambiguous response.
            response=client.submit_web()
            info=response.get('data') or {}
            bvid=info.get('bvid')
            if response.get('code')==21138 and not info.get('bvid') and not info.get('aid'):
                # The pinned upstream uploader explicitly changes API after
                # this "web unsupported" rejection. It accepted no archive.
                raise UploadNotSubmitted('web_submit_explicitly_unsupported')
            if response.get('code')!=0 or not re.fullmatch(r'BV[a-zA-Z0-9]{10}',str(bvid or '')):
                pending=RemoteUploadPending('CI submit needs authenticated archive reconciliation')
                code=response.get('code')
                pending.safe_evidence=dict(backend='github-qn',phase='submit_response',
                    response_code=code if type(code) is int else None,
                    response_parsed=True,video_sha256=expected_sha256,checked_at=int(time.time()))
                raise pending
        process=subprocess.CompletedProcess(cmd,0,bvid,'')
        process.upload_metrics=dict(backend='github-qn',upload_called=True,fc_invocations=0,
            video_sha256=expected_sha256,bvid=bvid,total_seconds=round(time.monotonic()-started,3),
            completed_at=int(time.time()))
        return process
    except (UploadNotSubmitted,RemoteUploadPending):raise
    except Exception as exc:
        if submission_entered:
            pending=RemoteUploadPending('CI submit outcome is unknown: '+type(exc).__name__)
            pending.safe_evidence=dict(backend='github-qn',phase='submit_response',
                response_parsed=False,error_type=type(exc).__name__,
                video_sha256=expected_sha256,checked_at=int(time.time()))
            raise pending from exc
        raise UploadNotSubmitted(type(exc).__name__) from exc
    finally:
        logging.disable(old_disable)
