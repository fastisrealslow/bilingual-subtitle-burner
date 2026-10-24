"""Resolve first-party programme metadata, never a media/publication approval."""
from datetime import datetime
import html
import math
import re
from urllib.parse import urljoin, urlsplit
import urllib.request


def page_identity(url):
    parsed=urlsplit(url)
    match=re.fullmatch(r'/v/ip/typyq/(20\d{4})/t(20\d{6})_(\d+)\.htm',parsed.path)
    if (parsed.scheme!='https' or parsed.hostname!='www.p5w.net' or parsed.username
            or parsed.password or parsed.query or parsed.fragment or not match
            or match[1]!=match[2][:6]):
        raise ValueError('Expected an exact official Tianyu programme page')
    return match[2]+'_'+match[3]


def media_url(url,base):
    result=urljoin(base,html.unescape(url));parsed=urlsplit(result)
    if (parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.p5w.net')
            or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('Programme attachment must remain on the official public media domain')
    return result


def finite_duration(playlist):
    if (not playlist.lstrip().startswith('#EXTM3U')
            or not re.search(r'^#EXT-X-ENDLIST\s*$',playlist,re.M)
            or '#EXT-X-KEY' in playlist):
        raise ValueError('Expected a complete, public, unencrypted programme playlist')
    values=re.findall(r'^#EXTINF:([^,\r\n]+)',playlist,re.M)
    try:durations=[float(v) for v in values]
    except ValueError:raise ValueError('Invalid programme segment duration') from None
    if not durations or any(not math.isfinite(v) or v<=0 for v in durations):
        raise ValueError('Invalid programme duration evidence')
    return round(sum(durations),3)


def extract(url,read):
    """Read public page and its explicit playlist using the real page Referer."""
    identity=page_identity(url);page=read(url,'https://www.p5w.net/')
    title=re.search(r'<title>(.*?)</title>',page,re.S|re.I)
    stamp=re.search(r'<[^>]+\bid=[\"\x27]dTime[\"\x27][^>]*>\s*([^<]+)',page,re.I)
    attachments=set(re.findall(r'\bvar\s+videoUrl\s*=\s*[\"\x27]([^\"\x27]+)',page))
    if not title or not stamp or len(attachments)!=1:
        raise ValueError('Programme page lacks unique title, publication date or attachment')
    title=html.unescape(re.sub(r'<[^>]+>','',title[1])).split('_')[0].strip()
    published=datetime.fromisoformat(stamp[1].strip()).isoformat()
    if published[:10].replace('-','')!=identity.split('_')[0]:
        raise ValueError('Programme publication identity does not match its page')
    denied=bool(re.search(r'禁止转载|未经[^。\n]{0,24}(?:不得|禁止)[^。\n]{0,12}转载',
                          re.sub(r'<[^>]+>',' ',page)))
    result=dict(id=identity,title=title,page_url=url,author='全景网·天玉朋友圈',published_at=published,
        origin_role='official_publisher',recording_date_verified=False,
        metadata_provenance='p5w_page_videoUrl_finite_public_hls_v1',
        media_quality_approved=False,rights_clearance=False,no_reprint=1 if denied else None,
        duration=0,has_video=True,direct_dispatch=False,source_role='catalog_only')
    if denied:return result
    master_url=media_url(next(iter(attachments)),url);master=read(master_url,url)
    lines=master.splitlines();variants=[]
    for i,line in enumerate(lines[:-1]):
        if not line.startswith('#EXT-X-STREAM-INF:'):continue
        resolution=re.search(r'RESOLUTION=(\d+)x(\d+)',line)
        bandwidth=re.search(r'BANDWIDTH=(\d+)',line)
        if resolution and bandwidth and not lines[i+1].startswith('#'):
            width,height=map(int,resolution.groups())
            if 720<=height<=1920 and 960<=width<=3840:
                variants.append((height,int(bandwidth[1]),width,media_url(lines[i+1],master_url)))
    if not variants:raise ValueError('Official programme lacks native HD variant metadata')
    height,bandwidth,width,variant=max(variants)
    duration=finite_duration(read(variant,url))
    result.update(duration=duration,declared_resolution=dict(width=width,height=height),
                  public_master_url=master_url,public_variant_url=variant,bandwidth=bandwidth,
                  direct_dispatch=120<=duration<=5400,
                  source_role='mother_candidate' if 120<=duration<=5400 else 'catalog_only')
    return result


def resolve(url):
    """Use the page's video attachment, not its misleading audio fallback tag."""
    def read(target,referer):
        request=urllib.request.Request(target,headers={'User-Agent':'Mozilla/5.0','Referer':referer})
        with urllib.request.urlopen(request,timeout=20) as response:
            return response.read().decode('utf-8')
    metadata=extract(url,read)
    if not metadata['direct_dispatch']:
        raise ValueError('Programme is catalogue-only; no automatic media download')
    return dict(page_url=url,media_url=metadata['public_master_url'],
                declared_duration=metadata['duration'],provider='p5w',
                provenance=metadata['metadata_provenance'],metadata=metadata)
