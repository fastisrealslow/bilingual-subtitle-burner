"""Expand actual uploader-series episodes, with their own author, CID and clock."""
import json
import re


def episode_items(data, duration_seconds, publish_time):
    items, seen = [], set()
    for section in (data.get('ugc_season') or {}).get('sections') or []:
        for episode in section.get('episodes') or []:
            bvid = episode.get('bvid') or ''
            if not re.fullmatch(r'BV[0-9A-Za-z]{10}', bvid):
                continue
            arc = episode.get('arc') or {}
            author = (arc.get('author') or {}).get('name') or ''
            title = arc.get('title') or episode.get('title') or ''
            if author in {'', '园园滚雪球', '园来滚雪球'} or '林园' not in title:
                continue
            if re.search(r'园林|虎林园|林园酒店|纯音频|录音版|张海辉', title):
                continue
            for page in episode.get('pages') or [episode.get('page') or {}]:
                duration = duration_seconds(page.get('duration'))
                number, cid = page.get('page'), page.get('cid')
                if not 120 <= duration <= 5400 or type(number) is not int or number < 1 or not cid:
                    continue
                key = f'bilibili_search:{bvid}' + (f':p{number}' if number > 1 else '')
                if key in seen:
                    continue
                seen.add(key)
                items.append(dict(id=key, source='bilibili_series', title=title,
                    author=author, publish_time=publish_time(arc.get('pubdate')),
                    url=f'https://www.bilibili.com/video/{bvid}' + (f'?p={number}' if number > 1 else ''),
                    extra=json.dumps(dict(bvid=bvid, page=number, cid=cid, duration=duration,
                        source_role='mother_candidate', direct_dispatch=True,
                        metadata_status='known_duration', metadata_provenance='bilibili_actual_series_episode',
                        series_id=(data.get('ugc_season') or {}).get('id'),
                        media_verified=False, editorial_approved=False), ensure_ascii=False)))
    return items
