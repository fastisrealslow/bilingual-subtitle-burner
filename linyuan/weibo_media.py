"""Select actual playable MP4, not the misleading 480p 'HD' shortcut."""
from urllib.parse import urlsplit


def best_mp4(media):
    choices=[]
    for row in media.get('playback_list') or []:
        if not isinstance(row,dict):continue
        info=row.get('play_info') or {}
        if not isinstance(info,dict):continue
        url=info.get('url')
        if not isinstance(url,str):continue
        parsed=urlsplit(url)
        if parsed.scheme not in {'http','https'} or not parsed.path.lower().endswith('.mp4'):
            continue
        if info.get('mime') not in (None,'video/mp4'):continue
        try:
            width,height=int(info.get('width') or 0),int(info.get('height') or 0)
            bitrate=int(info.get('bitrate') or 0)
        except (ValueError,TypeError):continue
        if min(width,height)<=0 or min(width,height)>1080 or max(width,height)>1920:
            continue
        choices.append((min(width,height),width*height,bitrate,url))
    if choices:return max(choices)[-1]
    for key in ('mp4_720p_mp4','stream_url_hd','stream_url','mp4_hd_url','mp4_sd_url'):
        url=media.get(key)
        if isinstance(url,str) and urlsplit(url).scheme in {'http','https'}:
            return url
    return ''
