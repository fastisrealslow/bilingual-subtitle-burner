"""One requested video replacement on the already reviewed third archive."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

from reviewed_updates import creator_detail, payload_for, matches

DIRECTORY = Path(__file__).parent / 'reviewed_0910'
BVID = 'BV1hmYt6SEJd'
SLUG = 'ly-0909-3c2ade'
BATCH = 'reviewed-third-video-20260910'
STATE_KEY = 'reviewed_third_video_0910'
OLD_SHA = '50ec3894f4e2822dfd91aba5f1a6702fd47122ba6b5e12ab6c4315083cf38e6e'


def validated_asset(fc, item):
    if (item.get('batch') != BATCH or item.get('bvid') != BVID or item.get('slug') != SLUG
            or item.get('old_video_sha256') != OLD_SHA or item.get('title') != item.get('old_title')
            or item.get('video') != 'third-video.mp4'):
        raise ValueError('Unrecognized third-video revision')
    video = DIRECTORY / item['video']
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    proof = json.loads((DIRECTORY / 'third-video-proof.json').read_text())
    if (digest != item['video_sha256'] or digest != proof.get('sha256')
            or digest != item['fingerprints'].get('sha256')
            or not proof.get('full_decode_passed')
            or not proof.get('within_shot_crop_scale_constant')
            or proof.get('matched_ratio', 0) < .8
            or proof.get('frames') != proof.get('encoded_frames')
            or abs(fc.mp4_duration(video) - item['duration_sec']) > .05):
        raise ValueError('Third video verification mismatch')
    subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(video),'-f','null','-'],
                   check=True,capture_output=True,timeout=180)
    return video


def apply(fc):
    item = json.loads((DIRECTORY / 'third-video-manifest.json').read_text())
    video = validated_asset(fc, item)
    state = fc.load_state()
    transactions = state.setdefault(STATE_KEY, {})
    transaction = transactions.get(BVID, {})
    if transaction.get('status') == 'verified':
        if transaction.get('video_sha256') != item['video_sha256']:
            raise ValueError('Receipt belongs to a different revision')
        return dict(status='already_verified', bvid=BVID, new_posts=0)
    prior = state.get('reviewed_updates_0910', {}).get(BVID, {})
    if prior.get('status') != 'verified':
        raise ValueError('Original reviewed packaging is not verified')
    receipt = state.get('published', {}).get(SLUG, {})
    parts = [p for p in receipt.get('parts', []) if p.get('bvid') == BVID]
    if len(parts) != 1 or parts[0].get('fingerprints', {}).get('sha256') != OLD_SHA:
        raise ValueError('Original media receipt changed')
    part = parts[0]
    from biliup.plugins.bili_webup import BiliBili, Data
    from requests.adapters import HTTPAdapter
    cookie = json.loads(os.environ['BILIBILI_COOKIES'])
    entries = (cookie.get('cookie_info') or {}).get('cookies') or cookie.get('cookies') or []
    values = {c['name']: str(c['value']) for c in entries}
    if values.get('DedeUserID') != str(fc.OWNER_MID) or not values.get('bili_jct'):
        raise ValueError('Third video account mismatch')
    client = BiliBili(Data())
    client.login_by_cookies(dict(cookie_info=dict(cookies=entries)))
    session = client._BiliBili__session
    session.mount('https://', HTTPAdapter(max_retries=0))

    def persist():
        fc.save_state(state)
        if fc.load_state().get(STATE_KEY) != transactions:
            raise RuntimeError('Third video receipt did not persist; refusing edit')

    current = creator_detail(session, BVID)
    payload_for(current, item, prior['cover'])
    archive = current.get('archive') or current.get('Archive') or {}
    if urlsplit(archive.get('cover') or '').path != urlsplit(prior['cover']).path:
        raise ValueError('Reviewed cover changed')
    if not transaction:
        original_filename = ((prior.get('original_archive') or {}).get('videos') or [{}])[0].get('filename')
        if not original_filename or (current.get('videos') or [{}])[0].get('filename') != original_filename:
            raise ValueError('Creator video differs from the reviewed original')
        transaction = dict(status='prepared', title=item['title'], cover=prior['cover'],
            batch=BATCH, video_sha256=item['video_sha256'], created_at=int(time.time()),
            original_archive=copy.deepcopy(current))
        transactions[BVID] = transaction
        persist()
    if transaction.get('video_sha256') != item['video_sha256']:
        raise ValueError('In-flight revision has different video bytes')
    if not transaction.get('filename'):
        uploaded = client.upload_file(str(video), lines='bda2', tasks=1)
        transaction.update(status='uploaded', filename=uploaded['filename'])
        persist()
    current = creator_detail(session, BVID)
    if not matches(current, item, transaction):
        if transaction['status'] in ('edit_requested', 'edit_rejected'):
            return dict(status=transaction['status'], bvid=BVID, new_posts=0)
        original = transaction['original_archive']
        current_archive = current.get('archive') or current.get('Archive') or {}
        if urlsplit(current_archive.get('cover') or '').path != urlsplit(transaction['cover']).path:
            raise ValueError('Cover changed during upload')
        if ((current.get('videos') or [{}])[0].get('filename')
                != (original.get('videos') or [{}])[0].get('filename')):
            raise ValueError('Video changed while replacement was being prepared')
        payload = payload_for(current, item, transaction['cover'], transaction['filename'])
        transaction.update(status='edit_requested', payload_schema_version=3)
        persist()
        reply = session.post('https://member.bilibili.com/x/vu/web/edit',
            params=dict(csrf=values['bili_jct']), json=payload, timeout=60).json()
        transaction['api_code'] = reply.get('code')
        transaction['api_message'] = str(reply.get('message') or reply.get('msg') or '')[:300]
        if reply.get('code') != 0:
            transaction['status'] = 'edit_rejected'
        persist()
        current = creator_detail(session, BVID)
    if not matches(current, item, transaction):
        return dict(status=transaction['status'], bvid=BVID, new_posts=0)
    transaction.update(status='verified', verified_at=int(time.time()),
        aid=(current.get('archive') or current.get('Archive') or {}).get('aid'),
        cid=(current.get('videos') or [{}])[0].get('cid'))
    part.setdefault('editorial_revision_history', []).append(dict(batch=BATCH,
        title=part.get('title'), ts=transaction['created_at'],
        fingerprints=copy.deepcopy(part.get('fingerprints'))))
    part['fingerprints'] = copy.deepcopy(item['fingerprints'])
    receipt['fingerprints'] = copy.deepcopy(item['fingerprints'])
    receipt['duration_sec'] = item['duration_sec']
    part['reviewed_media_revision'] = dict(batch=BATCH, source_url=item['source_url'],
        source_range=item['source_range'], original_range_retained_for_dedup=True)
    # Original source ranges, the other two receipts and daily quota are untouched.
    persist()
    fc.log_event('reviewed_update', '已原位更新第三条视频 ' + BVID, item['title'])
    return dict(status='verified', bvid=BVID, new_posts=0)
