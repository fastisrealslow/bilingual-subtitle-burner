"""Own-account, free Bilibili collections; no copyright or publication changes.

Endpoints/payloads verified against the current official creator application.
Never bypass a permission/CAPTCHA error, retry a mutation, delete episodes,
move a video out of an existing collection, or touch an unmanaged collection.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

OWNER_MID=275211725
CREATOR='https://member.bilibili.com'
PUBLIC='https://api.bilibili.com'
MARKER='自动整理标识：ly-series:'


class CollectionError(Exception):
    """Only a safe error tag; never include response text, cookies or URLs."""


class Client:
    def __init__(self, cookie_json, session=None):
        import requests
        self.session=session or requests.Session()
        try:
            rows=json.loads(cookie_json.lstrip('\ufeff'))['cookie_info']['cookies']
            cookies={r['name']:r['value'] for r in rows}
            self.csrf=cookies['bili_jct']
            if not cookies.get('SESSDATA') or not self.csrf:
                raise ValueError()
        except (KeyError,TypeError,ValueError):
            raise CollectionError('cookies_missing_or_invalid') from None
        for name,value in cookies.items():
            self.session.cookies.set(name,value,domain='.bilibili.com')
        self.session.headers.update({'Referer':CREATOR+'/',
            'Origin':CREATOR,'User-Agent':'Mozilla/5.0'})

    def call(self, path, *, public=False, payload=None, params=None):
        # Fixed hosts, no redirect credential propagation or blind POST retries.
        query=dict(params or {})
        if payload is not None:query['csrf']=self.csrf
        try:
            r=self.session.request('POST' if payload is not None else 'GET',
                (PUBLIC if public else CREATOR)+path,params=query,
                json=payload,timeout=25,allow_redirects=False)
            if r.status_code!=200:raise CollectionError('http_'+str(r.status_code))
            value=r.json()
        except CollectionError:raise
        except Exception:raise CollectionError('transport_or_decode_error') from None
        if value.get('code')!=0:
            raise CollectionError('api_'+str(value.get('code')))
        return value.get('data')

    def identity(self):
        nav=self.call('/x/web-interface/nav',public=True)
        if not nav.get('isLogin') or nav.get('mid')!=OWNER_MID:
            raise CollectionError('login_or_owner_mismatch')
        white=self.call('/x/vupre/web/archive/white')
        return {'owner_mid':OWNER_MID,'logged_in':True,'season_enabled':bool(white.get('season'))}

    def seasons(self):
        rows=[]
        for page in range(1,21):
            d=self.call('/x2/creative/web/seasons',params=dict(pn=page,ps=30,
                order='mtime',sort='desc',draft=1,source=0))
            batch=d.get('seasons') or []
            rows.extend(batch)
            total=(d.get('page') or {}).get('total')
            if len(batch)<30 or (total is not None and len(rows)>=int(total)):
                return rows
        raise CollectionError('season_pagination_incomplete')

    def video(self,bvid):
        d=self.call('/x/web-interface/view',public=True,params={'bvid':bvid})
        if (d.get('owner') or {}).get('mid')!=OWNER_MID:
            raise CollectionError('video_owner_mismatch')
        pages=d.get('pages') or []
        if len(pages)!=1 or not pages[0].get('cid'):
            raise CollectionError('video_not_single_page')
        if (d.get('ugc_season') or {}).get('id'):
            d['_existing_season']=d['ugc_season']['id']
        return d

    def episodes(self,section):
        d=self.call('/x2/creative/web/season/section',params={'id':section})
        # Official creator getSection normalizes missing/null episodes to [].
        # A newly created empty section uses exactly this response shape.
        rows=d.get('episodes')
        if rows is None:rows=[]
        if not isinstance(rows,list):raise CollectionError('unrecognized_episode_response')
        return rows


def plans(state):
    """Same-source published batches only; not assumed full interviews/series."""
    result=[]
    for slug,receipt in sorted((state.get('published') or {}).items()):
        source=receipt.get('source_url')
        parts=[p for p in receipt.get('parts') or [] if p.get('bvid')
               and p.get('status','published')=='published']
        if not source or not 2<=len(parts)<=50:continue
        if len({p['bvid'] for p in parts})!=len(parts):continue
        if not all(isinstance(p.get('part_index'),int) and p['part_index']>=0 for p in parts):
            continue
        parts.sort(key=lambda p:(p['part_index'],p.get('ts',0)))
        if len({p['part_index'] for p in parts})!=len(parts):continue
        key=hashlib.sha256((slug+'\n'+source).encode()).hexdigest()[:20]
        first=str(parts[0].get('title') or '同场访谈').replace('\n',' ').strip()
        result.append(dict(key=key,slug=slug,
            title=('林园观点摘录｜'+first.removeprefix('林园：').removeprefix('林园:'))[:50],
            description='同场素材的已发布观点摘录，按制作分集顺序整理；各条独立成篇。\n'+MARKER+key,
            bvids=[p['bvid'] for p in parts]))
    return result


def managed(seasons,key):
    marker=MARKER+key
    hits=[r for r in seasons if marker in str((r.get('season') or {}).get('desc') or '')]
    if len(hits)>1:raise CollectionError('ambiguous_managed_collection')
    return hits[0] if hits else None


def section_id(row):
    sections=(row.get('sections') or {}).get('sections') or []
    if len(sections)!=1 or not sections[0].get('id'):
        raise CollectionError('ambiguous_collection_section')
    return sections[0]['id']


def sync(client,state,*,apply=False,max_new=1):
    identity=client.identity()
    result=dict(version=1,checked_at=int(time.time()),apply=apply,identity=identity,
        created=0,added=0,verified_collections=[],errors=[],plans=[])
    if not identity['season_enabled']:
        result['errors'].append({'error':'account_season_permission_disabled'})
        return result
    seasons=client.seasons()
    for plan in plans(state):
        detail=dict(key=plan['key'],slug=plan['slug'],episode_count=len(plan['bvids']))
        result['plans'].append(detail)
        if not apply:continue
        try:
            collection=managed(seasons,plan['key'])
            if collection is None and result['created']>=max_new:
                detail['status']='new_collection_budget_deferred';continue
            videos=[client.video(b) for b in plan['bvids']]
            season_id=(collection.get('season') or {}).get('id') if collection else None
            if any(v.get('_existing_season') not in (None,season_id) for v in videos):
                detail['status']='video_already_in_other_collection';continue
            # Verify eligibility before creating a collection, avoiding empty debris.
            for v in videos:
                if season_id and v.get('_existing_season')==season_id:continue
                client.call('/x2/creative/web/season/section/episode/verify',
                    params={'aid':v['aid'],'from':0})
            if collection is None:
                cover=videos[0].get('pic','')
                if cover.startswith('http://'):cover='https://'+cover[7:]
                from urllib.parse import urlsplit
                host=urlsplit(cover).hostname or ''
                if not host.endswith('.hdslb.com') or not cover.startswith('https://'):
                    raise CollectionError('invalid_platform_cover')
                created=client.call('/x2/creative/web/season/add',payload={
                    'cover':cover,'title':plan['title'],'desc':plan['description'],'captcha_token':''})
                if not isinstance(created,int) or isinstance(created,bool) or created<=0:
                    raise CollectionError('unrecognized_create_response')
                result['created']+=1
                seasons=client.seasons()
                collection=managed(seasons,plan['key'])
                if not collection or collection['season']['id']!=created:
                    raise CollectionError('created_collection_not_visible')
                season_id=created
            section=section_id(collection)
            existing=client.episodes(section)
            expected={v['aid'] for v in videos}
            if any(e.get('aid') not in expected for e in existing):
                raise CollectionError('unmanaged_episode_present')
            current={e['aid'] for e in existing}
            missing=[v for v in videos if v['aid'] not in current]
            if missing:
                client.call('/x2/creative/web/season/section/episodes/add',payload={
                    'sectionId':section,'csrf':client.csrf,'episodes':[
                        dict(aid=v['aid'],cid=v['pages'][0]['cid'],title=v['title'],charging_pay=0)
                        for v in missing]})
                result['added']+=len(missing)
            episodes=client.episodes(section)
            if len(episodes)!=len(videos) or {e.get('aid') for e in episodes}!=expected:
                raise CollectionError('episode_readback_mismatch')
            by_aid={e['aid']:e for e in episodes}
            desired=[by_aid[v['aid']] for v in videos]
            if [e['aid'] for e in episodes]!=[e['aid'] for e in desired]:
                client.call('/x2/creative/web/season/section/edit',payload={
                    'section':dict(id=section,type=1,seasonId=season_id,title='正片'),
                    'sorts':[dict(id=e['id'],sort=n) for n,e in enumerate(desired,1)],
                    'captcha_token':''})
                episodes=client.episodes(section)
                if [e.get('aid') for e in episodes]!=[v['aid'] for v in videos]:
                    raise CollectionError('sort_readback_mismatch')
            detail.update(status='verified',season_id=season_id)
            result['verified_collections'].append(dict(season_id=season_id,key=plan['key'],
                bvids=plan['bvids'],episode_count=len(episodes)))
        except CollectionError as exc:
            detail['status']='blocked';result['errors'].append({'key':plan['key'],'error':str(exc)})
            # Stop all mutations on auth/permission/captcha/unknown outcomes.
            break
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true')
    base=Path(__file__).resolve().parent
    ap.add_argument('--state',type=Path,default=base/'.automation/fc_state.json')
    ap.add_argument('--output',type=Path,default=base/'.automation/collection_sync_report.json')
    args=ap.parse_args()
    try:
        result=sync(Client(os.environ.get('BILIBILI_COOKIES','')),
                    json.loads(args.state.read_text()),apply=args.apply)
    except CollectionError as exc:
        result=dict(checked_at=int(time.time()),apply=args.apply,errors=[{'error':str(exc)}])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    from batch_delivery import write_json
    write_json(args.output,result)
    print(json.dumps(result,ensure_ascii=False))
    if result.get('errors'):raise SystemExit(1)


if __name__=='__main__':main()
