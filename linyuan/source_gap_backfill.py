"""Reconcile reference clues and full source directories without approving media."""
import argparse
import json
from pathlib import Path
import sqlite3
import time
import monitor_v2 as monitor

BASE = Path(__file__).resolve().parent


def get_api(path):
    data = json.loads(monitor.http_get('https://api.bilibili.com'+path,
        referer='https://www.bilibili.com/', timeout=15))
    if data.get('code') != 0:
        raise ValueError('Bilibili metadata code '+str(data.get('code')))
    return data['data']



def reference_metadata(bvid, account):
    try:
        d=get_api('/x/web-interface/view?bvid='+bvid)
    except Exception:
        matches=monitor.BilibiliSearchSource({'pages':1},{})._fetch_via_api(bvid)
        row=next((r for r in matches if r.get('bvid')==bvid and r.get('up')==account['name']),None)
        if not row:
            raise ValueError('No exact reference ID and uploader match')
        return dict(title=row['title'],date=monitor.source_publish_time(row.get('pubdate'))[:10],
            dur=row.get('duration',0),play=row.get('view_count',0),metadata_provenance='bilibili_search_exact_id_author')
    if int((d.get('owner') or {}).get('mid',0)) != account['mid']:
        raise ValueError('Reference uploader did not match expected mid')
    return dict(title=d['title'],date=monitor.source_publish_time(d.get('pubdate'))[:10],
        dur=d.get('duration',0),play=(d.get('stat') or {}).get('view',0),metadata_provenance='bilibili_view')


def collection_item(bvid, part, parent):
    number = int(part['page'])
    duration = monitor.duration_seconds(part.get('duration'))
    eligible = 120 <= duration <= 5400
    author = (parent.get('owner') or {}).get('name', '')
    suffix = f':p{number}' if number > 1 else ''
    return dict(id=f'bilibili_search:{bvid}{suffix}',source='bilibili_collection',
        title=f"林园 P{number} {part.get('part','')}",
        url=f'https://www.bilibili.com/video/{bvid}'+(f'?p={number}' if number>1 else ''),
        publish_time=monitor.source_publish_time(parent.get('pubdate')),author=author,
        extra=json.dumps(dict(bvid=bvid,page=number,cid=part['cid'],duration=duration,
            collection_title=parent.get('title',''),source_role='mother_candidate' if eligible else 'catalog_only',
            direct_dispatch=bool(author) and eligible and author not in monitor.BLACKLIST_AUTHORS|{'园园滚雪球'},
            duration_eligible=eligible,metadata_status='known_duration',
            exclusion_reason=None if eligible else 'outside_120_to_5400_seconds'),ensure_ascii=False))


def apply_lineage(conn, catalog):
    """Never infer a confirmed media match from a similar title."""
    families={f['id']:f for f in catalog['families']}
    for row in conn.execute('SELECT id,url,extra FROM items').fetchall():
        item_id,url,raw=row
        extra=json.loads(raw or '{}')
        before=dict(extra)
        for f in families.values():
            if url in f.get('official_urls',[]) or url in f.get('mirror_urls',[]):
                extra['source_family']=f['id']
                extra['origin_role']='official_publisher' if url in f.get('official_urls',[]) else 'repost_or_lead'
                extra['reference_match_status']='needs_media_match'
        for ref in catalog['references']:
            if url.rstrip('/')==f"https://www.bilibili.com/video/{ref['bvid']}":
                extra.update(source_role='reference',direct_dispatch=False,
                    lineage_status=ref.get('match_status','needs_media_match'),candidate_families=ref['candidate_families'])
                if ref.get('visual_evidence'):extra['visual_evidence']=ref['visual_evidence']
        if extra!=before:
            conn.execute('UPDATE items SET extra=? WHERE id=?',(json.dumps(extra,ensure_ascii=False),item_id))
    conn.commit()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--offline',action='store_true')
    args=parser.parse_args()
    catalog=json.loads((BASE/'source_lineage.json').read_text())
    monitor.init_db()
    report=dict(version=1,checked_at=int(time.time()),new_reference_ids=[],errors=[],source_families=catalog['families'])
    seeds_path=BASE/'up_videos.json'
    seeds=json.loads(seeds_path.read_text())
    for ref in catalog['references']:
        bvid=ref['bvid']
        if bvid not in seeds:
            # Reported dates stay explicitly attributed; unknown duration is zero, never guessed.
            seeds[bvid]=dict(title=ref['title'],date=ref['reported_date'],dur=0,play=0,
                metadata_provenance=ref['metadata_provenance'])
        if not args.offline and seeds[bvid].get('metadata_provenance'):
            try:
                seeds[bvid].update(reference_metadata(bvid,catalog['reference_account']))
            except Exception as exc:
                report['errors'].append(dict(target=bvid,stage='reference_metadata',error=type(exc).__name__))
    seeds_path.write_text(json.dumps(seeds,ensure_ascii=False,indent=2)+'\n')
    refs=monitor.CompetitorReferenceSource({},{}).fetch(None)
    wanted={r['bvid'] for r in catalog['references']}
    refs=[r for r in refs if json.loads(r['extra'])['bvid'] in wanted]
    report['new_reference_ids']=[r['id'] for r in monitor.upsert_items(refs)]
    bvid=catalog['collection']['bvid']
    if not args.offline:
        try:
            try:
                parent=get_api('/x/web-interface/view?bvid='+bvid)
                pages=parent['pages']
            except Exception:
                pages=get_api('/x/player/pagelist?bvid='+bvid)
                with sqlite3.connect(monitor.DB_PATH) as conn:
                    old=conn.execute('SELECT author,publish_time,extra FROM items WHERE id=?',('bilibili_search:'+bvid,)).fetchone()
                parent=dict(owner=dict(name=old[0] if old else ''),pubdate=old[1] if old else '',
                    title=json.loads(old[2]).get('collection_title','') if old else '')
            report['new_collection_ids']=[r['id'] for r in monitor.upsert_items([collection_item(bvid,p,parent) for p in pages])]
            report['collection_api_pages']=len(pages)
        except Exception as exc:
            report['errors'].append(dict(target=bvid,stage='collection_metadata',error=type(exc).__name__))
        for article_id in catalog['official_short_ids']:
            try:
                info=monitor.YicaiVideoSource({}, {})._extract(article_id)
                monitor.upsert_items([dict(id='yicai_video:'+article_id,source='yicai_video',
                    title=info['title'],url=info['page_url'],author='第一财经',
                    publish_time=info['published_at'],extra=json.dumps(dict(article_id=article_id,
                        mp4_url=info['mp4_url'],published_at=info['published_at'],source_role='catalog_only',
                        direct_dispatch=False,origin_role='official_publisher',source_family='yicai_2026_08',
                        duration=52 if article_id=='103326920' else 30,
                        exclusion_reason='official_teaser_below_120_seconds'),ensure_ascii=False))])
            except Exception as exc:
                report['errors'].append(dict(target=article_id,stage='official_short_metadata',error=type(exc).__name__))
    with sqlite3.connect(monitor.DB_PATH) as conn:
        apply_lineage(conn,catalog)
        rows=conn.execute('SELECT id,url,extra FROM items').fetchall()
    report['references']=[dict(bvid=r['bvid'],catalogued=any(i=='competitor_reference:'+r['bvid'] for i,_,_ in rows),
        match_status=r.get('match_status','needs_media_match'),candidate_families=r['candidate_families'],
        title=seeds[r['bvid']]['title'],date=seeds[r['bvid']]['date'],duration_sec=seeds[r['bvid']]['dur'],
        metadata_provenance=seeds[r['bvid']].get('metadata_provenance','existing_catalog'),
        visual_evidence=r.get('visual_evidence')) for r in catalog['references']]
    report['collection_pages']=sorted({int(e['page']) for _,_,raw in rows for e in [json.loads(raw or '{}')]
        if e.get('bvid')==bvid and e.get('page')})
    report['missing_collection_pages']=sorted(set(range(1,catalog['collection']['expected_pages']+1))-set(report['collection_pages']))
    report['collection_catalog_only']=[dict(id=i,duration=e.get('duration')) for i,_,raw in rows for e in [json.loads(raw or '{}')]
        if e.get('bvid')==bvid and e.get('source_role')=='catalog_only']
    monitor.export_dashboard_data()
    out=BASE/'.automation/source_gap_audit.json'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ['new_reference_ids','missing_collection_pages','collection_catalog_only','errors']},ensure_ascii=False))
    assert all(r['catalogued'] for r in report['references'])


if __name__=='__main__':main()
