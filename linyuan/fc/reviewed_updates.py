"""Apply the three explicitly accepted 2026-09-10 assets to existing archives."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit

DIRECTORY = Path(__file__).parent / 'reviewed_0910'
TARGETS = {'BV16vYT6ME5s', 'BV16QYK6qEwm', 'BV1hmYt6SEJd'}


def creator_detail(session, bvid):
    response = session.get('https://member.bilibili.com/x/vupre/web/archive/view',
                           params=dict(bvid=bvid, history=''), timeout=30)
    content_type = response.headers.get('Content-Type', '')
    if response.status_code != 200:
        raise RuntimeError(f'Creator detail HTTP {response.status_code}; type={content_type}; bvid={bvid}')
    try:
        reply = response.json()
    except ValueError:
        raise RuntimeError(f'Creator detail returned non-JSON; HTTP 200; type={content_type}; bvid={bvid}') from None
    if not isinstance(reply, dict):
        raise RuntimeError('Creator detail returned invalid JSON shape')
    if reply.get('code') != 0:
        raise RuntimeError('Creator detail unavailable: ' + str(reply.get('code')))
    return reply.get('data') or {}


def asset(name, digest):
    if Path(name).name != name:
        raise ValueError('Invalid reviewed asset path')
    path = DIRECTORY / name
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError('Reviewed asset bytes changed: ' + name)
    return path


def payload_for(data, item, cover, filename=None):
    archive = data.get('archive') or data.get('Archive') or {}
    videos = data.get('videos') or []
    if (len(videos) != 1 or not archive.get('aid')
            or archive.get('bvid') not in (None, item['bvid'])
            or archive.get('title') not in (item['old_title'], item['title'])):
        raise ValueError('Existing archive identity, title or part count changed')
    # The read model contains nested status/policy objects that are not valid
    # edit inputs. Use the uploader's Studio fields plus existing visibility.
    editable = ('aid', 'bvid', 'copyright', 'source', 'tid', 'cover', 'title',
        'desc_format_id', 'desc', 'dynamic', 'tag', 'dtime', 'interactive',
        'mission_id', 'lossless_music', 'no_reprint', 'is_only_self',
        'is_space_hidden', 'no_public', 'open_elec', 'up_selection_reply',
        'up_close_reply', 'up_close_danmu')
    payload = {k:copy.deepcopy(archive[k]) for k in editable if archive.get(k) is not None}
    if archive.get('desc_v2'):
        payload['desc_v2'] = copy.deepcopy(archive['desc_v2'])
    if archive.get('human_type2'):
        payload['human_type2'] = copy.deepcopy(archive['human_type2'])
    if (archive.get('creation_statement') or {}).get('id', 0) > 0:
        payload['creation_statement'] = {'id': archive['creation_statement']['id']}
    # Creator reads expose the category as {id, name}; edit expects an integer.
    if isinstance(payload.get('human_type2'), dict):
        payload['human_type2'] = int(payload['human_type2'].get('id') or 0)
    video = {k: v for k, v in videos[0].items() if k in ('filename', 'title', 'desc', 'cid')}
    if filename:
        video['filename'] = filename
        video.pop('cid', None)
    video['title'] = item['title']
    payload.update(title=item['title'], cover=cover, videos=[video])
    return payload


def matches(data, item, transaction):
    archive = data.get('archive') or data.get('Archive') or {}
    videos = data.get('videos') or [{}]
    return (archive.get('title') == item['title']
            and urlsplit(archive.get('cover') or '').path == urlsplit(transaction['cover']).path
            and (not item.get('video') or videos[0].get('filename') == transaction.get('filename')))


def apply(fc):
    manifest = json.loads((DIRECTORY / 'manifest.json').read_text())
    items = manifest['items']
    if (manifest.get('batch') != 'reviewed-20260910' or not manifest.get('approved_assets_only')
            or len(items) != 3 or {i['bvid'] for i in items} != TARGETS):
        raise ValueError('Unrecognized approved batch')
    # Validate the exact accepted assets before opening any upload session.
    for item in items:
        asset(item['cover'], item['cover_sha256'])
        if item.get('video'):
            video = asset(item['video'], item['video_sha256'])
            proof = json.loads((DIRECTORY / 'video-proof.json').read_text())
            if (proof.get('full_decode_passed') is not True
                    or proof.get('within_shot_crop_scale_constant') is not True
                    or proof.get('matched_ratio', 0) < .8
                    or proof.get('frames') != proof.get('encoded_frames')
                    or abs(fc.mp4_duration(video) - 120.2) > .05):
                raise ValueError('Accepted video verification mismatch')
            subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(video),
                            '-f','null','-'],check=True,capture_output=True,timeout=180)
    state = fc.load_state()
    transactions = state.setdefault('reviewed_updates_0910', {})
    if all(transactions.get(i['bvid'], {}).get('status') == 'verified' for i in items):
        return dict(status='already_verified', bvids=sorted(TARGETS), new_posts=0)

    from biliup.plugins.bili_webup import BiliBili, Data
    from requests.adapters import HTTPAdapter
    cookie = json.loads(os.environ['BILIBILI_COOKIES'])
    entries = (cookie.get('cookie_info') or {}).get('cookies') or cookie.get('cookies') or []
    values = {c['name']: str(c['value']) for c in entries}
    if values.get('DedeUserID') != str(fc.OWNER_MID) or not values.get('bili_jct'):
        raise ValueError('Reviewed update account mismatch')
    client = BiliBili(Data())
    client.login_by_cookies(dict(cookie_info=dict(cookies=entries)))
    session = client._BiliBili__session
    session.mount('https://', HTTPAdapter(max_retries=0))

    def persist():
        fc.save_state(state)
        observed = fc.load_state().get('reviewed_updates_0910')
        if observed != transactions:
            raise RuntimeError('Update receipt did not persist; refusing edit')

    def detail(bvid):
        return creator_detail(session, bvid)

    results = []
    for item in items:
        bvid = item['bvid']
        transaction = transactions.get(bvid, {})
        receipt = state.get('published', {}).get(item['slug'], {})
        parts = [p for p in receipt.get('parts', []) if p.get('bvid') == bvid]
        if len(parts) != 1:
            raise ValueError('Missing original publication receipt: ' + bvid)
        part = parts[0]
        if transaction.get('status') == 'verified':
            results.append(dict(bvid=bvid, status='already_verified')); continue
        if part.get('fingerprints', {}).get('sha256') != item['old_video_sha256']:
            raise ValueError('A newer media revision exists: ' + bvid)
        before = detail(bvid)
        payload_for(before, item, 'validation-only')
        if not transaction:
            # Preserve the exact 16:9 accepted image. biliup.cover_up crops it to 16:10.
            reply = session.post('https://member.bilibili.com/x/vu/web/cover/up',
                data=dict(cover='data:image/jpeg;base64,' + base64.b64encode(
                    asset(item['cover'], item['cover_sha256']).read_bytes()).decode(),
                    csrf=values['bili_jct']), timeout=30).json()
            cover = (reply.get('data') or {}).get('url')
            if reply.get('code') != 0 or not cover:
                raise RuntimeError('Cover upload rejected: ' + str(reply.get('code')))
            transaction = dict(status='cover_uploaded', cover=cover, title=item['title'],
                cover_sha256=item['cover_sha256'], created_at=int(time.time()),
                original_archive=copy.deepcopy(before))
            transactions[bvid] = transaction; persist()
        if item.get('video') and not transaction.get('filename'):
            uploaded = client.upload_file(str(asset(item['video'], item['video_sha256'])), lines='bda2', tasks=1)
            transaction.update(filename=uploaded['filename'], video_sha256=item['video_sha256'])
            persist()
        current = detail(bvid)
        if not matches(current, item, transaction):
            # A definitive parameter rejection of the old payload may be retried
            # once with the corrected schema. Uncertain edits are only read back.
            corrected_rejection = (transaction['status'] == 'edit_rejected'
                and transaction.get('api_code') == 21001
                and transaction.get('payload_schema_version', 1) < 3)
            # The two old sync workers were confirmed gone before recovery.
            # A v2 request may be corrected only when creator state still equals
            # the original title, cover and video; never replay a v3 request.
            original = transaction.get('original_archive') or {}
            old_archive = original.get('archive') or original.get('Archive') or {}
            now_archive = current.get('archive') or current.get('Archive') or {}
            unchanged_original = (now_archive.get('state') == 0
                and now_archive.get('title') == item['old_title']
                and old_archive.get('cover') == now_archive.get('cover')
                and (original.get('videos') or [{}])[0].get('filename')
                    == (current.get('videos') or [{}])[0].get('filename'))
            corrected_uncertain = (transaction['status'] == 'edit_requested'
                and transaction.get('payload_schema_version') == 2 and unchanged_original)
            if transaction['status'] in ('edit_requested', 'edit_rejected') and not (corrected_rejection or corrected_uncertain):
                results.append(dict(bvid=bvid, status=transaction['status'])); continue
            payload = payload_for(current, item, transaction['cover'], transaction.get('filename'))
            transaction.update(status='edit_requested', payload_schema_version=3); persist()
            reply = session.post('https://member.bilibili.com/x/vu/web/edit',
                params=dict(csrf=values['bili_jct']), json=payload, timeout=60).json()
            transaction['api_code'] = reply.get('code')
            transaction['api_message'] = str(reply.get('message') or reply.get('msg') or '')[:300]
            if reply.get('code') != 0:
                transaction['status'] = 'edit_rejected'
            persist()
            current = detail(bvid)
        if not matches(current, item, transaction):
            results.append(dict(bvid=bvid, status=transaction['status'], code=transaction.get('api_code')))
            continue
        transaction.update(status='verified', verified_at=int(time.time()),
            aid=(current.get('archive') or current.get('Archive') or {}).get('aid'),
            cid=(current.get('videos') or [{}])[0].get('cid'))
        part.setdefault('editorial_revision_history', []).append(dict(
            title=part.get('title'), ts=transaction['created_at'], fingerprints=copy.deepcopy(part.get('fingerprints'))))
        part['title'] = item['title']; part['cover'] = transaction['cover']
        receipt['title'] = item['title']
        if item.get('video'):
            part['fingerprints'] = item['fingerprints']; receipt['fingerprints'] = item['fingerprints']
            receipt['duration_sec'] = item['duration_sec']
            part['reviewed_media_revision'] = dict(batch=manifest['batch'],
                source_url='https://www.bilibili.com/video/BV17Rt8zKEji',
                source_range=[218.04, item['new_source_end']], source_quality='480P',
                subtitle_text='previous transcript with reviewed layout; no new ASR')
            # Retain original source range/SHA as dedup history for the entire old excerpt.
        persist()
        fc.log_event('reviewed_update', '已原位更新 ' + bvid, item['title'])
        results.append(dict(bvid=bvid, status='verified', title=item['title'], cover=transaction['cover']))
    return dict(results=results, new_posts=0, status='verified' if all(
        r['status'] in ('verified','already_verified') for r in results) else 'pending')
