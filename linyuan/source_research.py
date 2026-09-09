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
from source_gap_backfill import reference_metadata

BASE=Path(__file__).resolve().parent
STATE=BASE/'.automation/source_research_state.json'
QUERIES={
 'maotai_table_exchange':['林园 茅台 股东 聚餐','林园 茅台 晚宴 红衬衫'],
 'fudan_2026_05':['林园 复旦 2026 5月23 完整版'],
 'gelong_conversation':['格隆对话林园 完整版','格隆博士会客厅 林园'],
 'hnw_course':['林园 高净值研究院 2026 完整版'],
 'investor_call':['林园 投资者连线 完整版'],
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
        command=[sys.executable,str(BASE/'ci_fetch_yicai.py'),'--url',url,'--out',str(path)]
    elif host in {'m.weibo.cn','weibo.com','www.weibo.com','www.douyin.com'}:
        command=[sys.executable,'-m','yt_dlp','--continue','--socket-timeout','30','--retries','2',
            '-f','bv*[height<=1080]+ba/b[height<=1080]/b','--merge-output-format','mp4','-o',str(path),url]
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
    offsets=range(0,len(mother)-len(reference)+1,3)
    scored=[]
    for off in offsets:
        distances=[(ref[i]^mother[off+8+i]).bit_count() for i in range(0,len(ref),stride)]
        ratio=sum(d<=4 for d in distances)/len(distances)
        if ratio>=.75:scored.append((ratio,off))
    if not scored:return None
    ratio,off=max(scored)
    return dict(status='audio_match_candidate',matched_ratio=round(ratio,3),
        mother_fingerprint_offset=off,original_publisher_confirmed=False)


def discover(catalog,state,max_queries):
    now=time.time();records=[]
    for _ in range(max_queries):
        queries=[(f,q) for f,qs in QUERIES.items() for q in qs]
        index=state.get('search_cursor',0)%len(queries)
        family,query=queries[index];state['search_cursor']=index+1
        search_key=family+'|'+query
        old=state.setdefault('searches',{}).get(search_key,{})
        if old.get('next_retry_at',0)>now:continue
        try:
            source=monitor.BilibiliSearchSource(dict(keyword=query,pages=1),{})
            rows=source.fetch(None)
            accepted=[]
            for row in rows:
                extra=json.loads(row['extra'])
                if row.get('author') in monitor.BLACKLIST_AUTHORS|{'园园滚雪球'}:continue
                duration=extra.get('duration',0)
                if '林园' not in row['title'] or duration<120 or duration>5400:continue
                extra.update(source_family=family,origin_role='unverified_publisher',
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
        return dict(status='searched',new_bvids=added)
    except Exception as exc:return dict(status='retry_next_run',error=type(exc).__name__)


def seed_jobs(catalog,state,discovered):
    jobs=state.setdefault('jobs',{})
    def add(url,role,family,priority):
        key=key_for(url)
        jobs.setdefault(key,dict(url=url,role=role,family=family,priority=priority,status='pending',attempts=0))
    # First verify the previously timed-out full speech and the untested course.
    for priority,family in enumerate(['fudan_2026_05','hnw_course','gelong_conversation'],1):
        f=next(f for f in catalog['families'] if f['id']==family)
        for index,url in enumerate(f.get('mirror_urls',[])):
            add(url,'mother',family,priority if index==0 else 4)
    for ref in catalog['references'][:1]:add('https://www.bilibili.com/video/'+ref['bvid'],'reference','maotai_table_exchange',0)
    for row in discovered:add(row['url'],'mother',json.loads(row['extra'])['source_family'],5)
    seeds=json.loads((BASE/'up_videos.json').read_text())
    recent=sorted(seeds.items(),key=lambda p:p[1].get('date',''),reverse=True)[:10]
    for bvid,video in recent:
        if video.get('metadata_provenance','').startswith('bilibili_'):
            add('https://www.bilibili.com/video/'+bvid,'reference','unresolved',4)
    for f in catalog['families']:
        for url in f.get('candidate_urls',[]):add(url,'mother',f['id'],2)


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
    ordered=sorted(state['jobs'].items(),key=lambda p:(p[1].get('priority',2),p[1].get('last_attempt_at',0),p[0]))
    for key,job in ordered:
        evidence_dir=args.cache/key
        if job.get('status')=='inspected' and not (evidence_dir/'audio.json').is_file():
            job.update(status='pending',next_retry_at=0)
        if not due(job,now) or processed>=args.max_items:continue
        processed+=1;raw=args.cache/(key+'.mp4')
        try:
            if not raw.is_file():fetch_media(job['url'],raw,args.download_budget)
            evidence=inspect_media(raw,evidence_dir)
            job.update(status='inspected',last_attempt_at=time.time(),next_retry_at=0,last_error=None,
                evidence=evidence,evidence_run_id=os.environ.get('GITHUB_RUN_ID'))
            print('Media inspected:',job['url'],round(evidence['duration_sec'],1),'seconds',flush=True)
            # Validated fingerprint/frame evidence survives subsequent runners; raw mothers need not.
            raw.unlink(missing_ok=True)
        except Exception as exc:
            failed(job,exc,time.time());raw.unlink(missing_ok=True)
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
        pending=sum(j.get('status')!='inspected' for j in state['jobs'].values()),audio_matches=matches,
        reference_refresh=state['reference_refresh'],jobs=state['jobs'],searches=state['searches'])
    write_json(BASE/'.automation/source_research_report.json',report)
    write_json(args.evidence/'report.json',report)
    monitor.export_dashboard_data()
    print(json.dumps({k:v for k,v in report.items() if k not in {'jobs','searches'}},ensure_ascii=False))


if __name__=='__main__':main()
