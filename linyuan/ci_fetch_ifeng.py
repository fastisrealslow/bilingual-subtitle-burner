"""Download the main article's public video, never a sidebar recommendation."""
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request
from urllib.parse import urlsplit


ARTICLE_HOSTS={'original.ifeng.com','finance.ifeng.com','v.ifeng.com'}


def article_video(page, article_url):
    parsed=urlsplit(article_url)
    if parsed.scheme!='https' or parsed.hostname not in ARTICLE_HOSTS or not re.fullmatch(r'/c/[A-Za-z0-9]+',parsed.path):
        raise ValueError('需要凤凰网公开文章地址')
    match=re.search(r'\bvar\s+allData\s*=\s*',page)
    if not match:raise ValueError('官方文章缺少可解析的正文数据')
    data,_=json.JSONDecoder().raw_decode(page[match.end():])
    doc=data.get('docData') or {}
    videos=[row.get('data') or {} for row in (doc.get('contentData') or {}).get('contentList',[])
            if row.get('type')=='video']
    provenance='docData.contentData.contentList[type=video]'
    # Phoenix's standalone official video pages use docData.videoPlayUrl,
    # not an article contentList. Never look through relatedVideos/videoStream.
    if not doc.get('contentData') and doc.get('videoPlayUrl'):
        videos=[dict(playUrl=doc['videoPlayUrl'],guid=doc.get('guid'),
                     duration=doc.get('duration'),title=doc.get('title'))]
        provenance='docData.videoPlayUrl'
    if len(videos)!=1:
        raise ValueError('正文没有唯一视频附件；不使用推荐区、图片或猜测链接')
    video=videos[0];url=video.get('playUrl','');media=urlsplit(url)
    if media.scheme!='https' or not re.fullmatch(r'video\d+\.ifeng\.com',media.hostname or '') or not media.path.endswith('.mp4'):
        raise ValueError('正文视频不是公开的凤凰视频MP4')
    if not video.get('guid') or not isinstance(video.get('duration'),(int,float)) or video['duration']<=0:
        raise ValueError('正文视频缺少身份或时长')
    return dict(article=article_url,article_title=doc.get('title'),published_at=doc.get('newsTime'),
        publisher=(doc.get('fhhAccountDetail') or {}).get('catename'),
        video_title=video.get('title'),guid=video['guid'],media_url=url,
        declared_duration=video['duration'],provenance=provenance,
        recording_date=None,production_quality='not_evaluated')


def fetch(url,output):
    from ci_fetch_bilibili import download_one, validate_media, sha256_file
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.ifeng.com/'})
    with urllib.request.urlopen(request,timeout=45) as response:
        page=response.read(12*1024*1024).decode('utf-8')
    origin=article_video(page,url)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    identity={k:origin[k] for k in ('article','guid','media_url')}
    proof=output.with_suffix('.origin.json')
    if output.exists() and proof.exists():
        try:
            saved=json.loads(proof.read_text())
            with output.open('rb') as handle: digest=sha256_file(handle)
            if all(saved.get(k)==v for k,v in identity.items()) and saved.get('sha256')==digest:
                valid=validate_media(output)
                if abs(valid['duration']-origin['declared_duration'])<=3:
                    return output
        except (ValueError,RuntimeError,OSError):
            pass
    # Isolate the exact article, document GUID and public representation.
    # The existing whole _src cache already survives failed source steps.
    key=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:20]
    temporary=output.with_name(output.stem+'.official-'+key+'.mp4')
    download_one(urllib.request.build_opener(),[origin['media_url']],url,temporary,
        attempts=1,deadline=time.monotonic()+750,
        stop_http_statuses=(401,403,412,429,451,567))
    valid=validate_media(temporary)
    if abs(valid['duration']-origin['declared_duration'])>3:
        raise ValueError('实际媒体时长与正文附件不符')
    with temporary.open('rb') as handle:digest=sha256_file(handle)
    origin.update(sha256=digest,duration_sec=valid['duration'])
    temporary.replace(output)
    proof.write_text(json.dumps(origin,ensure_ascii=False,indent=2)+'\n')
    return output


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();fetch(args.url,args.out)
