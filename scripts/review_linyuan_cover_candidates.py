"""Audit frame-selection behavior on verified finals, without creating new clips.

Rendered final pixels can differ from the pre-render source. This isolates the
selection algorithm; results are not production cover approvals or yield gains.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import produce_cn as producer


def main():
    import cv2
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--corpus',type=Path,required=True)
    ap.add_argument('--reference',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=True)
    rows=[]
    for index,case in enumerate(json.loads(args.corpus.read_text())):
        final=ROOT/case['final_file'];meta=json.loads((final.parent/'meta.json').read_text())
        row=dict(case_id=case['id'],source_id=case['source_id'],final_sha256=case['final_sha256'],
                 editorial_approved=False,production_cover_approved=False)
        if meta.get('render_mode')!='live_video_card':
            # Direct native renders have burned captions over the face. Their
            # glyphs inflate Laplacian variance (observed on source308), so
            # they cannot measure the sharpness of the pre-caption source.
            row['status']='skipped_requires_clean_pre_caption_source'
        else:
            if hashlib.sha256(final.read_bytes()).hexdigest()!=case['final_sha256']:
                raise ValueError('Final bytes changed')
            folder=args.out/case['id'];folder.mkdir(exist_ok=True)
            layout=meta['layout_proof']
            if meta.get('render_mode')=='live_video_card':
                rect=layout.get('live_region') or dict(x=44,y=360,width=632,height=470)
            else:
                rect=dict(x=0,y=0,width=layout['canvas']['width'],height=layout['canvas']['height'])
            cap=cv2.VideoCapture(str(final));frames=[]
            try:
                for n,fraction in enumerate((.2,.3,.4,.5,.6,.7,.8)):
                    cap.set(cv2.CAP_PROP_POS_MSEC,case['duration']*fraction*1000)
                    ok,frame=cap.read()
                    if not ok:raise ValueError('Cannot decode frame')
                    x,y,w,h=(int(rect[k]) for k in ('x','y','width','height'))
                    cropped=frame[y:y+h,x:x+w]
                    if cropped.shape[:2]!=(h,w):raise ValueError('Invalid source window')
                    # The tracked production window is 632x470 before layout.
                    if meta.get('render_mode')=='live_video_card':
                        cropped=cv2.resize(cropped,(632,470),interpolation=cv2.INTER_AREA)
                    path=folder/f'frame-{n}.png'
                    if not cv2.imwrite(str(path),cropped):raise ValueError('Cannot write frame')
                    frames.append(path)
                try:
                    frame,box,proof=producer.select_verified_cover_face(frames,args.reference)
                    row.update(status='analyzed',proof=proof,selected_frame=str(frame.relative_to(ROOT)),
                               face_box=list(box),window=rect,
                               usable_sharp_match=proof['sharpness']>=60,
                               rescued_selection=proof['identity_first_sharpness']<60<=proof['sharpness'])
                except producer.VisualQualityError as exc:
                    row.update(status='no_identity_match',error=str(exc))
            finally:cap.release()
        rows.append(row)
        (args.out/'results.json').write_text(json.dumps(dict(rows=rows,
            scope='Final-pixel frame selection diagnostic, not source-render replay or new video yield'),ensure_ascii=False,indent=2)+'\n')
        print(index,row['case_id'],row['status'],row.get('proof',{}).get('sharpness'),
              'rescued='+str(row.get('rescued_selection',False)),flush=True)


if __name__=='__main__':main()
