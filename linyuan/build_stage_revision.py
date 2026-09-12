"""Rebuild the reported stage clip from its exact original source interval."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import produce_cn as P
import stage_context
import title_rewrite
import headline_policy
import landscape


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--out',default='stage-revision');args=ap.parse_args()
    src=Path(args.source);out=Path(args.out);out.mkdir(exist_ok=True,parents=True);work=out/'_tmp';work.mkdir(exist_ok=True)
    expected='81833e8aa7224716bd682b91e486ffcab5f4f2f4dc6fc00f8d523b08c59067dd'
    if hashlib.sha256(src.read_bytes()).hexdigest()!=expected:raise ValueError('Original source bytes changed')
    slug='ly-0911-171705';base=f'https://github.com/fastisrealslow/bilingual-subtitle-burner/releases/download/deliver-{slug}/'
    for suffix in ['meta.json','subtitles_1-1.ass']:
        urllib.request.urlretrieve(base+slug+'.'+suffix,work/suffix)
    old=json.loads((work/'meta.json').read_text());rows=landscape.read_captions(work,['subtitles_1-1.ass'])
    text=''.join(row['zh'] for row in rows)
    cw=headline_policy.attach_copy(title_rewrite.generate(text),text)
    cw['desc']='林园在2016年中国合伙人大会的演讲选段，讨论人口变化、消费需求与实业经营。'
    a,b=old['segments'][0]['start'],old['segments'][0]['end']
    plan=stage_context.plan(src,a,b-a,P._download_speaker_reference('林园',work),P._local_face_models())
    if not plan:raise ValueError('Source does not pass automatic stage-context recognition')
    meta=stage_context.render(src,a,b-a,out,work,rows,cw,old,plan,producer=P)
    for key in ['editorial_review','editorial_policy_version','asr_model','source_platform','occasion']:meta[key]=old[key]
    meta.update(slug=slug,source='https://www.bilibili.com/video/BV1hh411K7dJ')
    (out/'meta.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
    sys.path.insert(0,str(Path(__file__).parent/'fc'));import index as fc
    error=fc.artifact_quality_error(meta) or fc.artifact_subtitle_error(meta,out) or fc.artifact_cover_error(meta,out)
    if error:raise ValueError(error)
    subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(out/meta['final']),'-map','0:v:0','-map','0:a:0','-f','null','-'],check=True,timeout=180)
    item=dict(batch='wide-stage-20260912',bvid='BV1CLYd6LEWE',slug=slug,
        source_sha256=expected,old_video_sha256=old['fingerprints']['sha256'],old_title=old['title'],
        title=meta['title'],video_sha256=meta['fingerprints']['sha256'],
        cover_sha256=hashlib.sha256((out/meta['cover']).read_bytes()).hexdigest())
    (out/'revision-manifest.json').write_text(json.dumps(item,ensure_ascii=False,indent=2))
    print(json.dumps(dict(title=meta['title'],duration=meta['duration_sec'],presenter_motion=meta['stage_context']['final_presenter_motion']['moving_by_third']),ensure_ascii=False))

if __name__=='__main__':main()
