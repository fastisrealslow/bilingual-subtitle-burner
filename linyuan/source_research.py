"""Bounded daily source discovery, resumable media evidence and audio match candidates.
No result here approves a video for publication or changes production quality gates.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit
import monitor_v2 as monitor
import editorial_policy as editorial
from source_gap_backfill import reference_metadata

BASE=Path(__file__).resolve().parent
STATE=BASE/'.automation/source_research_state.json'
QUERIES={
 'maotai_table_exchange':['林园 茅台 股东 聚餐','林园 茅台 晚宴 红衬衫'],
 'fudan_2026_05':['林园 复旦 2026 5月23 完整版'],
 'gelong_conversation':['格隆对话林园 完整版','格隆博士会客厅 林园'],
 'hnw_course':['林园 高净值研究院 2026 完整版'],
 'investor_call':['林园 投资者连线 完整版'],
 'phoenix_2026_09':['林园 凤凰湾区财经论坛 2026 完整版','凤凰网财经 林园 AI 2026 9月'],
 'dadao_2026_06':['大道财经 对话林园 2026 6 完整版'],
 'zhejiang_2026_01':['林园 浙江大学 2026 1月9日 完整版'],
 'historical_long_leads_20261010':['林园 2025 5 采访 完整版','林园 2006 走进资本市场 完整版'],
}


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)


def key_for(url):return hashlib.sha256(url.encode()).hexdigest()[:20]


def failed(job,exc,now):
    job['attempts']=job.get('attempts',0)+1
    job.update(status='retry_wait',last_error=type(exc).__name__,last_attempt_at=now,
        next_retry_at=now+min(86400,21600*2**min(job['attempts']-1,2)))


def due(job,now):
    return job.get('status')!='inspected' and job.get('next_retry_at',0)<=now


def bounded_run(command,timeout,**kw):
    """Kill a timed-out downloader's entire process group, including ffmpeg/curl."""
    process=subprocess.Popen(command,start_new_session=True,**kw)
    try:
        stdout,stderr=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL)
        process.communicate()
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode,command)
    return stdout,stderr


def fetch_media(url,path,budget):
    host=urlsplit(url).hostname or ''
    if host in {'www.bilibili.com','bilibili.com'}:
        command=[sys.executable,str(BASE/'ci_fetch_bilibili.py'),'--url',url,'--out',str(path)]
    elif host in {'www.yicai.com','yicai.com'}:
        command=[sys.executable,str(BASE/'ci_fetch_yicai.py'),'--url',url,'--out',str(path),'--budget',str(budget)]
    elif host in {'m.weibo.cn','weibo.com','www.weibo.com','www.douyin.com'}:
        command=[sys.executable,'-m','yt_dlp','--continue','--socket-timeout','30','--retries','2',
            '-f','bv*[height<=1080]+ba/b[height<=1080]/b','--merge-output-format','mp4','-o',str(path),url]
    elif host in {'original.ifeng.com','finance.ifeng.com','v.ifeng.com'}:
        command=[sys.executable,str(BASE/'ci_fetch_ifeng.py'),'--url',url,'--out',str(path)]
    else:raise ValueError('Unsupported source host')
    bounded_run(command,budget,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def inspect_media(path,out):
    from ci_fetch_bilibili import validate_media
    valid=validate_media(path)
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    out.mkdir(parents=True,exist_ok=True)
    raw,_=bounded_run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0',
        '-f','chromaprint','-fp_format','raw','pipe:1'],180,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if len(raw)<80:raise ValueError('Insufficient audio fingerprint')
    values=list(struct.unpack(f'<{len(raw)//4}I',raw[:len(raw)//4*4]))
    write_json(out/'audio.json',dict(sha256=sha,values=values))
    frames=[]
    for n,seconds in enumerate([min(5,valid['duration']/4),min(15,valid['duration']/2),valid['duration']/2]):
        target=out/f'frame-{n}.jpg'
        bounded_run(['ffmpeg','-v','error','-y','-ss',str(seconds),'-i',str(path),'-frames:v','1',
            '-vf','scale=640:-2',str(target)],40,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        frames.append(dict(file=target.name,seconds=round(seconds,2)))
    evidence=dict(sha256=sha,duration_sec=valid['duration'],frames=frames,
        audio_fingerprint_count=len(values),media_integrity='passed',production_quality='not_evaluated')
    write_json(out/'media.json',evidence)
    return evidence


def audio_candidate(reference,mother):
    """Conservative contiguous acoustic match; never proves the original publisher."""
    if len(reference)<80 or len(mother)<len(reference):return None
    ref=reference[8:-8]
    # Remove low-information silence/static fingerprints before matching.
    if len(set(ref))<20:return None
    stride=max(1,len(ref)//80)
    # All three phases matter: stepping by 3 silently missed offsets 1/2.
    offsets=range(0,len(mother)-len(reference)+1)
    scored=[]
    for off in offsets:
        distances=[(ref[i]^mother[off+8+i]).bit_count() for i in range(0,len(ref),stride)]
        ratio=sum(d<=4 for d in distances)/len(distances)
        if ratio>=.75:scored.append((ratio,off))
    if not scored:return None
    ratio,off=max(scored)
    return dict(status='audio_match_candidate',matched_ratio=round(ratio,3),
        mother_fingerprint_offset=off,original_publisher_confirmed=False)


def prepare_cache_jobs(jobs,cache):
    """Cache eviction is not media failure. New investigations precede reconstruction."""
    for key,job in jobs.items():
        audio=cache/key/'audio.json'
        evidence=job.get('evidence') or {}
        historical=evidence.get('media_integrity')=='passed'
        usable=False
        if historical and audio.is_file():
            try:
                saved=json.loads(audio.read_text())
                usable=(saved.get('sha256')==evidence.get('sha256')
                        and len(saved.get('values') or [])==evidence.get('audio_fingerprint_count')
                        and len(saved.get('values') or [])>=80)
            except (ValueError,OSError,TypeError):pass
        job['fingerprint_cache_available']=usable
        job['cache_recovery_needed']=historical and not usable
        if usable:
            job.update(status='inspected',next_retry_at=0)
        elif job.get('status')=='inspected':
            job.update(status='pending',next_retry_at=0)
    return sorted(jobs.items(),key=lambda p:(p[1].get('cache_recovery_needed',False),
        p[1].get('priority',2),p[1].get('last_attempt_at',0),p[0]))


def long_mother_rows(rows):
    """Research full recordings first; a search query is never event-date evidence."""
    accepted=[]
    for row in rows:
        if row.get('author') in monitor.BLACKLIST_AUTHORS|{'园园滚雪球'}:continue
        try:duration=float(json.loads(row.get('extra') or '{}').get('duration') or 0)
        except (ValueError,TypeError):continue
        if '林园' not in row.get('title','') or not 900<=duration<=5400:continue
        if any(word in row['title'] for word in ('解说林园','解读林园','混剪','鬼畜')):continue
        accepted.append((duration,row))
    return [row for _,row in sorted(accepted,key=lambda p:-p[0])[:4]]


def discover(catalog,state,max_queries):
    now=time.time();records=[]
    for request_index in range(max_queries):
        queries=[(f,q) for f,qs in QUERIES.items() for q in qs]
        index=state.get('search_cursor',0)%len(queries)
        recent=[(f,q) for f,q in queries if f=='phoenix_2026_09'
                and state.get('searches',{}).get(f+'|'+q,{}).get('next_retry_at',0)<=now]
        if request_index==0 and recent:
            family,query=recent[0]
        else:
            family,query=queries[index];state['search_cursor']=index+1
        search_key=family+'|'+query
        old=state.setdefault('searches',{}).get(search_key,{})
        if old.get('next_retry_at',0)>now:continue
        try:
            source=monitor.BilibiliSearchSource(dict(keyword=query,pages=1),{})
            rows=source.fetch(None)
            accepted=[]
            for row in long_mother_rows(rows):
                extra=json.loads(row['extra'])
                extra.update(source_family='unresolved',candidate_families=[family],origin_role='unverified_publisher',
                    source_role='mother_candidate',direct_dispatch=True,reference_match_status='needs_media_match',
                    discovery_query=query)
                row.update(source='reference_origin_search',extra=json.dumps(extra,ensure_ascii=False))
                accepted.append(row)
                if len(accepted)>=4:break
            # Existing source evidence takes precedence over a fresh search title.
            with sqlite3.connect(monitor.DB_PATH) as conn:
                existing={r[0] for r in conn.execute('SELECT id FROM items')}
            new=monitor.upsert_items([r for r in accepted if r['id'] not in existing])
            records.extend(accepted)
            state['searches'][search_key]=dict(status='searched',checked_at=now,next_retry_at=now+43200,
                found=len(accepted),new_ids=[r['id'] for r in new])
            print('Source search:',family,'candidates',len(accepted),'new',len(new),flush=True)
        except Exception as exc:
            failed(old,exc,now);state['searches'][search_key]=old
        write_json(STATE,state)
    return records


def refresh_recent_references(catalog):
    """Search the exact uploader name; only verified owner-name rows become references."""
    path=BASE/'up_videos.json';seeds=json.loads(path.read_text());added=[]
    from reference_audit import discover_collections
    before=set(seeds)
    seeds,collection_report=discover_collections(seeds)
    try:
        rows=monitor.BilibiliSearchSource(dict(pages=1),{})._fetch_via_api(catalog['reference_account']['name'])
        for row in rows:
            if row.get('up')!=catalog['reference_account']['name'] or '林园' not in row.get('title',''):continue
            bvid=row['bvid']
            if bvid not in seeds:added.append(bvid)
            seeds[bvid]=dict(title=row['title'],date=monitor.source_publish_time(row.get('pubdate'))[:10],
                dur=row.get('duration',0),play=row.get('view_count',0),metadata_provenance='bilibili_search_exact_id_author')
        write_json(path,seeds)
        monitor.upsert_items(monitor.CompetitorReferenceSource({},{}).fetch(None))
        return dict(status='searched',new_bvids=sorted(set(added)|set(seeds)-before),
                    collections=collection_report)
    except Exception as exc:
        # Successfully enumerated public collections survive a failed search.
        write_json(path,seeds)
        return dict(status='retry_next_run',error=type(exc).__name__,
                    new_bvids=sorted(set(seeds)-before),collections=collection_report)


def seed_jobs(catalog,state,discovered):
    jobs=state.setdefault('jobs',{})
    def add(url,role,family,priority):
        key=key_for(url)
        jobs.setdefault(key,dict(url=url,role=role,family=family,priority=priority,status='pending',attempts=0))
        # Existing queue entries should receive a new priority, not stay stale.
        if jobs[key].get('role')==role:
            jobs[key]['priority']=min(priority,jobs[key].get('priority',priority))
            if family!='unresolved':jobs[key]['family']=family
    # First verify the previously timed-out full speech and the untested course.
    for priority,family in enumerate(['fudan_2026_05','hnw_course','gelong_conversation'],1):
        f=next(f for f in catalog['families'] if f['id']==family)
        for index,url in enumerate(f.get('mirror_urls',[])):
            add(url,'mother',family,priority if index==0 else 4)
    for ref in catalog['references'][:1]:add('https://www.bilibili.com/video/'+ref['bvid'],'reference','maotai_table_exchange',0)
    for row in discovered:
        extra=json.loads(row['extra'])
        family=(extra.get('candidate_families') or [extra.get('source_family','unresolved')])[0]
        add(row['url'],'mother',family,0 if family=='phoenix_2026_09' else 5)
        jobs[key_for(row['url'])]['family_status']='search_clue_not_verified'
    seeds=json.loads((BASE/'up_videos.json').read_text())
    recent=sorted(seeds.items(),key=lambda p:p[1].get('date',''),reverse=True)[:30]
    for bvid,video in recent:
        if video.get('metadata_provenance','').startswith('bilibili_'):
            # Other speakers remain research references, not LinYuan material.
            if '林园' not in video.get('title','') or '余军' in video.get('title',''):continue
            family=('phoenix_2026_09' if '凤凰湾区财经论坛2026' in video.get('description','')
                    else 'unresolved')
            add('https://www.bilibili.com/video/'+bvid,'reference',family,1)
    for f in catalog['families']:
        for url in f.get('official_urls',[]):
            from source_priority import VERIFIED_PRIMARY_PAGES
            primary=url in VERIFIED_PRIMARY_PAGES or url in f.get('verified_primary_urls',[])
            add(url,'mother',f['id'],-1 if primary else 2)
            jobs[key_for(url)]['publisher_status']='verified_primary_page' if primary else 'catalog_lead_not_primary_proof'
            job=jobs[key_for(url)]
            if primary and 'yicai.com' in url and not job.get('download_resume_policy_version'):
                job['download_resume_policy_version']=1
                if job.get('last_error')=='TimeoutExpired' and not job.get('evidence'):
                    job['next_retry_at']=0  # One bounded retry after this concrete downloader fix.
        for url in f.get('candidate_urls',[]):add(url,'mother',f['id'],2)
        for url in f.get('research_only_urls',[]):add(url,'mother',f['id'],6)
        for url,proof in f.get('visual_reclassifications',{}).items():
            add(url,'mother',f['id'],6)
            jobs[key_for(url)].update(family=f['id'],priority=6,
                family_status='visual_evidence_not_primary_publisher',visual_classification=proof)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--max-items',type=int,default=3)
    parser.add_argument('--max-queries',type=int,default=3)
    parser.add_argument('--download-budget',type=int,default=300)
    parser.add_argument('--cache',type=Path,default=BASE/'.source-research-cache')
    parser.add_argument('--evidence',type=Path,default=Path('/tmp/source-research-evidence'))
    args=parser.parse_args()
    catalog=json.loads((BASE/'source_lineage.json').read_text())
    state=json.loads(STATE.read_text()) if STATE.exists() else dict(version=1,jobs={},searches={})
    monitor.init_db()
    state['reference_refresh']=refresh_recent_references(catalog)
    discovered=discover(catalog,state,args.max_queries)
    seed_jobs(catalog,state,discovered)
    args.cache.mkdir(parents=True,exist_ok=True);args.evidence.mkdir(parents=True,exist_ok=True)
    processed=0;now=time.time()
    ordered=prepare_cache_jobs(state['jobs'],args.cache)
    for key,job in ordered:
        evidence_dir=args.cache/key
        if not due(job,now) or processed>=args.max_items:continue
        processed+=1;raw=args.cache/(key+'.mp4')
        from ci_fetch_yicai import partial_path
        partial=partial_path(raw)
        before_bytes=partial.stat().st_size if partial.exists() else 0
        try:
            if not raw.is_file():fetch_media(job['url'],raw,args.download_budget)
            evidence=inspect_media(raw,evidence_dir)
            job.update(status='inspected',last_attempt_at=time.time(),next_retry_at=0,last_error=None,
                cache_recovery_needed=False,fingerprint_cache_available=True,
                evidence=evidence,evidence_run_id=os.environ.get('GITHUB_RUN_ID'))
            print('Media inspected:',job['url'],round(evidence['duration_sec'],1),'seconds',flush=True)
            # Validated fingerprint/frame evidence survives subsequent runners; raw mothers need not.
            raw.unlink(missing_ok=True)
        except Exception as exc:
            failed(job,exc,time.time());raw.unlink(missing_ok=True)
            partial_bytes=partial.stat().st_size if partial.exists() else 0
            job['download_partial_bytes']=partial_bytes
            if partial_bytes>before_bytes:
                job['next_retry_at']=time.time()+1800
            print('Media retry queued:',job['url'],type(exc).__name__,flush=True)
        write_json(STATE,state)
    matches=[]
    references=[(k,j) for k,j in state['jobs'].items() if j.get('status')=='inspected' and j['role']=='reference']
    mothers=[(k,j) for k,j in state['jobs'].items() if j.get('status')=='inspected' and j['role']=='mother']
    for rk,r in references:
        rp=args.cache/rk/'audio.json'
        if not rp.exists():continue
        ref=json.loads(rp.read_text())['values']
        for mk,m in mothers:
            mp=args.cache/mk/'audio.json'
            if not mp.exists():continue
            match=audio_candidate(ref,json.loads(mp.read_text())['values'])
            if match:matches.append(dict(reference_url=r['url'],mother_url=m['url'],**match))
    import shutil
    for key,job in state['jobs'].items():
        folder=args.cache/key
        if folder.exists():shutil.copytree(folder,args.evidence/key,dirs_exist_ok=True)
    state.update(updated_at=int(time.time()),last_run_id=os.environ.get('GITHUB_RUN_ID'),audio_match_candidates=matches)
    write_json(STATE,state)
    report=dict(run_id=state['last_run_id'],processed=processed,inspected=sum(j.get('status')=='inspected' for j in state['jobs'].values()),
        historical_media_verified=sum((j.get('evidence') or {}).get('media_integrity')=='passed' for j in state['jobs'].values()),
        cache_recovery_pending=sum(j.get('cache_recovery_needed',False) for j in state['jobs'].values()),
        pending=sum(j.get('status')!='inspected' for j in state['jobs'].values()),audio_matches=matches,
        reference_refresh=state['reference_refresh'],jobs=state['jobs'],searches=state['searches'])
    write_json(BASE/'.automation/source_research_report.json',report)
    write_json(args.evidence/'report.json',report)
    monitor.export_dashboard_data()
    print(json.dumps({k:v for k,v in report.items() if k not in {'jobs','searches'}},ensure_ascii=False))


if __name__=='__main__':main()
