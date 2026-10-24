"""Read-only Linux check of real first-party metadata and normal extractor."""
import json
from pathlib import Path
import subprocess
import sys

from ci_fetch_generic import FORMAT
import monitor_v2 as M
from p5w_source import extract, resolve


def main():
    url='https://www.p5w.net/v/ip/typyq/202511/t20251121_6424547.htm'
    result=dict(passed=False,diagnostic_only=True,registered_stock=False,
        published=False,media_quality_approved=False,recording_date_verified=False)
    out=Path('p5w-primary-source-check.json')
    def save():out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    save()
    metadata=extract(url,lambda u,referer:M.http_get(u,referer=referer,timeout=15))
    result['metadata']=metadata;save()
    assert metadata['duration']==3316.48 and metadata['declared_resolution']==dict(width=1920,height=1080)
    assert metadata['direct_dispatch'] and metadata['origin_role']=='official_publisher'
    assert not metadata['recording_date_verified'] and not metadata['media_quality_approved']
    origin=resolve(url)
    assert origin['declared_duration']==metadata['duration']
    # Same official attachment/Referer and format preference as normal fetch.
    # Never use the page's <audio> fallback as video evidence.
    response=subprocess.run([sys.executable,'-m','yt_dlp','--simulate','--dump-single-json',
        '--no-playlist','--socket-timeout','20','--retries','0','--fragment-retries','0',
        '-f',FORMAT,'--format-sort','res:1080','--referer',url,origin['media_url']],
        capture_output=True,text=True,timeout=120,check=True)
    data=json.loads(response.stdout)
    result['extractor']=dict(name=data.get('extractor'),format_id=data.get('format_id'),
        width=data.get('width'),height=data.get('height'),protocol=data.get('protocol'))
    save()
    assert data.get('width')==1920 and data.get('height')==1080
    assert data.get('protocol') in ('m3u8','m3u8_native')
    result['passed']=True;save();print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
