"""Block known restricted source pages on legacy retries as well as new picks.

No restriction found is NOT rights clearance. Actual source/media gates remain.
Publication-free diagnostic previews may inspect a source, never promote it.
"""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import urllib.request

BASE=Path(__file__).resolve().parent


def official_page_key(url):
    parsed=urlsplit(str(url or ''))
    if parsed.hostname in {'bilibili.com','www.bilibili.com','m.bilibili.com'}:
        match=re.fullmatch(r'/video/(BV[0-9A-Za-z]{10})/?',parsed.path)
        return ('bilibili',match[1]) if match else None
    if parsed.hostname not in {'www.yicai.com','yicai.com'}:return None
    match=re.fullmatch(r'/video/(\d+)\.html/?',parsed.path)
    return ('yicai',match[1]) if match else None


def restriction(url,seeds,policies=None):
    if policies is None:
        policies=json.loads((BASE/'source_restrictions.json').read_text())['pages']
    key=official_page_key(url)
    if key:
        for row in policies:
            if official_page_key(row.get('source_url'))==key:
                return row['reason']
    parsed=urlsplit(str(url or ''))
    if parsed.hostname not in {'bilibili.com','www.bilibili.com','m.bilibili.com'}:return None
    match=re.fullmatch(r'/video/(BV[0-9A-Za-z]{10})/?',parsed.path)
    if not match:return None
    for row in seeds:
        if row.get('bvid')!=match[1]:continue
        if row.get('no_reprint')==1:
            return '已知源页禁止转载；不得通过旧任务重试绕过准入限制'
        if row.get('direct_dispatch') is False or row.get('source_role') in {'reference','catalog_only'}:
            return '源页仅供目录/对标研究，不是可直接生产素材；不得通过旧任务重试绕过准入限制'
    return None


def source_urls(source,slug,state):
    sources=[source]
    entries=[e for e in state.get('dispatched') or [] if e.get('slug')==slug]
    if entries:
        entry=max(entries,key=lambda e:float(e.get('ts') or 0))
        # A staging URL does not erase the exact original page's restriction.
        sources.append(entry.get('source_url'))
    return sources


def live_bilibili_restriction(source,open_url=None):
    """Observe exact public source flags before any cached-media production.

    Unavailable/absent flags remain UNKNOWN, never reuse authorization. An
    observed explicit ban cannot fall through to a media/cache fallback.
    """
    key=official_page_key(source)
    if not key or key[0]!='bilibili':return None,{'status':'not_bilibili_page'}
    url='https://api.bilibili.com/x/web-interface/view?bvid='+key[1]
    observation=dict(source_bvid=key[1],evidence_url=url,status='unknown',rights_clearance=False)
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0',
        'Referer':'https://www.bilibili.com/'})
    try:
        with (open_url or urllib.request.urlopen)(request,timeout=10) as response:
            raw=response.read(200_001)
        if len(raw)>200_000:raise ValueError('Source view metadata exceeds budget')
        payload=json.loads(raw);data=payload.get('data') or {}
        if payload.get('code')!=0 or data.get('bvid')!=key[1]:
            raise ValueError('Source view metadata is unavailable or mismatched')
        flag=(data.get('rights') or {}).get('no_reprint')
        observation.update(status='observed',no_reprint=flag)
        if flag==1:
            return '实时官方源元数据明确禁止转载；不得通过旧种子、暂存链接或缓存母片绕过',observation
        return None,observation
    except Exception as exc:
        observation['error_type']=type(exc).__name__
        return None,observation


def check(source,slug,seeds,state,policies=None):
    sources=source_urls(source,slug,state)
    return next((reason for url in sources if (reason:=restriction(url,seeds,policies))),None)


def main():
    source=os.environ['RUN_SOURCE'];slug=os.environ['RUN_SLUG']
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',slug):
        raise SystemExit('Invalid production slug')
    seeds=json.loads((BASE/'bilibili_seeds.json').read_text())['seeds']
    state=json.loads((BASE/'.automation/fc_state.json').read_text())
    reason=check(source,slug,seeds,state)
    target=BASE/'deliver'/slug/'_tmp/source_quality.json'
    if not reason:
        observations=[];seen=set()
        for url in source_urls(source,slug,state):
            key=official_page_key(url)
            if not key or key[0]!='bilibili' or key in seen:continue
            seen.add(key)
            reason,observed=live_bilibili_restriction(url)
            observations.append(observed)
            if reason:break
        if observations:
            target.parent.mkdir(parents=True,exist_ok=True)
            (target.parent/'source_admission_observation.json').write_text(
                json.dumps(observations,ensure_ascii=False,indent=2)+'\n')
    if reason:
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(dict(passed=False,retryable=False,
            failure_stage='source-admission',reason=reason),ensure_ascii=False,indent=2)+'\n')
        raise SystemExit(reason)
    print('No known source restriction found; not a rights or media approval')


if __name__=='__main__':main()
