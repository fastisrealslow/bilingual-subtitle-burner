import json,os,time,urllib.request,concurrent.futures
from pathlib import Path
from datetime import datetime,timezone,timedelta
OUT=Path('analytics30');OUT.mkdir(exist_ok=True);(OUT/'covers').mkdir(exist_ok=True)
now=time.time(); cutoff=now-30*86400
raw=json.loads(os.environ['BILIBILI_COOKIES']);entries=(raw.get('cookie_info') or {}).get('cookies') or raw.get('cookies') or []
cookies={x['name']:str(x['value']) for x in entries};assert cookies.get('DedeUserID')=='275211725'
headers={'Cookie':'; '.join(k+'='+v for k,v in cookies.items()),'User-Agent':'Mozilla/5.0','Referer':'https://member.bilibili.com/'}
def get(url,auth=False):
    req=urllib.request.Request(url,headers=headers if auth else {'User-Agent':'Mozilla/5.0','Referer':'https://www.bilibili.com/'})
    with urllib.request.urlopen(req,timeout=30) as r:return json.load(r)
rows=[]; pages=[]
for pn in range(1,11):
    d=get(f'https://member.bilibili.com/x/web/archives?status=pubed,not_pubed,is_pubing&pn={pn}&ps=50&coop=1&interactive=1',True)
    if d.get('code')!=0:raise RuntimeError('creator_api_code='+str(d.get('code')))
    body=d.get('data') or {};batch=body.get('arc_audits') or [];pages.append({'page':pn,'count':len(batch),'page_info':body.get('page')})
    for row in batch:
        a=row.get('Archive') or row.get('archive') or {};stat=row.get('stat') or row.get('Stat') or {}
        if str(a.get('mid'))!='275211725':continue
        pub=a.get('pubdate') or a.get('ctime') or 0
        if not isinstance(pub,(int,float)) or pub<cutoff:continue
        safe={k:a.get(k) for k in ('aid','bvid','title','pic','cover','duration','state','state_desc','is_only_self','no_public','had_passed','pubdate','ctime')}
        safe['public']=a.get('state')==0 and a.get('is_only_self')==0 and a.get('no_public')==0
        safe['creator_stat']={k:stat.get(k) for k in ('view','play','like','coin','favorite','share','reply','danmaku') if k in stat}
        safe['archive_fields']=sorted(a);safe['row_fields']=sorted(row)
        rows.append(safe)
    if len(batch)<50:break
    time.sleep(.6)
def detail(row):
    try:
        d=get('https://api.bilibili.com/x/web-interface/view?bvid='+row['bvid'])
        row['public_api_code']=d.get('code')
        if d.get('code')==0:
            a=d['data'];assert str(a['owner']['mid'])=='275211725'
            row.update(stat=a.get('stat'),pic=a.get('pic'),duration=a.get('duration'),pubdate=a.get('pubdate'),public_title=a.get('title'))
    except Exception as e:row['detail_error']=type(e).__name__
    url=row.get('pic') or row.get('cover')
    if url:
        if url.startswith('//'):url='https:'+url
        if url.startswith('http:'):url='https:'+url[5:]
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(req,timeout=30) as r:data=r.read()
            (OUT/'covers'/f"{row['bvid']}.jpg").write_bytes(data);row['cover_file']=f"covers/{row['bvid']}.jpg"
        except Exception as e:row['cover_error']=type(e).__name__
    return row
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows=list(pool.map(detail,rows))
report={'collected_at':datetime.fromtimestamp(now,timezone(timedelta(hours=8))).isoformat(),'cutoff_at':datetime.fromtimestamp(cutoff,timezone(timedelta(hours=8))).isoformat(),'owner_mid':275211725,'pages':pages,'rows':rows}
(OUT/'snapshot.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'rows':len(rows),'public':sum(x['public'] for x in rows),'covers':sum('cover_file'in x for x in rows),'public_stats':sum('stat'in x for x in rows),'pages':pages},ensure_ascii=False))
