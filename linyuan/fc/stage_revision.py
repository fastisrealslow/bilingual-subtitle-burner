"""Replace the reported portrait incident in place, with durable edit receipts."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile
from reviewed_updates import creator_detail,payload_for,matches

BVID='BV1CLYd6LEWE'
SLUG='ly-0911-171705'
OLD_SHA='b8c5e65b0b4e59fbe18d42a3dfa104af1d46042ddf4e04ec6425d14d1ef64e9b'
SOURCE_SHA='81833e8aa7224716bd682b91e486ffcab5f4f2f4dc6fc00f8d523b08c59067dd'
MANIFEST='linyuan/.automation/stage_revision_BV1CLYd6LEWE.json'
STATE_KEY='stage_revision_0912'


def apply(fc):
    item=json.loads(fc.gh('GET',f'/contents/{MANIFEST}?ref=main',raw=True,timeout=30))
    if (item.get('bvid')!=BVID or item.get('slug')!=SLUG or item.get('old_video_sha256')!=OLD_SHA
            or item.get('source_sha256')!=SOURCE_SHA or item.get('batch')!='wide-stage-20260912'):
        raise ValueError('Unrecognized stage repair manifest')
    state=fc.load_state();transactions=state.setdefault(STATE_KEY,{})
    txn=transactions.get(BVID,{})
    if txn.get('status')=='verified':
        if txn.get('video_sha256')!=item['video_sha256']:raise ValueError('A different repair is already verified')
        return dict(status='already_verified',bvid=BVID,new_posts=0)
    receipt=state.get('published',{}).get(SLUG,{})
    rows=[p for p in receipt.get('parts',[]) if p.get('bvid')==BVID]
    if len(rows)!=1 or rows[0].get('fingerprints',{}).get('sha256')!=OLD_SHA:
        raise ValueError('Original video receipt changed')
    part=rows[0]
    def persist():
        fc.save_state(state)
        if fc.load_state().get(STATE_KEY)!=transactions:raise RuntimeError('Repair receipt did not persist')
    with tempfile.TemporaryDirectory(prefix='stage-revision-') as tmp:
        root=Path(tmp);archive=root/'delivery.zip'
        fc.download_reviewed_zip(int(item['artifact_id']),archive,attempts=2)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                n=entry.filename
                if '/' in n or '\\' in n or entry.is_dir():continue
                if n=='meta.json' or n.endswith(('.mp4','.ass','.jpg')):(root/n).write_bytes(z.read(entry))
        meta=json.loads((root/'meta.json').read_text())
        if meta.get('title')!=item['title'] or meta.get('render_mode')!='stage_context':raise ValueError('Repair packaging changed')
        for key in ['final','cover']:
            if Path(meta[key]).name!=meta[key]:raise ValueError('Unsafe repair filename')
        video=root/meta['final'];cover=root/meta['cover']
        if (hashlib.sha256(video.read_bytes()).hexdigest()!=item['video_sha256']
                or hashlib.sha256(cover.read_bytes()).hexdigest()!=item['cover_sha256']):
            raise ValueError('Repair media bytes differ from manifest')
        if (meta.get('source_sha256')!=SOURCE_SHA
                or fc.editorial.intervals(meta.get('segments'))!=fc.editorial.intervals(part.get('source_segments'))):
            raise ValueError('Repair changes original source interval')
        error=(fc.artifact_quality_error(meta) or fc.artifact_subtitle_error(meta,root)
               or fc.artifact_cover_error(meta,root) or fc.editorial.metadata_error(meta,fc.mp4_duration(video)))
        if error:raise ValueError(error)
        subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(video),'-map','0:v:0','-map','0:a:0','-f','null','-'],check=True,capture_output=True,timeout=180)
        from biliup.plugins.bili_webup import BiliBili,Data
        from requests.adapters import HTTPAdapter
        cookie=json.loads(os.environ['BILIBILI_COOKIES'])
        entries=(cookie.get('cookie_info') or {}).get('cookies') or cookie.get('cookies') or []
        values={c['name']:str(c['value']) for c in entries}
        if values.get('DedeUserID')!=str(fc.OWNER_MID) or not values.get('bili_jct'):raise ValueError('Wrong repair account')
        client=BiliBili(Data());client.login_by_cookies(dict(cookie_info=dict(cookies=entries)))
        session=client._BiliBili__session;session.mount('https://',HTTPAdapter(max_retries=0))
        current=creator_detail(session,BVID);payload_for(current,item,'validation-only')
        if not txn:
            txn=dict(status='prepared',original_archive=copy.deepcopy(current),title=item['title'],
                video_sha256=item['video_sha256'],payload_schema_version=3,created_at=int(time.time()))
            transactions[BVID]=txn;persist()
        if txn['video_sha256']!=item['video_sha256']:raise ValueError('Another repair is in progress')
        if not txn.get('cover'):
            reply=session.post('https://member.bilibili.com/x/vu/web/cover/up',
                data=dict(cover='data:image/jpeg;base64,'+base64.b64encode(cover.read_bytes()).decode(),csrf=values['bili_jct']),timeout=30).json()
            url=(reply.get('data') or {}).get('url')
            if reply.get('code')!=0 or not url:raise RuntimeError('Repair cover upload rejected')
            txn['cover']=url;persist()
        if not txn.get('filename'):
            txn['filename']=client.upload_file(str(video),lines='bda2',tasks=1)['filename'];persist()
        current=creator_detail(session,BVID)
        if not matches(current,{**item,'video':meta['final']},txn):
            if txn['status'] in ('edit_requested','edit_rejected'):
                return dict(status=txn['status'],bvid=BVID,new_posts=0)
            if (current.get('videos') or [{}])[0].get('filename')!=(txn['original_archive'].get('videos') or [{}])[0].get('filename'):
                raise ValueError('Archive media changed during repair')
            payload=payload_for(current,item,txn['cover'],txn['filename'])
            payload['desc']=meta['desc']
            if 'desc_v2' in payload:payload.pop('desc_v2')
            txn['status']='edit_requested';persist()
            reply=session.post('https://member.bilibili.com/x/vu/web/edit',params=dict(csrf=values['bili_jct']),json=payload,timeout=60).json()
            txn['api_code']=reply.get('code');txn['api_message']=str(reply.get('message') or '')[:200]
            if reply.get('code')!=0:txn['status']='edit_rejected'
            persist();current=creator_detail(session,BVID)
        if not matches(current,{**item,'video':meta['final']},txn):return dict(status=txn['status'],bvid=BVID,new_posts=0)
        part.setdefault('editorial_revision_history',[]).append(dict(title=part.get('title'),fingerprints=copy.deepcopy(part.get('fingerprints')),ts=txn['created_at']))
        for target in (part,receipt):
            target.update(title=meta['title'],fingerprints=meta['fingerprints'],resolution=meta['resolution'],render_mode='stage_context',duration_sec=meta['duration_sec'])
        txn.update(status='verified',verified_at=int(time.time()),cid=(current.get('videos') or [{}])[0].get('cid'),archive_state=(current.get('archive') or {}).get('state'))
        persist();fc.log_event('media_repair','已原位修复舞台画面、标题与封面 '+BVID,meta['title'])
        return dict(status='verified',bvid=BVID,new_posts=0,cid=txn['cid'],archive_state=txn.get('archive_state'))
