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
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import audio_preprocessing as audio_clock


def extract_published_audio(video, output):
    """Keep recognition windows on the same presentation clock as frames."""
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(video), '-map', '0:a:0',
        *audio_clock.asr_args(audio_clock.DEFAULT), '-c:a', 'pcm_s16le', str(output)],
        check=True)
    with wave.open(str(output)) as wav:
        duration = wav.getnframes() / wav.getframerate()
    return audio_clock.clock_proof(video, duration, audio_clock.DEFAULT)


def compact(text):
    return ''.join(c.lower() for c in text if c.isalnum())


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
    clock = extract_published_audio(args.video, audio)
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
        text=compact(words)
        row=dict(frame_sec=round(float(seconds),3),caption=words)
        matches=[]
        for recognized in recognition:
            acoustic=compact(recognized['text'])
            match=SequenceMatcher(None,text,acoustic,autojunk=False).find_longest_match()
            if match.size>=6:
                matches.append((match.size,recognized,text[match.a:match.a+match.size]))
        if matches:
            _,nearest,fragment=max(matches,key=lambda m:(m[0],-abs(m[1]['start_sec']-seconds)))
            row.update(independent_start_sec=nearest['start_sec'],
                independent_end_sec=nearest['end_sec'],matched_fragment=fragment,
                outside_spoken_interval_sec=round(max(nearest['start_sec']-seconds,
                    seconds-nearest['end_sec'],0),3),status='exact_fragment_window_match')
        else:row['status']='wording_unconfirmed'
        rows.append(row)
    cap.release()
    report=dict(version=2,video_sha256=hashlib.sha256(args.video.read_bytes()).hexdigest(),
        audio_clock=clock,
        model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),device='cpu',
        engine='sherpa-onnx paraformer zh 2024-03-09',recognition=recognition,samples=rows,
        duration_sec=duration,elapsed_sec=round(time.monotonic()-started,2),
        continuous_human_listening=False,production_cues_changed=False,
        interpretation='Exact fragments locate only a 3-second acoustic window, not word timestamps; OCR/ASR can err.')
    (args.out/'sync-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(sampled=len(rows),exact_matches=sum(r['status']=='exact_fragment_window_match' for r in rows),
        large_disagreements=[r for r in rows if r.get('outside_spoken_interval_sec',0)>2]),ensure_ascii=False))


if __name__=='__main__':main()
