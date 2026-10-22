"""Read only the requested account's exact archive receipts; never change visibility."""
import argparse
import http.cookiejar
import json
import os
import time
from pathlib import Path
import urllib.error
import urllib.request


def archive_status(bvids, owner_mid, cookies_json, expected_titles=None, include_statistics=False):
    target=set(bvids)
    titles=set(expected_titles or [])
    result={'owner_mid':str(owner_mid),'requested_bvids':sorted(target),'videos':[],
            'verification_origin':'authenticated-creator-read','public_count':0}
    if titles:
        result['requested_titles']=sorted(titles)
    data=json.loads(cookies_json.lstrip('\ufeff'))
    entries=(data.get('cookie_info') or {}).get('cookies') or data.get('cookies')
    if entries is None:
        entries=[{'name':k,'value':v} for k,v in data.items() if isinstance(v,str)]
    values={e['name']:str(e['value']) for e in entries if e.get('name') and e.get('value')}
    if values.get('DedeUserID')!=str(owner_mid) or not values.get('SESSDATA'):
        raise ValueError('Authenticated account does not match the configured owner')
    jar=http.cookiejar.CookieJar()
    for name,value in values.items():
        jar.set_cookie(http.cookiejar.Cookie(0,name,value,None,False,'.bilibili.com',
            True,False,'/',True,True,None,False,None,None,{}))
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders=[('User-Agent','Mozilla/5.0 Chrome/126.0.0.0 Safari/537.36'),
        ('Referer','https://member.bilibili.com/platform/upload-manager/article'),
        ('Accept','application/json')]
    for page in range(1,4):
        url=('https://member.bilibili.com/x/web/archives?status=pubed,not_pubed,is_pubing'
             f'&pn={page}&ps=50&coop=1&interactive=1')
        try:
            with opener.open(url,timeout=30) as response:
                payload=json.load(response)
        except urllib.error.HTTPError as exc:
            result['http_status']=exc.code
            break
        result['api_code']=payload.get('code')
        if payload.get('code')!=0:
            break
        body=payload.get('data') or {}
        rows=body.get('arc_audits') or []
        observed_at=time.time()
        result['response_keys']=sorted(body)
        for row in rows:
            archive=row.get('Archive') or row.get('archive') or {}
            if archive.get('bvid') not in target and archive.get('title') not in titles:
                continue
            safe={k:archive[k] for k in ('bvid','aid','mid','title','state','state_desc',
                  'duration','is_only_self','no_public','had_passed','pubdate','ctime') if k in archive}
            safe['public']=(str(archive.get('mid'))==str(owner_mid)
                and archive.get('state')==0 and archive.get('is_only_self')==0
                and archive.get('no_public')==0)
            if include_statistics:
                counters=row.get('stat') or row.get('Stat') or archive.get('stat') or {}
                if not isinstance(counters,dict):counters={}
                safe.update(observed_at=observed_at,statistics_origin='authenticated-creator-read',
                    statistics={k:counters.get(k) for k in ('view','like','danmaku')},
                    available_stat_fields=sorted(counters),available_archive_fields=sorted(archive),
                    publication_clock_candidates={k:archive[k] for k in ('pubdate','pubtime','ctime') if k in archive})
            result['videos'].append(safe)
        if not rows or (target <= {r['bvid'] for r in result['videos']}
                        and titles <= {r.get('title') for r in result['videos']}):
            break
    result['found_count']=len(result['videos'])
    result['public_count']=sum(v['public'] for v in result['videos'])
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--bvid',action='append',required=True)
    parser.add_argument('--owner-mid',default='275211725')
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    try:
        result=archive_status(args.bvid,args.owner_mid,os.environ['BILIBILI_COOKIES'])
    except Exception as exc:
        result={'verification_origin':'authenticated-creator-read','error_type':type(exc).__name__}
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
