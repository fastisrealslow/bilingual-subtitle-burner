"""Fresh public reference census; unknown copyright/lineage stays unknown.

Public collections are not the complete account feed. Never count a reference
download, similar title or a search hit as an accepted original mother/video.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import monitor_v2 as monitor

BASE=Path(__file__).resolve().parent
REFERENCE_MID=1700344493
COPYRIGHT_ENUM_SOURCE='https://s1.hdslb.com/bfs/static/creator-monorepo/videoup/static/js/index.02c1a54b.js'
# Current official upload application: ORIGINAL:1, REPRINT:2, UNSELECTED:3.
COPYRIGHT_CLASSES={1:'self_made',2:'repost',3:'unselected'}


def api(path):
    raw=json.loads(monitor.http_get('https://api.bilibili.com/'+path,
        referer='https://www.bilibili.com/',timeout=15))
    if raw.get('code')!=0:
        raise ValueError('Public metadata code '+str(raw.get('code')))
    return raw['data']


def discover_collections(catalog, getter=api):
    catalog={k:dict(v) for k,v in catalog.items()}
    errors=[]; listed=[]; collection_complete=True
    try:
        d=getter(f'x/polymer/web-space/seasons_series_list?mid={REFERENCE_MID}&page_num=1&page_size=20')
        listed=d['items_lists'].get('seasons_list') or []
        total=d['items_lists'].get('page',{}).get('total',len(listed))
        collection_complete=total<=20
    except Exception as e:
        errors.append(dict(stage='collections',error=type(e).__name__))
        return catalog,dict(account_feed_complete=False,collection_listing_complete=False,errors=errors)
    for collection in listed:
        meta=collection['meta']; expected=int(meta.get('total') or 0); seen=set()
        for page in range(1,min(40,(expected+99)//100)+1):
            try:
                d=getter(f"x/polymer/web-space/seasons_archives_list?mid={REFERENCE_MID}"
                    f"&season_id={meta['season_id']}&sort_reverse=true&page_num={page}&page_size=100")
                archives=d.get('archives') or []
                if not archives:break
                for r in archives:
                    if not r.get('bvid'):continue
                    seen.add(r['bvid']); old=catalog.get(r['bvid'],{})
                    catalog[r['bvid']]={**old,'title':r.get('title',''),
                        'date':datetime.fromtimestamp(r.get('pubdate') or 0,timezone.utc).strftime('%Y-%m-%d'),
                        'pubdate':r.get('pubdate') or 0,'dur':r.get('duration') or 0,
                        'play':(r.get('stat') or {}).get('view',0),'season':meta.get('title') or meta.get('name'),
                        'metadata_provenance':'bilibili_public_collection'}
                if len(seen)>=expected or len(archives)<100:break
            except Exception as e:
                errors.append(dict(stage='collection_page',season_id=meta['season_id'],page=page,
                                   error=type(e).__name__))
                break
        if len(seen)<expected:collection_complete=False
    return catalog,dict(account_feed_complete=False,collection_listing_complete=collection_complete,
        discovery='public_collections_plus_existing_reference_catalog',errors=errors)


def exact_reference(bvid,getter=api):
    try:
        d=getter('x/web-interface/view?bvid='+bvid)
        if int((d.get('owner') or {}).get('mid',0))!=REFERENCE_MID:
            raise ValueError('Unexpected reference owner')
        return dict(bvid=bvid,verified=True,title=d['title'],pubdate=d['pubdate'],
            date=datetime.fromtimestamp(d['pubdate'],timezone.utc).isoformat(),
            duration_sec=d['duration'],copyright_raw=d.get('copyright'),
            copyright_class=COPYRIGHT_CLASSES.get(d.get('copyright'),'unknown'),
            view=(d.get('stat') or {}).get('view'),description=d.get('desc',''),
            season_id=(d.get('ugc_season') or {}).get('id'),
            pages=[dict(cid=p.get('cid'),page=p.get('page'),duration=p.get('duration'))
                   for p in d.get('pages') or []],source_role='reference',direct_dispatch=False)
    except Exception as e:
        return dict(bvid=bvid,verified=False,error=type(e).__name__)


def discover_account_window(catalog,getter=api):
    """Normal public/account-session feed, no signing or risk-control bypass."""
    catalog={k:dict(v) for k,v in catalog.items()};seen=set();offset=''
    try:
        for page in range(5):
            from urllib.parse import urlencode
            d=getter('x/polymer/web-dynamic/v1/feed/space?'+urlencode(
                dict(host_mid=REFERENCE_MID,offset=offset)))
            for item in d.get('items') or []:
                modules=item.get('modules') or {};author=modules.get('module_author') or {}
                archive=((modules.get('module_dynamic') or {}).get('major') or {}).get('archive') or {}
                if author.get('mid')!=REFERENCE_MID or not archive.get('bvid'):continue
                bvid=archive['bvid'];seen.add(bvid);old=catalog.get(bvid,{})
                ts=int(author.get('pub_ts') or old.get('pubdate') or 0)
                catalog[bvid]={**old,'title':archive.get('title',''),'pubdate':ts,
                    'date':datetime.fromtimestamp(ts,timezone.utc).strftime('%Y-%m-%d'),
                    'metadata_provenance':'bilibili_account_feed'}
            if len(seen)>=30 or not d.get('has_more'):
                return catalog,dict(account_feed_window_complete=True,
                    account_feed_complete=not d.get('has_more'),video_posts_seen=len(seen))
            next_offset=d.get('offset')
            if not next_offset or next_offset==offset:break
            offset=next_offset
        return catalog,dict(account_feed_window_complete=False,account_feed_complete=False,
                            video_posts_seen=len(seen),error='bounded_feed_window_incomplete')
    except Exception as exc:
        return catalog,dict(account_feed_window_complete=False,account_feed_complete=False,
                            video_posts_seen=len(seen),error=type(exc).__name__)


def copyright_summary(rows):
    counts=Counter(r.get('copyright_class','unknown') if r.get('verified') else 'unverified'
                   for r in rows)
    n=len(rows); original=counts['self_made']; unknown=counts['unknown']+counts['unverified']
    return dict(sample_size=n,self_made=original,repost=counts['repost'],
        unselected=counts['unselected'],unknown=counts['unknown'],unverified=counts['unverified'],
        confirmed_self_made_ratio=original/n if n else None,
        self_made_ratio_upper_bound=(original+unknown)/n if n else None,
        over_80_percent_confirmed=bool(n==10 and original/n>.8),
        complete_classification=bool(n and not unknown),
        classification_meaning='Upload declaration, not proof of actual authorship or rights.',
        enum_source=COPYRIGHT_ENUM_SOURCE)


def coverage_report(rows, research, lineage, publications):
    matches=research.get('audio_matches') or research.get('audio_match_candidates') or []
    jobs=research.get('jobs') or {}
    families=lineage.get('families') or []
    known={r['bvid']:r.get('candidate_families') or [] for r in lineage.get('references') or []}
    out=[]
    for row in rows:
        b=row['bvid'];url='https://www.bilibili.com/video/'+b
        matching=[m for m in matches if m.get('reference_url','').rstrip('/')==url]
        matched_urls={m['mother_url'].rstrip('/') for m in matching if m.get('mother_url')}
        # A family clue remains a lead, never upgraded to confirmed overlap.
        hints=list(known.get(b,[]))
        if '凤凰湾区财经论坛2026' in (row.get('description') or ''):
            hints.append('phoenix_2026_09')
        candidate_urls=[u for f in families if f.get('id') in hints
                        for k in ('official_urls','mirror_urls') for u in f.get(k,[])]
        source_leads=[u for f in families if f.get('id') in hints
                      for u in f.get('evidence') or []]
        matched_jobs=[j for j in jobs.values() if j.get('role')=='mother'
                      and j.get('url','').rstrip('/') in matched_urls]
        media=[j for j in matched_jobs if (j.get('evidence') or {}).get('media_integrity')=='passed']
        matched_hashes={(j.get('evidence') or {}).get('sha256') for j in media}
        published=[]
        for slug,p in (publications.get('published') or {}).items():
            for part in p.get('parts') or []:
                if (part.get('bvid') and (part.get('source_sha256') in matched_hashes
                        or p.get('source_url','').rstrip('/') in matched_urls)):
                    published.append(dict(bvid=part['bvid'],title=part.get('title'),
                        ts=part.get('ts'),same_mother_only=True,exact_argument_confirmed=False))
        out.append(dict(bvid=b,verified=row.get('verified'),title=row.get('title'),
            pubdate=row.get('pubdate'),source_role='reference',direct_dispatch=False,
            in_linyuan_scope='林园' in (row.get('title') or '') and '余军' not in (row.get('title') or ''),
            coverage_status=('audio_match_candidate_with_publications' if published else 'audio_match_candidate'
                             if matching else 'family_lead_only' if candidate_urls or source_leads else 'unresolved'),
            source_media_inspected=bool(media),audio_match_candidates=matching,
            candidate_source_urls=candidate_urls,metadata_only_leads=source_leads,
            candidate_families=hints,same_mother_publications=published,
            exact_argument_covered=False,reference_origin_confirmed=False))
    return dict(sample_size=len(out),rows=out,
        counts=dict(Counter(r['coverage_status'] for r in out)),
        exact_argument_covered=0,all_reference_sources_available=False,
        more_and_faster_than_reference=False,
        note='Title/family clues do not prove overlap, rights, usable stock or publication speed.')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,
        default=BASE/'.automation/reference_audit.json');ap.add_argument('--refresh',action='store_true')
    args=ap.parse_args();catalog=json.loads((BASE/'up_videos.json').read_text())
    getter=api
    if os.environ.get('BILIBILI_COOKIES'):
        from platform_collections import Client,OWNER_MID
        try:
            client=Client(os.environ['BILIBILI_COOKIES'])
            nav=client.call('/x/web-interface/nav',public=True)
            if not nav.get('isLogin') or nav.get('mid')!=OWNER_MID:
                raise ValueError('Unexpected account')
            def getter(path):
                from urllib.parse import urlsplit,parse_qsl
                parsed=urlsplit(path)
                return client.call('/'+parsed.path,public=True,params=dict(parse_qsl(parsed.query)))
        except Exception:getter=api
    catalog,discovery=discover_collections(catalog,getter)
    catalog,feed=discover_account_window(catalog,getter)
    discovery.update(feed)
    newest=sorted(catalog.items(),key=lambda p:(p[1].get('pubdate') or 0,p[1].get('date','')),reverse=True)
    # Legacy catalogue rows may lack a timestamp; sort all rows by date first.
    newest=sorted(newest,key=lambda p:(p[1].get('date',''),p[1].get('pubdate') or 0),reverse=True)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows=list(pool.map(lambda b:exact_reference(b,getter),[b for b,_ in newest[:30]]))
    for r in rows:
        if r.get('verified'):
            catalog[r['bvid']].update(title=r['title'],date=r['date'][:10],pubdate=r['pubdate'],
                dur=r['duration_sec'],play=r['view'],copyright_raw=r['copyright_raw'],
                copyright_class=r['copyright_class'],metadata_checked_at=int(time.time()),
                description=r['description'],
                metadata_provenance='bilibili_view')
    def load(name):
        p=BASE/name
        return json.loads(p.read_text()) if p.exists() else {}
    report=dict(version=1,checked_at=int(time.time()),reference_mid=REFERENCE_MID,
        discovery=discovery,latest_known_10=rows[:10],copyright=copyright_summary(rows[:10]),
        references=rows,coverage=coverage_report(rows,load('.automation/source_research_report.json'),
            load('source_lineage.json'),load('.automation/fc_state.json')))
    from batch_delivery import write_json
    write_json(args.output,report)
    if args.refresh:
        write_json(BASE/'up_videos.json',catalog)
        monitor.upsert_items(monitor.CompetitorReferenceSource({},{}).fetch(None))
    print(json.dumps(dict(checked=len(rows),verified=sum(r['verified'] for r in rows),
        copyright=report['copyright'],coverage=report['coverage']['counts'],discovery=discovery),ensure_ascii=False))


if __name__=='__main__':main()
