"""Measure published caption wording against independent, timed CPU recognition.

This is diagnostic evidence, never a replacement transcript or publication approval.
"""
import argparse
import hashlib
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import subprocess
import time
import wave


def compact(text):
    return ''.join(c.lower() for c in text if c.isalnum())


def match_caption_window(words, recognition, seconds):
    """A slightly longer distant repeat does not prove a caption delay."""
    text = compact(words)
    candidates = []
    for recognized in recognition:
        acoustic = compact(recognized['text'])
        match = SequenceMatcher(None, text, acoustic, autojunk=False).find_longest_match()
        if match.size < 4:
            continue
        distance = max(recognized['start_sec'] - seconds,
                       seconds - recognized['end_sec'], 0)
        candidates.append(dict(length=match.size,
                               matched_fragment=text[match.a:match.a+match.size],
                               independent_start_sec=recognized['start_sec'],
                               independent_end_sec=recognized['end_sec'],
                               outside_spoken_interval_sec=round(distance, 3)))
    strong = [m for m in candidates if m['length'] >= 6]
    if not strong:
        return dict(status='wording_unconfirmed')
    best = max(strong, key=lambda m: (m['length'], -m['outside_spoken_interval_sec']))
    # ASR can confuse homophones or split a repeated phrase across a window.
    # Keep both witnesses when the nearby phrase is almost as long and is
    # contained in the remote exact match. Do not guess which occurrence was
    # spoken, normalize words, or silently declare synchronization correct.
    nearby = [m for m in candidates if m['outside_spoken_interval_sec'] <= 2
              and m['length'] >= max(4, best['length'] - 1)
              and m['matched_fragment'] in best['matched_fragment']]
    if best['outside_spoken_interval_sec'] > 2 and nearby:
        return dict(status='ambiguous_repeated_fragment', remote_candidate=best,
                    nearby_candidate=max(nearby, key=lambda m: (m['length'],
                                          -m['outside_spoken_interval_sec'])))
    return dict(status='exact_fragment_window_match',
                **{k: v for k, v in best.items() if k != 'length'})


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--video',type=Path,required=True)
    ap.add_argument('--weights',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--sample-step',type=float,default=3)
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    import cv2
    import numpy as np
    from sherpa_onnx import OfflineRecognizer
    from rapidocr_onnxruntime import RapidOCR
    audio=args.out/'published-audio.wav'
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(args.video),'-map','0:a:0',
        '-ac','1','-ar','16000','-c:a','pcm_s16le',str(audio)],check=True)
    with wave.open(str(audio)) as wav:
        samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype=np.int16).astype(np.float32)/32768
    model=args.weights/'model.int8.onnx'
    recognizer=OfflineRecognizer.from_paraformer(paraformer=str(model),
        tokens=str(args.weights/'tokens.txt'),num_threads=2,sample_rate=16000,
        feature_dim=80,decoding_method='greedy_search',provider='cpu')
    started=time.monotonic();recognition=[]
    # Paraformer does not expose word timestamps. Retain the real, bounded
    # acoustic window instead of treating empty timestamp arrays as evidence.
    window=3*16000
    for offset in range(0,len(samples),window):
        stream=recognizer.create_stream();stream.accept_waveform(16000,samples[offset:offset+window])
        recognizer.decode_stream(stream)
        result=stream.result
        recognition.append(dict(start_sec=offset/16000,
            end_sec=min(offset+window,len(samples))/16000,text=result.text))
        print('recognized',offset/16000,'seconds',flush=True)
    cap=cv2.VideoCapture(str(args.video));fps=cap.get(cv2.CAP_PROP_FPS)
    duration=len(samples)/16000;ocr=RapidOCR();rows=[]
    for seconds in np.arange(.5,duration,args.sample_step):
        cap.set(cv2.CAP_PROP_POS_FRAMES,round(seconds*fps));ok,frame=cap.read()
        if not ok:continue
        height,width=frame.shape[:2]
        region=frame[round(height*.65):round(height*.97),round(width*.03):round(width*.97)]
        found,_=ocr(region)
        words=''.join(r[1] for r in sorted(found or [],key=lambda r:min(p[1] for p in r[0])))
        row=dict(frame_sec=round(float(seconds),3),caption=words)
        row.update(match_caption_window(words, recognition, float(seconds)))
        rows.append(row)
    cap.release()
    report=dict(version=1,video_sha256=hashlib.sha256(args.video.read_bytes()).hexdigest(),
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),device='cpu',
        engine='sherpa-onnx paraformer zh 2024-03-09',recognition=recognition,samples=rows,
        duration_sec=duration,elapsed_sec=round(time.monotonic()-started,2),
        continuous_human_listening=False,production_cues_changed=False,
        caption_match_version=2,
        interpretation='Exact fragments locate only a 3-second acoustic window, not word timestamps; OCR/ASR can err. Nearby repeated wording makes a distant match ambiguous, not proof of a delay.')
    (args.out/'sync-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(sampled=len(rows),exact_matches=sum(r['status']=='exact_fragment_window_match' for r in rows),
        large_disagreements=[r for r in rows if r.get('outside_spoken_interval_sec',0)>2]),ensure_ascii=False))


if __name__=='__main__':main()
