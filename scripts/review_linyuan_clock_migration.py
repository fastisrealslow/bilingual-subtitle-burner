"""Compare real caption/cover rendering on a source-clock diagnostic sample.

Raw historic word times are mapped through decoded audio frame PTS only for
this review. Production re-recognizes clock-corrected PCM, never this migration.
This sample does NOT certify source identity or editorial acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))


def main():
    import cv2
    import numpy as np
    from PIL import Image
    import produce_cn as P
    import presentation as V
    import caption_readability as R
    import audio_preprocessing as A
    import editorial_cover as C
    from qwen_asr_evidence import validated_words
    ap=argparse.ArgumentParser()
    for name in ('source','evidence','meta','out'):ap.add_argument('--'+name,type=Path,required=True)
    ap.add_argument('--final',required=True,help='Published file from immutable metadata')
    ap.add_argument('--reference',type=Path,required=True)
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    rows=json.loads(args.meta.read_text())
    # Choose the latest publication via explicit metadata, not guessed times.
    meta=next(m for m in rows if m['final']==args.final)
    report=json.loads(args.evidence.read_text())
    words=validated_words([report],report['source_pcm_sha256'],report['source_video_sha256'],report['duration'])
    raw=subprocess.check_output(['ffprobe','-v','error','-select_streams','a:0',
        '-show_frames','-show_entries','frame=best_effort_timestamp_time,nb_samples:stream=sample_rate',
        '-of','json',str(args.source)],text=True)
    frames=json.loads(raw);rate=int(frames['streams'][0]['sample_rate']);samples=0
    decoded=[];pts=[]
    for frame in frames['frames']:
        decoded.append(samples/rate);pts.append(float(frame['best_effort_timestamp_time']))
        samples+=int(frame['nb_samples'])
    decoded.append(samples/rate);pts.append(pts[-1]+int(frames['frames'][-1]['nb_samples'])/rate)
    if any(a>b for a,b in zip(pts,pts[1:])):raise ValueError('Non-monotonic source PTS')
    if abs(decoded[-1]-report['duration'])>.1:raise ValueError('Historic sample clock does not match this decode length')
    map_time=lambda t:float(np.interp(t,decoded,pts))
    interval=meta['segments'][0];lo,hi=interval['start'],interval['end']
    selected=[w for w in words if lo<=w['start'] and w['end']<=hi]
    # Keep the immutable words; this diagnostic isn't an editorial correction.
    origin=map_time(lo)
    cues=P._funasr_tokens_to_cues([w['text'] for w in selected],
        [map_time(w['start'])-origin for w in selected],0,map_time(hi)-origin,
        end_timestamps=[map_time(w['end'])-origin for w in selected])
    entries=[P.cue_caption_entry(c) for c in cues]
    clean,edit=R.clean_entries(entries)
    layout=V.layout_for(720,1280)
    ass=args.out/'captions.ass';prepared=V.write_ass(clean,ass,layout,'PingFang SC')
    video=args.out/'phase1-review.mp4'
    subprocess.run(['ffmpeg','-y','-v','error','-ss',str(origin),'-i',str(args.source),
        '-vf',f'setpts=PTS-STARTPTS,scale=720:1280,setsar=1,ass={ass}',
        '-af',A.render_prefix(A.DEFAULT)+'asetpts=PTS-STARTPTS','-t','30',
        '-c:v','libx264','-preset','veryfast','-crf','20','-c:a','aac',str(video)],check=True)
    cap=cv2.VideoCapture(str(video));cap.set(cv2.CAP_PROP_POS_MSEC,6500);ok,frame=cap.read();cap.release()
    if not ok:raise ValueError('Review frame unavailable')
    cv2.imwrite(str(args.out/'subtitle-example.jpg'),frame)
    cap=cv2.VideoCapture(str(args.source));cap.set(cv2.CAP_PROP_POS_MSEC,origin*1000);ok,frame=cap.read();cap.release()
    from live_tracking import reference_faces
    faces=reference_faces([frame],args.reference,P._local_face_models(),P.LOCAL_FACE_COSINE_THRESHOLD)
    if not len(faces):raise ValueError('No source portrait for rendering diagnostic')
    face=faces[0]
    image=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
    cover=C.render(image,args.out/'cover.jpg',face,meta['cover_title'],'林园',C.font_path())
    proof=dict(version=1,diagnostic_only=True,publication_approved=False,
        source_sha256=hashlib.sha256(args.source.read_bytes()).hexdigest(),
        original_media_identity_verified=False,historical_pcm_duration=report['duration'],
        source_clock_duration=pts[-1],legacy_start=lo,source_pts_start=origin,
        prepared_captions=len(prepared),single_line=all(len(c['lines'])==1 for c in prepared),
        subtitle_style=layout['subtitle_style'],
        cover_feed_safe=all(V.feed_safe_box(b) for b in cover['text_boxes']+[cover['face_box']]),
        production_migration_used=False)
    (args.out/'review-proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    print(json.dumps(proof,ensure_ascii=False))


if __name__=='__main__':main()
