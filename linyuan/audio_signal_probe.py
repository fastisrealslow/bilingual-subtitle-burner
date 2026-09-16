"""Measure source audio without filtering, rewriting ASR, or approving media."""
import argparse
import json
from pathlib import Path
import subprocess


def measure(samples, rate=16000):
    import numpy as np
    samples=np.asarray(samples,dtype=np.float64)
    if samples.ndim!=2 or len(samples)<rate//10:
        raise ValueError('Insufficient decoded channel samples')
    rms=np.sqrt(np.mean(samples**2,axis=0))
    mono=samples.mean(axis=1)
    mono_rms=float(np.sqrt(np.mean(mono**2)))
    spectrum=np.abs(np.fft.rfft(samples,axis=0))**2
    frequency=np.fft.rfftfreq(len(samples),1/rate)
    total=spectrum.sum(axis=0)
    speech=spectrum[(frequency>=150)&(frequency<=4000)].sum(axis=0)
    hum=spectrum[(frequency>=40)&(frequency<=70)].sum(axis=0)
    correlation=None
    if samples.shape[1]==2 and min(rms)>1e-8:
        centered=samples-samples.mean(axis=0)
        norm=np.sqrt((centered**2).sum(axis=0).prod())
        if norm>0:correlation=float((centered[:,0]*centered[:,1]).sum()/norm)
    return dict(channel_rms=rms.tolist(),arithmetic_mono_rms=mono_rms,
        mono_to_loudest_channel_ratio=mono_rms/max(float(max(rms)),1e-12),
        stereo_correlation=correlation,
        speech_band_energy_fraction=(speech/np.maximum(total,1e-20)).tolist(),
        hum_40_70hz_energy_fraction=(hum/np.maximum(total,1e-20)).tolist())


def probe(source):
    import numpy as np
    data=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','a:0',
        '-show_entries','stream=channels,sample_rate:format=duration','-of','json',str(source)],timeout=60))
    channels=int(data['streams'][0]['channels'])
    duration=float(data['format']['duration'])
    if not 1<=channels<=8 or duration<=0:raise ValueError('Invalid source audio metadata')
    rows=[]
    for position in (.1,.5,.9):
        start=max(0,min(duration-12,duration*position))
        raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(start),'-i',str(source),
            '-t','12','-map','0:a:0','-ar','16000','-c:a','pcm_f32le','-f','f32le','-'],timeout=90)
        samples=np.frombuffer(raw,dtype='<f4').reshape(-1,channels)
        mono_raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(start),'-i',str(source),
            '-t','12','-map','0:a:0','-ar','16000','-ac','1','-c:a','pcm_f32le','-f','f32le','-'],timeout=90)
        mono=np.frombuffer(mono_raw,dtype='<f4').astype(np.float64)
        row=measure(samples)
        row.update(start=start,duration=len(samples)/16000,
                   ffmpeg_asr_mono_rms=float(np.sqrt(np.mean(mono**2))))
        rows.append(row)
    return dict(version=1,channels=channels,source_duration=duration,samples=rows,
                diagnostic_only=True,final_quality_approved=False,
                note='Spectral energy and correlation are not speech intelligibility or ASR accuracy.')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();result=probe(args.source)
    out=Path(args.out);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
