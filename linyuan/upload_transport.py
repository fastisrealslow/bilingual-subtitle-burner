"""Exact, single-use transport for a delivery already reviewed by the publisher.

The CI publisher owns the existing shared pipeline lease and all admission,
slot, experiment and deduplication gates. FC only transfers these exact bytes
and runs the existing domestic uploader. Unknown outcomes are never replayed.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.error
import zipfile

from artifact_range import delivery_files

VERSION = 1
TRIGGER = 'upload-reviewed-only'
TAG = 'reviewed-upload-transport'
MAX_BYTES = 2 * 1024**3
START_WINDOW = 15 * 60
# An old runtime treats an unknown trigger as a publisher. Its first guard
# rejects this permanently paused slug before reading state or uploading.
LEGACY_SENTINEL = 'ly-0907-aba5c6'


class RemoteUploadPending(RuntimeError):
    """The remote process may still be alive; retain the full upload intent."""


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def pack(directory, part, submission, destination):
    """Include only the selected delivery files; never parent folders/cookies."""
    directory, destination = Path(directory), Path(destination)
    names = delivery_files(part)
    manifest = dict(version=VERSION, part=part, submission=submission,
                    files={n: digest(directory/n) for n in names})
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_STORED) as archive:
        archive.writestr('manifest.json', canonical(manifest))
        for name in names:
            path = directory/name
            if path.is_symlink() or not path.is_file():
                raise ValueError('Delivery is not a regular file')
            archive.write(path, name)
    if destination.stat().st_size > MAX_BYTES:
        raise ValueError('Upload bundle exceeds bounded transport size')
    return manifest


def unpack(bundle, directory):
    directory = Path(directory)
    with zipfile.ZipFile(bundle) as archive:
        entries = archive.infolist()
        if (len(entries)>32 or sum(e.file_size for e in entries)>MAX_BYTES
                or len({e.filename for e in entries})!=len(entries)):
            raise ValueError('Invalid upload bundle size or duplicate members')
        for e in entries:
            if (Path(e.filename).name!=e.filename or '\\' in e.filename
                    or e.filename in {'.','..'} or e.is_dir()
                    or stat.S_ISLNK(e.external_attr>>16) or e.flag_bits & 1):
                raise ValueError('Invalid upload bundle member')
        if archive.getinfo('manifest.json').file_size>2*1024**2:
            raise ValueError('Manifest exceeds bound')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('version')!=VERSION:
            raise ValueError('Unknown upload protocol')
        names=delivery_files(manifest['part'])
        if (set(archive.namelist())!={'manifest.json',*names}
                or set(manifest['files'])!=set(names)):
            raise ValueError('Upload bundle file contract mismatch')
        for name in names:
            with archive.open(name) as src, (directory/name).open('wb') as dst:
                shutil.copyfileobj(src,dst,1024**2)
            if digest(directory/name)!=manifest['files'][name]:
                raise ValueError('Upload bundle member checksum mismatch')
    return manifest


def validate_intent(fc, event, manifest, now=None):
    now=time.time() if now is None else now
    submission=manifest['submission']
    task_id=event.get('task_id')
    if (not re.fullmatch(r'ly-upload-[a-zA-Z0-9-]{8,100}',str(task_id or ''))
            or task_id!=submission.get('task_id')
            or event.get('manifest_sha256')!=hashlib.sha256(canonical(manifest)).hexdigest()
            or not 0<=now-float(submission.get('created_at') or 0)<START_WINDOW):
        raise ValueError('Unverified or expired upload identity')
    state=fc.load_state()
    entry=next((e for e in reversed(state.get('dispatched',[]))
                if e.get('slug')==submission.get('slug')),None)
    intent=(entry or {}).get('upload_receipt_intent') or {}
    binding=(entry or {}).get('upload_transport') or {}
    part=manifest['part']
    if (not entry or not entry.get('uploading') or entry.get('publication_quarantined')
            or submission.get('slug') in fc.REVIEW_PAUSED_SLUGS
            or binding.get('task_id')!=task_id
            or binding.get('bundle_sha256')!=event.get('bundle_sha256')
            or binding.get('manifest_sha256')!=event.get('manifest_sha256')
            or intent.get('title')!=submission.get('title')
            or intent.get('slug')!=submission.get('slug')
            or intent.get('part_index')!=submission.get('part_index')
            or intent.get('artifact_id')!=submission.get('artifact_id')
            or intent.get('source_sha256')!=part.get('source_sha256')
            or intent.get('source_segments')!=part.get('segments')
            or intent.get('fingerprints')!=part.get('fingerprints')
            or submission.get('copyright')!=fc.COPYRIGHT or submission.get('tid')!=fc.TID
            or (part.get('fingerprints') or {}).get('sha256')!=manifest['files'][part['final']]):
        raise ValueError('Upload is not bound to the durable reviewed intent')
    from source_admission_guard import restriction
    if restriction(entry.get('source_url') or '',[]):
        raise ValueError('Source is restricted')
    return entry


def worker(fc,event):
    """No source scans, slot selection, log commits or publication ledger writes."""
    if event.get('probe') is True:
        return dict(protocol=VERSION,upload_called=False)
    started=time.monotonic()
    attempted=False
    result=dict(protocol=VERSION,task_id=event.get('task_id'),upload_called=False)
    try:
        task_id=str(event.get('task_id') or '')
        if not re.fullmatch(r'ly-upload-[a-zA-Z0-9-]{8,100}',task_id):
            raise ValueError('Invalid transport task ID')
        # One durable CAS claim per task. An ambiguous PUT or a duplicate task
        # never enters the uploader, even if a caller bypasses SDK idempotency.
        import base64
        claim_path='linyuan/.automation/upload_tasks/'+task_id+'.json'
        try:
            old=fc.gh('GET','/contents/'+claim_path+'?ref=main',timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code!=404:raise
            old=None
        if old:
            receipt=json.loads(base64.b64decode(old['content']))
            if receipt.get('result'):
                return receipt['result']
            raise RemoteUploadPending('Transport task already claimed')
        claim=fc.gh('PUT','/contents/'+claim_path,dict(message='chore(upload): claim reviewed transfer',
            content=base64.b64encode(canonical(dict(task_id=task_id,claimed_at=int(time.time())))).decode()),timeout=30)
        claim_sha=(claim.get('content') or {}).get('sha')
        if not claim_sha:raise RemoteUploadPending('Transport claim was not confirmed')
        with tempfile.TemporaryDirectory(prefix='reviewed-upload-') as tmp:
            tmp=Path(tmp)
            # Only the exact public Release in this repository. No arbitrary
            # URLs or forwarded GitHub credentials on cross-host redirects.
            prefix=f'https://github.com/{fc.REPO}/releases/download/{TAG}/'
            url=str(event.get('bundle_url') or '')
            name=url.removeprefix(prefix)
            if not url.startswith(prefix) or not re.fullmatch(r'ly-upload-[a-zA-Z0-9-]+\.zip',name):
                raise ValueError('Untrusted upload bundle location')
            import requests
            bundle=tmp/'bundle.zip'
            deadline=time.monotonic()+720
            with requests.get(url,stream=True,timeout=(10,30)) as response:
                response.raise_for_status()
                with bundle.open('wb') as out:
                    count=0
                    for chunk in response.iter_content(1024**2):
                        count+=len(chunk)
                        if count>MAX_BYTES or time.monotonic()>deadline:
                            raise TimeoutError('Upload transport download exceeded bound')
                        out.write(chunk)
            if digest(bundle)!=event.get('bundle_sha256'):
                raise ValueError('Upload transport checksum mismatch')
            manifest=unpack(bundle,tmp)
            entry=validate_intent(fc,event,manifest)
            part,submission=manifest['part'],manifest['submission']
            video=tmp/part['final']
            error=(fc.editorial.metadata_error(part,fc.mp4_duration(video))
                   or fc.artifact_quality_error(part)
                   or fc.artifact_subtitle_error(part,tmp)
                   or fc.artifact_cover_error(part,tmp))
            if error:raise ValueError('Reviewed upload quality gate rejected: '+error)
            if int(part.get('presentation_version') or 0)<int(entry.get('required_presentation_version') or 0):
                raise ValueError('Presentation proof predates current requirements')
            cookies=tmp/'cookies.json'
            cookies.write_text(fc.COOKIES_JSON)
            cookies.chmod(0o600)
            import sys,os
            for path in ('/opt/python','/opt/python/lib/python3.10/site-packages','/code/python'):
                if Path(path).exists() and path not in sys.path:sys.path.insert(0,path)
            import biliup  # fail before marking an upload attempt
            env=os.environ.copy();env['PYTHONPATH']=':'.join(sys.path)
            cmd=[sys.executable,'-m','biliup','-u',str(cookies),'upload',str(video),
                 '--title',submission['title'],'--tid',str(fc.TID),'--copyright',str(fc.COPYRIGHT),
                 '--source',submission['source'],'--desc',submission['desc'],
                 '--tag',submission['tags'],'--limit','1','--submit','web',
                 '--cover',str(tmp/part['cover'])]
            # The shared publisher lease/intent may have changed during a slow
            # download. Check again immediately before the only side effect.
            validate_intent(fc,event,manifest)
            prepared=time.monotonic()
            attempted=True
            process=subprocess.run(cmd,capture_output=True,text=True,timeout=1620,env=env)
            matches=set(re.findall(r'BV[a-zA-Z0-9]{10}',(process.stdout or '')+(process.stderr or '')))
            result.update(upload_called=True,returncode=process.returncode,
                bvid=next(iter(matches)) if process.returncode==0 and len(matches)==1 else None,
                video_sha256=digest(video),completed_at=int(time.time()),
                transfer_review_seconds=round(prepared-started,3),
                upload_seconds=round(time.monotonic()-prepared,3))
    except RemoteUploadPending:
        raise
    except Exception as exc:
        # A timeout after CLI entry is ambiguous. Do not include cookies, raw
        # uploader output or signed transport URLs in public logs/artifacts.
        result.update(upload_called=attempted,error_type=type(exc).__name__,
                      completed_at=int(time.time()),returncode=-1)
    result['total_seconds']=round(time.monotonic()-started,3)
    if 'claim_sha' in locals() and claim_sha:
        try:
            fc.gh('PUT','/contents/'+claim_path,dict(message='chore(upload): record transfer outcome',
                sha=claim_sha,content=base64.b64encode(canonical(dict(task_id=task_id,result=result))).decode()),timeout=30)
        except Exception:
            # The async task result remains authoritative evidence. A failed
            # receipt write must never cause another attempt.
            pass
    result['total_seconds']=round(time.monotonic()-started,3)
    return result
