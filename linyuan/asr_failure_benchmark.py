"""Paired CPU hypotheses on actual failed audio. Never production ASR evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import time
import wave
import zipfile

CASES = {
    'fund': dict(url='https://m.weibo.cn/detail/5344830698031714',
        sha='e70b4632acd562154e0601cc29e48e6864bca4b7d0e56fa542b54ca37a2b4884',
        artifact=12402754120),
    'percentage': dict(url='https://m.weibo.cn/detail/5347466289610839',
        sha='c7a533ce67ed9d7dabb2805a6c8396370ad98de4574de748a218ac52154ce362',
        artifact=12404611301),
}
MODELS = {
    '0.6b': ('Qwen/Qwen3-ASR-0.6B', '5eb144179a02acc5e5ba31e748d22b0cf3e303b0'),
    '1.7b': ('Qwen/Qwen3-ASR-1.7B', '7278e1e70fe206f11671096ffdd38061171dd6e5'),
}


def failure_windows(cues, issues, recognition):
    text=''.join(c['text'] for c in cues)
    spans=[];cursor=0
    for cue in cues:
        spans.append((cursor,cursor+len(cue['text']),cue))
        cursor+=len(cue['text'])
    selected={}
    for issue in issues:
        quote=issue.get('suspect_quote') or ''
        if not quote or text.count(quote)!=1:
            raise ValueError('疑点原句在完整原始字幕中不唯一，不能猜测诊断时间段')
        first=text.index(quote);last=first+len(quote)
        touched=[c for a,b,c in spans if a<last and b>first]
        start,end=touched[0]['start'],touched[-1]['end']
        for chunk in recognition['chunks']:
            if chunk['core_start']<end and chunk['core_end']>start:
                selected[chunk['core_start']]={**chunk,'sample_kind':'actual_rejected_number'}
    if not selected or len(selected)>4:
        raise ValueError('失败音频没有有界且明确的原始识别窗口')
    # One untouched control, not just cherry-picked suspicious words.
    control=next((c for c in recognition['chunks'] if c['core_start'] not in selected),None)
    if control:selected[control['core_start']]={**control,'sample_kind':'same_source_control'}
    return [selected[key] for key in sorted(selected)]


def prepare(case, root):
    import os
    from ci_fetch_generic import fetch
    from ci_fetch_bilibili import sha256_file, validate_media
    from audio_preprocessing import policy, asr_args, clock_proof
    from asr_context import specification
    spec=CASES[case];root.mkdir(parents=True,exist_ok=True)
    from source_admission_guard import restriction
    if restriction(spec['url'],[]):raise ValueError('诊断不绕过已知素材限制')
    archive=root/'original-editorial.zip'
    subprocess.run(['gh','api',f'repos/{os.environ["GITHUB_REPOSITORY"]}/actions/artifacts/{spec["artifact"]}/zip'],
                   stdout=archive.open('wb'),check=True,timeout=180)
    with zipfile.ZipFile(archive) as z:
        recognition=json.loads(z.read('_tmp/qwen_cpu/recognition.json'))
        cues=json.loads(z.read('_tmp/cues_raw.json'))
        issues=json.loads(z.read('_tmp/transcript_audit.json'))['issues']
    if recognition.get('source_video_sha256')!=spec['sha']:
        raise ValueError('失败记录与待诊断母片身份不符')
    media=Path('linyuan/_src/video.mp4').resolve()
    actual=None
    if media.is_file():
        with media.open('rb') as handle:actual=sha256_file(handle)
    if actual!=spec['sha']:
        media=fetch(spec['url'],media.parent,root/'source_quality.json')
        with media.open('rb') as handle:actual=sha256_file(handle)
    if actual!=spec['sha']:
        raise ValueError('实际视频与失败母片哈希不同，不能复用旧音频时间点')
    validate_media(media)
    wav=root/'complete-source.wav'
    selected_audio=policy(actual)
    subprocess.run(['ffmpeg','-y','-v','error','-i',str(media),'-vn','-ar','16000',
                    *asr_args(selected_audio),'-c:a','pcm_s16le',str(wav)],check=True,timeout=600)
    with wave.open(str(wav)) as stream:
        if stream.getframerate()!=16000 or stream.getnchannels()!=1 or stream.getsampwidth()!=2:
            raise ValueError('诊断音频格式不是原有16k单声道PCM')
        pcm=stream.readframes(stream.getnframes())
    pcm_sha=hashlib.sha256(pcm).hexdigest()
    if pcm_sha!=recognition['source_pcm_sha256']:
        raise ValueError('完整PCM与原始识别输入不一致，禁止比较不同音频')
    clock=clock_proof(media,len(pcm)/32000,selected_audio)
    context=specification(source_sha=actual)
    samples=[]
    for chunk in failure_windows(cues,issues,recognition):
        start=int(chunk['offset']*16000);end=int((chunk['offset']+chunk['duration'])*16000)
        clip=pcm[2*start:2*end]
        name=f'{case}-{chunk["core_start"]}.wav'
        with wave.open(str(root/name),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000);output.writeframes(clip)
        samples.append(dict(id=name[:-4],file=name,source_url=spec['url'],
            source_sha256=actual,source_pcm_sha256=pcm_sha,pcm_sha256=hashlib.sha256(clip).hexdigest(),
            offset=chunk['offset'],duration=len(clip)/32000,sample_kind=chunk['sample_kind'],
            original_production_hypothesis=chunk['text'],context=context,clock=clock))
    (root/'manifest.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2))
    # Keep only small diagnostic audio and provenance; raw sources remain in
    # their existing source cache and are never registered as new inventory.
    wav.unlink();archive.unlink()


def infer(model_key, audio, output):
    from importlib.metadata import version
    import numpy as np
    import torch
    from huggingface_hub import snapshot_download
    from qwen_asr import Qwen3ASRModel
    from asr_cpu_benchmark import offline_only
    from qwen_cpu_transcript import retry_repetitive_transcript
    model_id,revision=MODELS[model_key]
    weights=snapshot_download(model_id,revision=revision)
    torch.set_num_threads(2);torch.set_num_interop_threads(1)
    offline_only()
    started=time.monotonic()
    model=Qwen3ASRModel.from_pretrained(weights,dtype=torch.float32,device_map='cpu',
                                      max_inference_batch_size=1,max_new_tokens=512)
    report=dict(diagnostic_only=True,production_approved=False,registered_stock=False,published=False,
        model_id=model_id,model_revision=revision,device='cpu',threads=2,
        qwen_asr_version=version('qwen-asr'),torch_version=torch.__version__,
        networking_during_inference=False,model_load_seconds=time.monotonic()-started,
        reference_kind='paired hypotheses; no human gold transcript or CER',results=[])
    output.mkdir(parents=True,exist_ok=True)
    manifests=sorted(audio.glob('*/manifest.json'))
    if len(manifests)!=len(CASES):raise ValueError('需要两个真实失败母片的相同音频样本')
    for manifest in manifests:
        for item in json.loads(manifest.read_text()):
            with wave.open(str(manifest.parent/item['file'])) as stream:
                if stream.getframerate()!=16000 or stream.getnchannels()!=1 or stream.getsampwidth()!=2:
                    raise ValueError('样本PCM格式发生变化')
                pcm=stream.readframes(stream.getnframes())
            if hashlib.sha256(pcm).hexdigest()!=item['pcm_sha256']:
                raise ValueError('对照样本PCM哈希不匹配')
            samples=np.frombuffer(pcm,np.int16).astype(np.float32)/32768
            start=time.monotonic()
            text=model.transcribe(audio=(samples,16000),language='Chinese',context=item['context']['text'])[0].text
            text,repair=retry_repetitive_transcript(model,samples,16000,text,context=item['context']['text'])
            row={**item,'hypothesis':text,'decode_repair':repair,'elapsed_seconds':time.monotonic()-start}
            row['rtf']=row['elapsed_seconds']/item['duration']
            report['results'].append(row)
            report['peak_rss_mb']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024
            (output/'hypotheses.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(json.dumps(dict(id=item['id'],hypothesis=text,elapsed_seconds=row['elapsed_seconds']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['prepare','infer'])
    parser.add_argument('--case',choices=list(CASES))
    parser.add_argument('--model',choices=list(MODELS))
    parser.add_argument('--audio',type=Path,default=Path('asr-failure-audio'))
    parser.add_argument('--output',type=Path,default=Path('asr-failure-result'))
    args=parser.parse_args()
    if args.mode=='prepare':
        if not args.case:parser.error('prepare requires --case')
        prepare(args.case,args.audio/args.case)
    else:
        if not args.model:parser.error('infer requires --model')
        infer(args.model,args.audio,args.output)
