"""Actual incident MP4s plus deliberately clipped head/chin controls."""
import argparse
import json
from pathlib import Path
import cv2
import produce_cn as p
from live_tracking import complete_face


def main():
    args=argparse.ArgumentParser()
    args.add_argument('--accepted',required=True,type=Path)
    args.add_argument('--rejected',required=True,type=Path)
    options=args.parse_args()
    cv2.setNumThreads(2)
    detector_path,_=p._local_face_models()
    detector=cv2.FaceDetectorYN.create(str(detector_path),'',(632,470),score_threshold=.8)
    def passes(frame):
        h,w=frame.shape[:2];detector.setInputSize((w,h));_,found=detector.detect(frame)
        return found is not None and any(complete_face(face,w,h) for face in found)
    def sample(path):
        cap=cv2.VideoCapture(str(path));total=cap.get(cv2.CAP_PROP_FRAME_COUNT);result=[]
        try:
            for i in range(6):
                cap.set(cv2.CAP_PROP_POS_FRAMES,int(total*(i+.5)/6));ok,image=cap.read()
                if not ok:raise ValueError('Actual incident frame missing')
                result.append(image[360:830,44:676])
        finally:cap.release()
        return result
    accepted=sample(options.accepted)
    assert sum(passes(im) for im in accepted)==6
    rows=[dict(case='actual_647_accepted',full_faces=6,total=6)]
    for part in (1,2):
        path=options.rejected/f'rejected/{part}/final_{part}.mp4'
        count=sum(passes(im) for im in sample(path))
        assert count<5
        rows.append(dict(case=f'actual_644_empty_{part}',full_faces=count,total=6))
    # Use the exact accepted source-time frame for a meaningful geometry control.
    cap=cv2.VideoCapture(str(options.accepted));cap.set(cv2.CAP_PROP_POS_MSEC,50000)
    ok,image=cap.read();cap.release();assert ok
    window=image[360:830,44:676]
    assert passes(window)
    assert not passes(window[150:])
    assert not passes(window[:240])
    rows.append(dict(case='actual_face_with_head_or_chin_clipped',rejected=True))
    print(json.dumps(rows,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
