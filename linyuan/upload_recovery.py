"""Reconcile expired upload intents through exact authenticated archive reads.

No uploader is called here. An absent old attempt is quarantined permanently;
it is never retried. Other independently verified material can fill its slot.
"""
from copy import deepcopy
import re
import time

VERSION = 1
LEASE_SECONDS = 45 * 60
# FC's hard execution limit is two hours. Legacy intents do not record the CLI
# result, so wait beyond that limit as well as the ordinary upload lease.
LEGACY_HORIZON_SECONDS = 2 * 3600 + 120
RECHECK_SECONDS = 300


def archive_match(entry, snapshot, now):
    """A fuzzy public title search is never a publication receipt."""
    if (snapshot.get('verification_origin') != 'authenticated-creator-read'
            or snapshot.get('api_code') != 0 or snapshot.get('http_status')
            or snapshot.get('response_error')):
        return 'unavailable', None
    if snapshot.get('scan_complete') is not True or snapshot.get('owner_verified') is not True:
        return 'incomplete', None
    title = entry.get('upload_title')
    exact = [v for v in snapshot.get('videos', []) if v.get('title') == title]
    if not exact:
        return ('absent' if snapshot.get('scan_complete') is True
                and snapshot.get('owner_verified') is True else 'incomplete'), None
    matches = [v for v in exact if (str(v.get('mid')) == str(snapshot.get('owner_mid'))
        and re.fullmatch(r'BV[a-zA-Z0-9]{10}', str(v.get('bvid') or ''))
        and type(v.get('ctime')) in (int, float)
        and float(entry.get('uploading_ts') or 0)-120 <= v['ctime'] <= now+60)]
    if len(exact) != 1 or len(matches) != 1:
        return 'ambiguous', None
    video = matches[0]
    intent = entry.get('upload_receipt_intent') or {}
    duration = intent.get('duration_sec')
    try:
        duration_ok = abs(float(video['duration'])-float(duration)) <= 2 and float(duration)>0
    except (KeyError, TypeError, ValueError):
        duration_ok = False
    # A legacy title-only intent cannot reconstruct the exact uploaded file,
    # experiment allocation or mother range. Retain it for evidence recovery.
    if (not duration_ok or not re.fullmatch('[a-f0-9]{64}',
            str((intent.get('fingerprints') or {}).get('sha256') or ''))
            or intent.get('title') != title or type(intent.get('part_index')) is not int
            or intent.get('slug') != entry.get('slug')
            or intent.get('part_index') != entry.get('upload_part_index')
            or intent.get('artifact_id') != entry.get('upload_artifact_id')
            or not intent.get('artifact_id') or not intent.get('source_segments')
            or not re.fullmatch('[a-f0-9]{64}',str(intent.get('source_sha256') or ''))
            or (entry.get('upload_expected_sha256') and entry['upload_expected_sha256'] !=
                (intent.get('fingerprints') or {}).get('sha256'))):
        return 'identity_found_metadata_missing', video
    if not video.get('public'):
        return 'archive_visibility_pending', video
    return 'matched', video


def reconcile(fc, state, *, now=None, lookup=None):
    """Caller owns the publication lease; persist reconciliation before posting."""
    now = time.time() if now is None else now
    entries = [e for e in fc._latest_dispatches(state) if e.get('uploading')]
    result = dict(recovered=0, quarantined=0, pending=max(0,len(entries)-4), changed=False)
    for entry in entries[:4]:
        started = entry.get('uploading_ts')
        if (type(started) not in (int,float) or now-started < LEASE_SECONDS
                or not entry.get('upload_title')
                or now-float(entry.get('upload_reconciled_at') or 0)<RECHECK_SECONDS):
            result['pending'] += 1
            continue
        if lookup is None:
            from bili_archive_status import archive_status
            read = lambda title: archive_status([], fc.OWNER_MID, fc.COOKIES_JSON,
                expected_titles=[title], max_pages=12, scan_all=True)
        else:
            read = lookup
        try:
            snapshot = read(entry.get('upload_title') or '')
            if str(snapshot.get('owner_mid')) != str(fc.OWNER_MID):
                status, video = 'unavailable', None
            else:
                status, video = archive_match(entry, snapshot, now)
        except Exception as exc:
            snapshot = dict(error_type=type(exc).__name__)
            status, video = 'unavailable', None
        entry['upload_reconciled_at'] = now
        if status == 'matched' and any(slug != entry['slug']
                and (info.get('bvid') == video['bvid'] or any(p.get('bvid') == video['bvid']
                    for p in info.get('parts',[]))) for slug,info in state.get('published',{}).items()):
            status = 'archive_already_owned_by_other_receipt'
        entry['upload_reconciliation'] = dict(version=VERSION, status=status, checked_at=now,
            api_code=snapshot.get('api_code'), http_status=snapshot.get('http_status'),
            scan_complete=snapshot.get('scan_complete') is True,
            error_type=snapshot.get('error_type'), bvid=(video or {}).get('bvid'))
        result['changed'] = True
        if status == 'matched':
            receipt = deepcopy(entry['upload_receipt_intent'])
            receipt.update(status='published', bvid=video['bvid'], ts=int(video['ctime']),
                           recovered_at=int(now), verification_origin='authenticated-creator-read')
            slug = entry['slug']
            previous = state.setdefault('published', {}).setdefault(slug, {})
            old_parts = previous.setdefault('parts', [])
            if not any(p.get('bvid') == video['bvid'] for p in old_parts):
                old_parts.append(receipt)
                fc.mark_part_processed(entry, receipt['part_index'])
                if time.gmtime(receipt['ts']+8*3600)[:3] == time.gmtime(now+8*3600)[:3]:
                    fc.record_publication_slot(state, receipt, receipt['ts'],
                        receipt.get('makeup_slot'), planned_slot=receipt.get('scheduled_slot'))
                    mode = 'audio_card_count' if receipt.get('render_mode')=='audio_card' else 'live_video_count'
                    state['daily_publish'][mode] = state['daily_publish'].get(mode,0)+1
                elif receipt.get('makeup_slot'):
                    state.setdefault('makeup_receipts',{})[receipt['makeup_slot']] = dict(receipt)
            previous.update(bvid=video['bvid'], title=receipt['title'], ts=receipt['ts'],
                parts_total=entry.get('upload_parts_total') or 1, source_url=entry.get('source_url'),
                bvids=list(dict.fromkeys([*(previous.get('bvids') or []), video['bvid']])))
            entry.pop('uploading', None)
            result['recovered'] += 1
        elif status == 'absent' and (now-started >= LEGACY_HORIZON_SECONDS
                or type((entry.get('upload_process_result') or {}).get('completed_at')) in (int,float)
                and started <= entry['upload_process_result']['completed_at'] <= now):
            # Preserve all original intent fields for later audits. Quarantine
            # this source/file/title even if a delayed archive appears later.
            entry['publication_quarantined'] = True
            entry['upload_reconciliation']['status'] = 'absent_quarantined'
            entry.pop('uploading', None)
            result['quarantined'] += 1
        else:
            result['pending'] += 1
    if result['changed'] and fc.save_state(state) is False:
        return {**result, 'persist_failed':True}
    return result


def quarantine_error(part, entry, state):
    """An ambiguous attempt cannot re-enter through a new slug or another part."""
    from editorial_policy import source_key
    for old in state.get('dispatched', []):
        if not old.get('publication_quarantined'):
            continue
        intent = old.get('upload_receipt_intent') or {}
        old_sha = (intent.get('fingerprints') or {}).get('sha256') or old.get('upload_expected_sha256')
        new_sha = part.get('sha256') or (part.get('fingerprints') or {}).get('sha256')
        if (old.get('slug') == entry.get('slug')
                or old_sha and old_sha == new_sha
                or old.get('upload_title') and old['upload_title'] == part.get('title')
                or old.get('source_url') and source_key(old['source_url']) == source_key(entry.get('source_url'))):
            return '先前投稿结果未能恢复，已隔离原文件、来源与标题，禁止自动重传'
    return None
