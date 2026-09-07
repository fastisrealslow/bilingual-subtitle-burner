"""Replace one reviewed media file in its existing archive; never submit a new post."""
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import zipfile

BVID='BV1HNbw6UEuv'
AID=117227877566035
SLUG='ly-mother-edited-0907-01'
OLD_SHA='3c8d79144c856487a8f1656c407ca856945765683ea8dd3ee7fb5f01dd48ebcf'
MANIFEST='linyuan/.automation/media_revision_BV1HNbw6UEuv.json'


def replacement_payload(data, filename):
    archive=data.get('archive') or data.get('Archive') or {}
    videos=data.get('videos') or []
    if int(archive.get('aid') or 0)!=AID or archive.get('bvid') not in (None,BVID) or len(videos)!=1:
        raise ValueError('Creator archive identity or part count changed')
    if not filename or filename==videos[0].get('filename'):
        raise ValueError('No new media filename')
    payload=copy.deepcopy(archive)
    # The upstream uploader's Studio/Video schema uses filename/title/desc.
    # Omitting the old cid lets the platform attach the new media to this aid.
    payload.update(aid=AID,videos=[dict(filename=filename,
        title=videos[0].get('title') or archive['title'],desc=videos[0].get('desc') or '')])
    return payload


def repair_known_media(event, fc):
    manifest=json.loads(fc.gh('GET',f'/contents/{MANIFEST}?ref=main',raw=True,timeout=20))
    new_sha=manifest.get('new_sha256','')
    if (manifest.get('bvid')!=BVID or manifest.get('old_sha256')!=OLD_SHA
            or not re.fullmatch('[0-9a-f]{64}',new_sha) or new_sha==OLD_SHA):
        raise ValueError('Unrecognized media revision manifest')
    state=fc.load_state()
    receipt=state.get('published',{}).get(SLUG,{})
    parts=receipt.get('parts') or []
    if len(parts)!=1 or parts[0].get('bvid')!=BVID:
        raise ValueError('Original publication receipt unavailable')
    transaction=state.setdefault('media_revisions',{}).get(BVID,{})
    if transaction.get('new_sha256')==new_sha and transaction.get('status')=='verified':
        return dict(bvid=BVID,status='already_verified',new_posts=0)
    if parts[0].get('fingerprints',{}).get('sha256')!=OLD_SHA:
        raise ValueError('A newer file already replaced this revision target')
    if transaction and transaction.get('new_sha256')!=new_sha:
        raise ValueError('Another media revision has an unresolved receipt')

    from biliup.plugins.bili_webup import BiliBili,Data
    from requests.adapters import HTTPAdapter
    cookie=json.loads(os.environ['BILIBILI_COOKIES'])
    entries=(cookie.get('cookie_info') or {}).get('cookies') or cookie.get('cookies') or []
    values={c['name']:str(c['value']) for c in entries}
    if values.get('DedeUserID')!=str(fc.OWNER_MID) or not values.get('bili_jct'):
        raise ValueError('Revision account mismatch')
    logging.getLogger('biliup').setLevel(logging.WARNING)
    client=BiliBili(Data())
    client.login_by_cookies(dict(cookie_info=dict(cookies=entries)))
    session=client._BiliBili__session
    session.mount('https://',HTTPAdapter(max_retries=0))
    def detail():
        reply=session.get('https://member.bilibili.com/x/web/archive/view',
            params=dict(bvid=BVID,history=''),timeout=30).json()
        if reply.get('code')!=0:
            raise RuntimeError('Creator detail unavailable, code='+str(reply.get('code')))
        return reply.get('data') or {}
    before=detail()
    # Validate identity before uploading a single byte.
    replacement_payload(before,'identity-validation-only')
    with tempfile.TemporaryDirectory(prefix='media-repair-') as tmp:
        directory=Path(tmp);archive=directory/'delivery.zip'
        fc.download_reviewed_zip(int(manifest['artifact_id']),archive,attempts=2)
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                name=info.filename
                if ('/' in name or '\\' in name or info.is_dir()
                        or name.startswith(SLUG+'.')):continue
                if name=='meta.json' or name.endswith(('.mp4','.ass','.jpg')):
                    (directory/name).write_bytes(z.read(info))
        meta=json.loads((directory/'meta.json').read_text())
        if not isinstance(meta,dict):raise ValueError('Revision must contain one MP4')
        error=fc.artifact_quality_error(meta) or fc.artifact_subtitle_error(meta,directory)
        if error:raise ValueError(error)
        if not isinstance(meta.get('final'),str) or Path(meta['final']).name!=meta['final']:
            raise ValueError('Invalid revision filename')
        video=directory/meta['final']
        if hashlib.sha256(video.read_bytes()).hexdigest()!=new_sha:
            raise ValueError('Actual revision MP4 differs from the reviewed file')
        if (meta.get('source_sha256')!=parts[0].get('source_sha256')
                or fc.editorial.intervals(meta.get('segments'))!=fc.editorial.intervals(parts[0].get('source_segments'))):
            raise ValueError('Revision changes the existing argument or source ranges')
        if meta.get('junction_frames_checked')!=2:
            raise ValueError('Revision lacks actual join-frame checks')
        error=fc.editorial.metadata_error(meta,fc.mp4_duration(video))
        if error:raise ValueError(error)
        subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(video),'-map','0:v:0','-map','0:a:0',
            '-f','null','-'],check=True,capture_output=True,timeout=180)
        if not transaction:
            uploaded=client.upload_file(str(video),lines='bda2',tasks=1)
            filename=uploaded['filename']
            transaction=dict(new_sha256=new_sha,old_sha256=OLD_SHA,filename=filename,
                status='ready_to_edit',artifact_id=manifest['artifact_id'],created_at=int(time.time()))
            state['media_revisions'][BVID]=transaction;fc.save_state(state)
        filename=transaction['filename']
        current=detail()
        if (current.get('videos') or [{}])[0].get('filename')!=filename:
            if transaction.get('status')!='ready_to_edit':
                return dict(bvid=BVID,status='edit_outcome_pending',new_posts=0)
            payload=replacement_payload(current,filename)
            transaction['status']='edit_requested';fc.save_state(state)
            # No automatic replay: after any uncertain response the next call
            # reads creator state and reuses this exact uploaded filename.
            response=session.post('https://member.bilibili.com/x/vu/web/edit',
                params=dict(csrf=values['bili_jct']),json=payload,timeout=60).json()
            transaction['api_code']=response.get('code');fc.save_state(state)
            if response.get('code')!=0:
                return dict(bvid=BVID,status='edit_rejected',code=response.get('code'),new_posts=0)
            current=detail()
        if (current.get('videos') or [{}])[0].get('filename')!=filename:
            return dict(bvid=BVID,status='edit_outcome_pending',new_posts=0)
        transaction.update(status='verified',verified_at=int(time.time()),
            cid=(current.get('videos') or [{}])[0].get('cid'))
        old=copy.deepcopy(parts[0]['fingerprints'])
        parts[0].setdefault('media_revision_history',[]).append(dict(fingerprints=old,ts=transaction['created_at']))
        parts[0]['fingerprints']=meta['fingerprints']
        parts[0]['media_revision_artifact_id']=manifest['artifact_id']
        receipt['fingerprints']=meta['fingerprints'];receipt['duration_sec']=meta['duration_sec']
        fc.save_state(state)
        fc.log_event('media_repair','已原位更新 '+BVID,'不新增稿件，不增加每日发布数')
        return dict(bvid=BVID,status='verified',new_posts=0,cid=transaction.get('cid'))
