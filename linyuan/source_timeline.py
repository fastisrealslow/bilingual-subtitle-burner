"""Exact audio-clock evidence for two versions of one source URL.

This only relates published time ranges. It never restores visual approvals,
changes a media hash, or approves a transcript, cover or finished video.
"""
import hashlib
import json
import math
from pathlib import Path
import re

REGISTRY=Path(__file__).with_name('source_timeline_evidence.json')
SCOPE='same_url_published_audio_timeline_only'


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def sha(value):return isinstance(value,str) and bool(re.fullmatch('[a-f0-9]{64}',value))


def number(value):return type(value) in (int,float) and math.isfinite(value)


def stable_report(report):
    result=json.loads(json.dumps(report))
    for key in ('source_video_sha256','elapsed_seconds','peak_rss_mb'):result.pop(key,None)
    for key in ('elapsed_seconds','peak_rss_mb'):result.get('alignment',{}).pop(key,None)
    return result


def validate_full_report(report):
    duration=report.get('duration');core=report.get('core_range')
    if (report.get('version')!=1 or not sha(report.get('source_video_sha256'))
            or not sha(report.get('source_pcm_sha256'))
            or report.get('audio_preprocessing')!='ffmpeg-source-clock-v2'
            or not number(duration) or duration<=0 or not isinstance(core,list) or len(core)!=2
            or core[0]!=0 or not number(core[1]) or abs(core[1]-duration)>.001
            or not report.get('model_revision') or not (report.get('alignment') or {}).get('model_revision')):
        raise ValueError('Full source-clock evidence is required')
    cursor=0
    chunks=report.get('chunks') or []
    for chunk in chunks:
        a,b=chunk.get('core_start'),chunk.get('core_end')
        if not number(a) or not number(b) or abs(a-cursor)>.001 or b<=a:
            raise ValueError('Raw source-clock coverage has a gap or overlap')
        if not isinstance(chunk.get('words'),list):raise ValueError('Raw words are unavailable')
        cursor=b
    if not chunks or abs(cursor-duration)>.001:raise ValueError('Raw source-clock coverage is incomplete')


def verify(old,new,*,source_key,old_run,old_artifact,new_run,new_artifact):
    validate_full_report(old);validate_full_report(new)
    if old['source_video_sha256']==new['source_video_sha256']:
        raise ValueError('Distinct video versions are required')
    # Compare every original word, clock, coverage range and model setting.
    # Only source video bytes and resource/timing telemetry may differ.
    if stable_report(old)!=stable_report(new):
        raise ValueError('PCM, words, alignment or source-clock evidence differs')
    if not source_key or any(type(v) is not int or v<=0 for v in (old_run,old_artifact,new_run,new_artifact)):
        raise ValueError('Exact URL and evidence provenance are required')
    return dict(version=1,scope=SCOPE,source_key=source_key,
        video_sha256=[old['source_video_sha256'],new['source_video_sha256']],
        source_pcm_sha256=old['source_pcm_sha256'],duration_sec=old['duration'],
        shared_evidence_sha256=digest(stable_report(old)),visual_approved=False,media_approved=False,
        evidence=[dict(run_id=old_run,artifact_id=old_artifact,report_sha256=digest(old)),
                  dict(run_id=new_run,artifact_id=new_artifact,report_sha256=digest(new))])


def valid(proof):
    if not isinstance(proof,dict):return False
    hashes=proof.get('video_sha256');evidence=proof.get('evidence')
    return (proof.get('version')==1 and proof.get('scope')==SCOPE and bool(proof.get('source_key'))
        and isinstance(hashes,list) and len(hashes)==2 and hashes[0]!=hashes[1] and all(map(sha,hashes))
        and sha(proof.get('source_pcm_sha256')) and sha(proof.get('shared_evidence_sha256'))
        and number(proof.get('duration_sec')) and proof['duration_sec']>0
        and proof.get('visual_approved') is False and proof.get('media_approved') is False
        and isinstance(evidence,list) and len(evidence)==2 and all(isinstance(e,dict)
            and type(e.get('run_id')) is int and e['run_id']>0
            and type(e.get('artifact_id')) is int and e['artifact_id']>0
            and sha(e.get('report_sha256')) for e in evidence))


def registry_sha256():return hashlib.sha256(REGISTRY.read_bytes()).hexdigest()


def lookup(source_key,a,b):
    try:rows=json.loads(REGISTRY.read_text()).get('proofs',[])
    except (OSError,ValueError,AttributeError):return None
    return next((p for p in rows if valid(p) and p['source_key']==source_key
        and set(p['video_sha256'])=={a,b}),None)


def related(source_key,a,b,current,previous):
    p=lookup(source_key,a,b)
    ranges=list(current or [])+list(previous or [])
    return bool(p and ranges and all(number(x) and number(y) and 0<=x<y<=p['duration_sec']+.05
        for x,y in ranges))
