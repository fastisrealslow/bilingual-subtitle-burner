"""Reuse recent exact public catalogue evidence; never approve source media."""
from datetime import datetime
import json
import re
import time

from source_catalogue_duration import eligible_duration
from source_admission_guard import restriction

MAX_AGE_SECONDS = 6 * 3600


def verified_title_admission_recheck(seed,now=None):
    """Bind a title-only classification change to unchanged, fresh API identity."""
    import hashlib
    proof=seed.get('title_admission_recheck') or {}
    before=proof.get('previous_seed') or {}
    encoded=json.dumps(before,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    if (proof.get('version')!=1
            or hashlib.sha256(encoded.encode()).hexdigest()!=proof.get('previous_seed_sha256')
            or before.get('metadata_provenance')!='bilibili_view_exact_id_author_pages'
            or before.get('target_speaker_title_evidence') is not False
            or before.get('source_role')!='catalog_only' or before.get('direct_dispatch') is not False
            or type(before.get('no_reprint')) is not int or before['no_reprint']!=0
            or before.get('reuse_hold_reason') or before.get('diagnostic_only')
            or before.get('media_verified') is not False or before.get('editorial_approved') is not False
            or any(before.get(k)!=seed.get(k) for k in ('bvid','title','observed_author','owner_mid','pages','no_reprint'))
            or not re.search(r'投资者交流|圆桌会议|讲座|全集',seed.get('title') or '')):
        return []
    return items(seed,now)


def items(seed, now=None):
    now = time.time() if now is None else now
    bvid = seed.get('bvid')
    if (not isinstance(bvid,str) or not re.fullmatch(r'BV[0-9A-Za-z]{10}',bvid)
            or seed.get('metadata_provenance') != 'bilibili_view_exact_id_author_pages'
            or seed.get('source_role') != 'mother_candidate'
            or seed.get('direct_dispatch') is not True
            or seed.get('target_speaker_title_evidence') is not True
            or type(seed.get('no_reprint')) is not int or seed['no_reprint'] != 0
            or seed.get('media_verified') is not False or seed.get('editorial_approved') is not False
            or seed.get('diagnostic_only') is True
            or type(seed.get('owner_mid')) is not int or seed['owner_mid'] <= 0
            or seed['owner_mid'] in {1700344493,275211725}):
        return []
    try:
        observed = datetime.fromisoformat(seed['checked_at'].replace('Z','+00:00'))
        if observed.tzinfo is None or not -60 <= now-observed.timestamp() <= MAX_AGE_SECONDS:
            return []
    except (KeyError,TypeError,ValueError,AttributeError):
        return []
    author = seed.get('observed_author')
    if not isinstance(author,str) or not author.strip() or author in {'园园滚雪球','园来滚雪球','账号已注销','已注销'}:
        return []
    url = 'https://www.bilibili.com/video/'+bvid
    if seed.get('url') not in {url,url+'/'} or restriction(url,[seed]):
        return []
    from fc.index import title_has_target_speaker,NOISE,AI_NOISE
    title = seed.get('title') or ''
    if not isinstance(title,str) or not title_has_target_speaker(title) or NOISE.search(title) or AI_NOISE.search(title):
        return []
    pages = seed.get('pages')
    if not isinstance(pages,list) or not pages:
        return []
    result = []
    seen = set()
    for page in pages:
        if not isinstance(page,dict):return []
        number,cid,duration = page.get('page'),page.get('cid'),page.get('duration')
        if (type(number) is not int or number<1 or type(cid) is not int or cid<1
                or not eligible_duration(duration,len(pages)) or number in seen
                or not isinstance(page.get('title',''),str)):
            return []
        seen.add(number)
        suffix = f':p{number}' if number>1 else ''
        result.append(dict(id='bilibili_search:'+bvid+suffix,source='bilibili_collection',
            title=f"林园 P{number} {page.get('title') or title}",
            url=url+(f'?p={number}' if number>1 else ''),author=author,
            publish_time=seed.get('published_at') or '',
            extra=json.dumps(dict(bvid=bvid,page=number,cid=cid,duration=duration,
                collection_title=title,source_role='mother_candidate',direct_dispatch=True,
                no_reprint=0,metadata_status='known_duration',
                metadata_provenance='cached_bilibili_view_exact_id_author_pages',
                metadata_checked_at=seed['checked_at'],media_verified=False,
                editorial_approved=False,rights_status='not_determined',
                content_date_hint=seed.get('content_date_hint'),
                content_date_hint_provenance=seed.get('content_date_hint_provenance')),
                ensure_ascii=False)))
    return result
