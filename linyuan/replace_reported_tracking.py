"""One authorized archive edit, protected by durable receipts and the publisher lease."""
import argparse
import copy
import json
import logging
import os
from pathlib import Path
import sys
import time

from tracking_revision_contract import BVID, SLUG, OLD_SHA, SOURCE_SHA, TITLE, sha
try:
    from reviewed_updates import creator_detail, payload_for
except ModuleNotFoundError:
    from fc.reviewed_updates import creator_detail, payload_for

AID = 117438213591310
OLD_CID = 42732880203
STATE_KEY = 'tracking_revision_20261015'


def checked_archive(data, filename=None):
    archive=data.get('archive') or data.get('Archive') or {}
    videos=data.get('videos') or []
    if (archive.get('aid') != AID or archive.get('bvid') != BVID
            or archive.get('title') != TITLE or len(videos) != 1):
        raise ValueError('Creator archive identity/title/part count changed')
    if filename is None and videos[0].get('cid') != OLD_CID:
        raise ValueError('Original media was already changed')
    return archive, videos[0]


def apply(fc, directory, client, csrf, validator):
    directory=Path(directory);meta=json.loads((directory/'meta.json').read_text())
    if (meta.get('title') != TITLE or meta.get('source_sha256') != SOURCE_SHA
            or (meta.get('tracking_revision') or {}).get('old_sha256') != OLD_SHA):
        raise ValueError('Unexpected repair bundle')
    error=validator(meta,directory)
    if error: raise ValueError(error)
    video=directory/meta['final'];new_sha=sha(video)
    state=fc.load_state();receipt=state.get('published',{}).get(SLUG) or {}
    rows=[p for p in receipt.get('parts',[]) if p.get('bvid') == BVID]
    if len(rows)!=1 or rows[0].get('source_sha256') != SOURCE_SHA:
        raise ValueError('Exact original publication receipt unavailable')
    part=rows[0];transactions=state.setdefault(STATE_KEY,{})
    txn=transactions.get(BVID) or {}
    if txn and txn.get('new_sha256') != new_sha:raise ValueError('Another repair is in progress')
    if part.get('fingerprints',{}).get('sha256') not in {OLD_SHA,new_sha}:
        raise ValueError('Publication media fingerprint changed')
    if fc.editorial.intervals(meta['segments']) != fc.editorial.intervals(part['source_segments']):
        raise ValueError('Repair changes source speech interval')
    session=client._BiliBili__session
    def persist():
        if fc.save_state(state) is False:raise RuntimeError('Repair transaction did not persist')
        if fc.load_state().get(STATE_KEY,{}).get(BVID) != txn:
            raise RuntimeError('Repair transaction readback mismatch')
    current=creator_detail(session,BVID)
    archive,previous=checked_archive(current, txn.get('filename'))
    if txn.get('status')=='verified':
        if previous.get('filename') != txn.get('filename'):raise ValueError('Verified replacement changed')
        return dict(status='already_verified',bvid=BVID,cid=previous.get('cid'),new_posts=0)
    if not txn:
        txn=dict(status='prepared',old_sha256=OLD_SHA,new_sha256=new_sha,
            old_cid=OLD_CID,original_archive=copy.deepcopy(current),created_at=int(time.time()),
            workflow_run_id=os.environ.get('GITHUB_RUN_ID'),new_posts=0)
        transactions[BVID]=txn;persist()
    if not txn.get('filename'):
        if txn['status']=='upload_requested':
            prior=txn.get('workflow_run_id')
            run=fc.gh('GET',f'/actions/runs/{int(prior)}',timeout=20) if str(prior).isdigit() else {}
            if run.get('status')!='completed':
                return dict(status='awaiting_prior_upload',bvid=BVID,new_posts=0)
            # A finished pre-edit worker cannot submit later. No filename was
            # saved and the exact original CID is still present: another media
            # upload may leave unused bytes, but can never duplicate a post.
            checked_archive(current)
            txn.setdefault('abandoned_upload_attempts',[]).append(dict(
                workflow_run_id=prior,conclusion=run.get('conclusion'),no_edit_requested=True))
            txn['status']='prepared';persist()
        txn['status']='upload_requested';persist()
        uploaded=client.upload_file(str(video), lines='bda2', tasks=1)
        if not uploaded.get('filename'):raise ValueError('Upload did not return a filename')
        txn.update(filename=uploaded['filename'],status='ready_to_edit');persist()
    current=creator_detail(session,BVID);archive,previous=checked_archive(current, txn['filename'])
    if previous.get('filename') != txn['filename']:
        if txn['status'] in {'edit_requested','edit_rejected'}:
            return dict(status='edit_outcome_pending',bvid=BVID,new_posts=0)
        if previous.get('filename') != txn['original_archive']['videos'][0].get('filename'):
            raise ValueError('Archive media changed while preparing repair')
        item=dict(bvid=BVID,title=TITLE,old_title=TITLE)
        payload=payload_for(current,item,archive['cover'],txn['filename'])
        payload['desc']=meta['desc'];payload.pop('desc_v2',None)
        # The creator detail's season is navigation state, never move/remove it.
        if archive.get('season_id'):payload['season_id']=archive['season_id']
        if archive.get('section_id'):payload['section_id']=archive['section_id']
        txn['status']='edit_requested';persist()
        try:
            reply=session.post('https://member.bilibili.com/x/vu/web/edit',
                params=dict(csrf=csrf),json=payload,timeout=60).json()
            txn['api_code']=reply.get('code')
            if reply.get('code')!=0:txn['status']='edit_rejected'
            persist()
        except Exception:
            # Do not retry the POST after an ambiguous response.
            txn['last_edit_response']='unknown';persist()
    current=creator_detail(session,BVID);archive,updated=checked_archive(current,txn['filename'])
    if updated.get('filename') != txn['filename']:
        return dict(status='edit_outcome_pending',bvid=BVID,new_posts=0)
    old_archive=txn['original_archive'].get('archive') or txn['original_archive'].get('Archive') or {}
    if archive.get('title')!=TITLE or archive.get('cover')!=old_archive['cover']:
        raise ValueError('Replacement changed title or cover unexpectedly')
    if part.get('fingerprints',{}).get('sha256') != new_sha:
        part.setdefault('media_revision_history',[]).append(dict(
            fingerprints=copy.deepcopy(part['fingerprints']),cid=OLD_CID,ts=txn['created_at']))
    part.update(fingerprints=meta['fingerprints'],resolution=meta['resolution'],
        duration_sec=meta['duration_sec'],media_revision_cid=updated.get('cid'),
        media_revision_run_id=txn['workflow_run_id'])
    if receipt.get('bvid')==BVID:
        receipt.update(fingerprints=meta['fingerprints'],duration_sec=meta['duration_sec'])
    txn.update(status='verified',verified_at=int(time.time()),cid=updated.get('cid'),
        archive_state=archive.get('state'))
    persist()
    return dict(status='verified',bvid=BVID,cid=updated.get('cid'),
        old_cid=OLD_CID,archive_state=archive.get('state'),new_posts=0)


def client_from_cookies(owner_mid):
    from biliup.plugins.bili_webup import BiliBili, Data
    from requests.adapters import HTTPAdapter
    cookie=json.loads(os.environ['BILIBILI_COOKIES'])
    entries=(cookie.get('cookie_info') or {}).get('cookies') or cookie.get('cookies') or []
    values={row['name']:str(row['value']) for row in entries}
    if values.get('DedeUserID')!=str(owner_mid) or not values.get('bili_jct'):
        raise ValueError('Repair account mismatch')
    logging.getLogger('biliup').setLevel(logging.WARNING)
    client=BiliBili(Data());client.login_by_cookies(values)
    client._BiliBili__session.mount('https://',HTTPAdapter(max_retries=0))
    return client,values['bili_jct']
