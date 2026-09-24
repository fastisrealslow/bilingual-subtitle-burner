"""Download the main article's public video, never a sidebar recommendation."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
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
        declared_duration=video['duration'],provenance='docData.contentData.contentList[type=video]',
        recording_date=None,production_quality='not_evaluated')


def fetch(url,output):
    from ci_fetch_bilibili import validate_media
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.ifeng.com/'})
    with urllib.request.urlopen(request,timeout=45) as response:
        page=response.read(12*1024*1024).decode('utf-8')
    origin=article_video(page,url)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name(output.stem+'.download.mp4')
    try:
        subprocess.run(['curl','-fLsS','--retry','2','--connect-timeout','20','--max-time','240',
            '--user-agent','Mozilla/5.0','--referer',url,'--output',str(temporary),origin['media_url']],
            check=True,timeout=750)
        valid=validate_media(temporary)
        if abs(valid['duration']-origin['declared_duration'])>3:
            raise ValueError('实际媒体时长与正文附件不符')
        origin.update(sha256=hashlib.sha256(temporary.read_bytes()).hexdigest(),duration_sec=valid['duration'])
        temporary.replace(output)
        output.with_suffix('.origin.json').write_text(json.dumps(origin,ensure_ascii=False,indent=2)+'\n')
        return output
    finally:
        temporary.unlink(missing_ok=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();fetch(args.url,args.out)
