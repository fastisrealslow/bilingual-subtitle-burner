"""Read-only audio correspondence audit; never patches publication history."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import urllib.request

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
RATE=8000


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def fetch(url,path,expected):
    if not expected or len(expected)!=64:raise ValueError('Missing frozen asset hash')
    if not path.is_file() or digest(path)!=expected:
        with urllib.request.urlopen(url,timeout=60) as response,path.open('wb') as stream:
            while block:=response.read(1024*1024):stream.write(block)
    if digest(path)!=expected:raise ValueError('Archived asset bytes changed')


def pcm(path):
    data=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0',
        '-ac','1','-ar',str(RATE),'-f','f32le','-'],timeout=180)
    return np.frombuffer(data,dtype='<f4').astype(np.float64)


def window_match(clip,reference):
    """Signed, zero-mean normalized waveform correlation over a bounded shift."""
    if len(clip)<RATE or len(reference)<len(clip):return None
    centered=clip-clip.mean();energy=float(centered@centered)
    if energy/len(clip)<1e-7:return None
    n=len(clip)
    sums=np.r_[0,np.cumsum(reference)];squares=np.r_[0,np.cumsum(reference*reference)]
    variance=np.maximum(0,squares[n:]-squares[:-n]-(sums[n:]-sums[:-n])**2/n)
    denominator=np.sqrt(energy*variance)
    fft_size=1<<(len(reference)+n-2).bit_length()
    convolution=np.fft.irfft(np.fft.rfft(reference,fft_size)*np.fft.rfft(centered[::-1],fft_size),fft_size)
    scores=np.divide(convolution[n-1:len(reference)],denominator,
        out=np.full(len(denominator),-1.),where=denominator>1e-9)
    best=int(np.argmax(scores))
    return dict(correlation=float(min(1,scores[best])),offset_samples=best)


def audit_segments(final,source,segments):
    rows=[];cursor=0.;margin=.4
    for index,segment in enumerate(segments):
        a,b=float(segment['start']),float(segment['end'])
        if not 0<=a<b or b>len(source)/RATE+.1:raise ValueError('Invalid source range')
        duration=b-a;position=.5
        while position<duration-.5:
            length=min(8.,duration-.5-position)
            if length<1:break
            old_start=cursor+position;expected=a+position
            clip=final[round(old_start*RATE):round((old_start+length)*RATE)]
            left=max(0,round((expected-margin)*RATE));right=round((expected+length+margin)*RATE)
            match=window_match(clip,source[left:right])
            row=dict(segment=index,final_start=old_start,source_expected_start=expected,seconds=length,
                passed=False,reason='Insufficient non-silent waveform evidence')
            if match is not None:
                offset=(left+match['offset_samples'])/RATE-expected
                row.update(correlation=round(match['correlation'],6),offset_seconds=round(offset,6),
                    passed=match['correlation']>=.90,reason='Bounded waveform comparison')
            rows.append(row);position+=length
        cursor+=duration
    # A local match cannot conceal an extra tail or a badly described concat.
    duration_ok=abs(len(final)/RATE-cursor)<=.6
    return dict(windows=rows,duration_matches=duration_ok,
        final_seconds=len(final)/RATE,metadata_seconds=cursor,
        waveform_matches=bool(rows) and duration_ok and all(r['passed'] for r in rows),
        scope='All sampled 8-second interior windows must match at >=0.90 within 0.4s; 0.5s cut edges excluded. Diagnostic only, not publication approval.')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-id',type=int,required=True);ap.add_argument('--source',type=Path,required=True)
    ap.add_argument('--out',type=Path,required=True);args=ap.parse_args()
    cases=json.loads((ROOT/'linyuan/simulations/benchmark-20260921/legacy-range-cases.json').read_text())
    case=next(c for c in cases if c['source_id']==args.source_id)
    if digest(args.source)!=case['source_sha256']:raise ValueError('Different current mother bytes')
    args.out.mkdir(parents=True,exist_ok=True);source=pcm(args.source)
    meta=args.out/'archived-meta.json';fetch(case['metadata_url'],meta,case['metadata_sha256'])
    metadata=json.loads(meta.read_text());rows=[]
    for part in case['parts']:
        original=next(m for m in metadata if m['part']==part['part'])
        if (original['slug']!=case['slug'] or
                original['title'].removesuffix('｜林园')!=part['receipt_title'].removesuffix('｜林园')):
            raise ValueError('Archived part does not match the historical receipt')
        video=args.out/f"archived-final-{part['part']}.mp4"
        fetch(part['media_url'],video,part['media_sha256'])
        result=audit_segments(pcm(video),source,original['segments'])
        rows.append(dict(**part,segments=original['segments'],**result))
    proof=dict(case=case,parts=rows,run_id=os.environ.get('GITHUB_RUN_ID'),commit=os.environ.get('GITHUB_SHA'),
        history_modified=False,production_approval=False,source_yield_credit=False,
        limitation='Compares archived render assets to the current mother; does not prove the current public Bilibili rendition matches the archived asset.')
    (args.out/'audit.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(source_id=args.source_id,parts=[dict(part=r['part'],matched=r['waveform_matches']) for r in rows]),ensure_ascii=False))


if __name__=='__main__':main()
