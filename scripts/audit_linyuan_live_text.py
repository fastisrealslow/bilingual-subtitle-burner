"""Recheck changing source text on hash-verified finals; never alter old scores."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import produce_cn as producer
from temporal_source_text import changing_text_tracks, VERSION


def main():
    import cv2
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--reports',type=Path,required=True)
    args=ap.parse_args();folder=args.reports.resolve()
    catalog=json.loads((folder/'media-verification.json').read_text())
    out=folder/'source-text-audit';out.mkdir(exist_ok=True)
    rows=[]
    for item in catalog:
        video=ROOT/item['file'];meta=json.loads((video.parent/'meta.json').read_text())
        if hashlib.sha256(video.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Final bytes changed after verification')
        layout=meta.get('layout_proof') or {};region=layout.get('live_region')
        if (not region and meta.get('audio_card_template')=='live_editorial_v4_readable'
                and item['dimensions']==[720,1280]):region=producer.LIVE_REGION
        row=dict(id=item['id'],final_sha256=item['sha256'],source_sha256=item['source_sha256'],
                 region=region,editorial_approved=False)
        if not region:
            row.update(status='not_applicable',reason='No declared source-only window; do not scan our captions.')
        else:
            work=out/str(item['id']);work.mkdir(exist_ok=True)
            cap=cv2.VideoCapture(str(video));count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));paths=[]
            try:
                for n in range(6):
                    cap.set(cv2.CAP_PROP_POS_FRAMES,int(count*(n+.5)/6));ok,frame=cap.read()
                    if not ok:raise ValueError('Missing audit frame')
                    x,y,w,h=(region[k] for k in ('x','y','width','height'))
                    image=frame[y:y+h,x:x+w]
                    if image.shape[:2]!=(h,w):raise ValueError('Invalid source-only region')
                    path=work/f'frame-{n}.jpg'
                    if not cv2.imwrite(str(path),image):raise ValueError('Cannot save audit frame')
                    paths.append(path)
            finally:cap.release()
            producer.detect_corner_logos_in_images(paths,stable_ratio=.5,max_area=.04)
            evidence=json.loads((work/'corner_ocr.json').read_text())['evidence']
            tracks=changing_text_tracks(evidence)
            row.update(status='changing_source_text' if tracks else 'no_changing_track_detected',tracks=tracks)
        rows.append(row);print(json.dumps(dict(id=row['id'],status=row['status'])),flush=True)
        (out/'audit.json').write_text(json.dumps(dict(version=VERSION,rows=rows,
            scope='New diagnostic on old finals; no rewrite of production pass counts or editorial approval.'),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
