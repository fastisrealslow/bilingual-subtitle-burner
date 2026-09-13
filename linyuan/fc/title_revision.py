"""Edit the reported title in place under the existing FC publication lease."""
import copy
import json
import os
import time
from reviewed_updates import creator_detail, payload_for

BVID = 'BV14PYY6sEbq'
SLUG = 'ly-0912-a7b1c7'
SHA256 = '587072d5901e876b92630edb5335f4010265a4590fa6a214eecc5cee60309e4c'
OLD_TITLE = '林园：谈消费需求、医药投资与买入时机'
TITLE = '林园：龙头还没跑出来，为什么先买整个行业？'
STATE_KEY = 'title_revision_0913'


def apply(fc):
    state = fc.load_state()
    receipt = state.get('published', {}).get(SLUG, {})
    parts = [p for p in receipt.get('parts', []) if p.get('bvid') == BVID]
    if len(parts) != 1 or parts[0].get('fingerprints', {}).get('sha256') != SHA256:
        raise ValueError('Original BVID and media receipt do not match')
    part = parts[0]
    txns = state.setdefault(STATE_KEY, {})
    txn = txns.get(BVID, {})
    if txn.get('status') == 'verified' and txn.get('title') == TITLE:
        return dict(status='already_verified', bvid=BVID, title=TITLE, new_posts=0)
    from biliup.plugins.bili_webup import BiliBili, Data
    from requests.adapters import HTTPAdapter
    cookie = json.loads(os.environ['BILIBILI_COOKIES'])
    entries = (cookie.get('cookie_info') or {}).get('cookies') or cookie.get('cookies') or []
    values = {c['name']:str(c['value']) for c in entries}
    if values.get('DedeUserID') != str(fc.OWNER_MID) or not values.get('bili_jct'):
        raise ValueError('Title edit account mismatch')
    client = BiliBili(Data())
    client.login_by_cookies(dict(cookie_info=dict(cookies=entries)))
    session = client._BiliBili__session
    session.mount('https://', HTTPAdapter(max_retries=0))
    return apply_session(fc, state, receipt, part, txns, txn, session, values['bili_jct'])


def apply_session(fc, state, receipt, part, txns, txn, session, csrf):
    def persist():
        fc.save_state(state)
        if fc.load_state().get(STATE_KEY) != txns:
            raise RuntimeError('Title edit intent or receipt did not persist')
    current = creator_detail(session, BVID)
    archive = current.get('archive') or current.get('Archive') or {}
    item = dict(bvid=BVID, old_title=OLD_TITLE, title=TITLE)
    payload = payload_for(current, item, archive['cover'])
    if not txn:
        txn = dict(status='prepared', title=TITLE, old_title=OLD_TITLE,
                   original_archive=copy.deepcopy(current), created_at=int(time.time()),
                   expected_sha256=SHA256)
        txns[BVID] = txn
        persist()
    original = txn['original_archive']
    video = (current.get('videos') or [{}])[0]
    old_video = (original.get('videos') or [{}])[0]
    old_archive = original.get('archive') or original.get('Archive') or {}
    if (video.get('filename') != old_video.get('filename') or video.get('cid') != old_video.get('cid')
            or archive.get('cover') != old_archive.get('cover')):
        raise ValueError('Existing media or cover changed during title edit')
    if archive.get('title') != TITLE:
        if txn['status'] in ('edit_requested', 'edit_rejected'):
            return dict(status=txn['status'], bvid=BVID, new_posts=0)
        txn['status'] = 'edit_requested'
        persist()
        reply = session.post('https://member.bilibili.com/x/vu/web/edit',
                             params=dict(csrf=csrf), json=payload, timeout=60).json()
        txn.update(api_code=reply.get('code'), api_message=str(reply.get('message') or '')[:200])
        if reply.get('code') != 0:
            txn['status'] = 'edit_rejected'
        persist()
        current = creator_detail(session, BVID)
        archive = current.get('archive') or current.get('Archive') or {}
        video = (current.get('videos') or [{}])[0]
    if archive.get('title') != TITLE:
        return dict(status=txn['status'], bvid=BVID, new_posts=0)
    if (video.get('filename') != old_video.get('filename') or video.get('cid') != old_video.get('cid')
            or archive.get('cover') != old_archive.get('cover')):
        raise ValueError('Readback media or cover no longer matches original')
    part.setdefault('editorial_revision_history', []).append(dict(title=part['title'], ts=txn['created_at']))
    part['title'] = TITLE
    if receipt.get('bvid') == BVID:
        receipt['title'] = TITLE
    txn.update(status='verified', verified_at=int(time.time()), title=archive['title'],
               cid=video.get('cid'), archive_state=archive.get('state'), new_posts=0)
    persist()
    fc.log_event('title_revision', '已原位更新标题 ' + BVID, TITLE)
    return dict(status='verified', bvid=BVID, title=archive['title'], cid=video.get('cid'), new_posts=0)
