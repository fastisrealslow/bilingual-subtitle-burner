"""Read-only leads for bounded view/page discovery, never media approval."""
import json
from pathlib import Path
import re
import sqlite3

NON_LIVE_FORMAT=re.compile(r'纯音频|纯录音|[（(]音频[）)]|音频(?:版|完整版)|录音(?:版|完整版)|'
    r'(?:AI|人工智能)(?:合成|模拟|拟声|配音)(?:访谈|音频|视频|版)',re.I)


def parent_seeds(db,existing=(),attempted=(),limit=64):
    """Search duration is a parent's total; only live page metadata can split it.

    Keep exact unattempted parents first. Known restrictions and non-speaker
    material stay excluded; a successful view lookup still grants no rights,
    identity, picture, story or delivery approval.
    """
    db=Path(db)
    if not db.is_file():return []
    from fc import index as F
    from seed_campaign import NO_MEDIA
    from source_admission_guard import official_page_key,restriction
    existing=set(existing);attempted=set(attempted);found={};denied=set()
    with sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True) as connection:
        connection.row_factory=sqlite3.Row
        rows=connection.execute("SELECT * FROM items WHERE source IN ('bilibili_search','reference_origin_search','bilibili_series') ORDER BY publish_time DESC")
        for raw in rows:
            row=dict(raw)
            try:extra=json.loads(row.get('extra') or '{}')
            except (TypeError,ValueError):continue
            if not isinstance(extra,dict):continue
            bvid=extra.get('bvid');title=row.get('title') or '';author=row.get('author') or ''
            if isinstance(bvid,str) and (extra.get('no_reprint')==1
                    or extra.get('direct_dispatch') is False
                    or extra.get('source_role') in {'reference','catalog_only'}):
                # A weaker duplicate must never hide an observed restriction.
                denied.add(bvid);found.pop(bvid,None)
                continue
            if (not isinstance(bvid,str) or not re.fullmatch(r'BV[0-9A-Za-z]{10}',bvid)
                    or bvid in existing or bvid in denied or official_page_key(row.get('url'))!=('bilibili',bvid)
                    or F.editorial.source_key(row.get('url') or '')!=bvid+':p1'
                    or not author or author in F.COMPETITOR_AUTHORS or author in {'账号已注销','已注销'}
                    or author in {'园来滚雪球','园园滚雪球'}
                    or extra.get('page') is not None
                    or extra.get('source_role') in {'reference','catalog_only'}
                    or extra.get('direct_dispatch') is False or extra.get('no_reprint')==1
                    or not F.title_has_target_speaker(title) or F.NOISE.search(title)
                    or F.AI_NOISE.search(title) or NO_MEDIA.search(title) or NON_LIVE_FORMAT.search(title)
                    or F.candidate_duration(dict(extra=extra))<=F.MAX_DUR
                    or restriction(row.get('url'),[])):
                continue
            found.setdefault(bvid,dict(bvid=bvid,title=title,
                metadata_refresh_priority=True,discovery='overlimit_catalog_parent',
                catalog_published_at=row.get('publish_time') or '',
                no_reprint=extra.get('no_reprint'),
                media_verified=False,editorial_approved=False))
    # Stable sorting retains the SQL publication order within each group.
    # Upload times order leads only; no recording dates are generated.
    return sorted(found.values(),key=lambda row:row['bvid'] in attempted)[:max(0,min(int(limit),64))]
