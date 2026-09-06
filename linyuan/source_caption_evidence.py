"""Read existing source subtitles with local CPU OCR, preserving separate ASR evidence.

Only explicitly measured, hash-bound subtitle regions are eligible. This does
not rewrite raw ASR or infer missing words from a text model.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

VERSION = 1
FPS = 4


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def read_source_captions(source, out, profile, source_sha):
    import cv2
    import numpy as np
    from rapidocr_onnxruntime import RapidOCR
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    cache=out/'source_caption_evidence.json'
    identity={'version':VERSION,'source_sha256':source_sha,'roi':profile['subtitle_roi_xywh'],'fps':FPS}
    if cache.exists():
        saved=json.loads(cache.read_text())
        if saved.get('identity')==identity:
            return saved
    x,y,w,h=profile['subtitle_roi_xywh']
    # Recognition of an already located single subtitle line is much faster
    # than repeatedly detecting every text box in the complete source frame.
    ocr=RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    command=['ffmpeg','-v','error','-i',str(source),'-vf',
             f'fps={FPS},crop={w}:{h}:{x}:{y}', '-f','rawvideo','-pix_fmt','bgr24','pipe:1']
    proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    rows=[];group=None;index=0;started=time.monotonic();low_confidence=0
    def finish():
        nonlocal group
        if group is not None:
            rows.append(group)
        group=None
    try:
        while True:
            raw=proc.stdout.read(w*h*3)
            if not raw:
                break
            if len(raw)!=w*h*3:
                raise ValueError('OCR source-frame stream ended mid-frame')
            frame=np.frombuffer(raw,dtype=np.uint8).reshape(h,w,3)
            result,_=ocr(frame,use_det=False,use_cls=False,use_rec=True)
            text='';confidence=0.0
            if result:
                text=''.join(str(r[0]) for r in result)
                confidence=min(float(r[1]) for r in result)
            text=re.sub(r'\s+','',text)
            if confidence<.96 or not re.search(r'[\u4e00-\u9fff]',text):
                low_confidence+=bool(text)
                text=''
            stamp=index/FPS
            if text and group and text==group['text']:
                group['end']=(index+1)/FPS
                group['observations']+=1
                group['confidence']=min(group['confidence'],confidence)
            elif text:
                finish()
                name=f'caption-{index:06d}.jpg'
                cv2.imwrite(str(out/name),frame)
                group={'start':stamp,'end':(index+1)/FPS,'text':text,
                       'confidence':confidence,'observations':1,'evidence_frame':name}
            else:
                finish()
            index+=1
            if index%400==0:
                print(json.dumps({'source_caption_frames':index,'source_seconds':index/FPS,
                    'elapsed_seconds':round(time.monotonic()-started,1)},ensure_ascii=False),flush=True)
        finish()
        if proc.wait(timeout=30):
            raise ValueError('Source subtitle extraction failed')
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait()
    result={'identity':identity,'engine':'RapidOCR ONNX CPU, recognition-only',
            'frames_sampled':index,'low_confidence_frames':low_confidence,
            'elapsed_seconds':round(time.monotonic()-started,2),'cues':rows}
    cache.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    source=Path(args.source);digest=sha256(source)
    profiles=json.loads((Path(__file__).parent/'source_crop_profiles.json').read_text())
    profile=profiles.get(digest) or {}
    if not profile.get('subtitle_roi_xywh'):
        raise SystemExit('No measured subtitle region for this exact source')
    result=read_source_captions(source,Path(args.out),profile,digest)
    print(json.dumps({k:v for k,v in result.items() if k!='cues'},ensure_ascii=False))
    print('Source caption observations:',len(result['cues']))


if __name__=='__main__':
    main()
