"""Source-bound channel correction; never infer acceptance from audio energy."""
import json
import math
import subprocess
from pathlib import Path

LEGACY_DEFAULT = 'ffmpeg-mono-v1'
LEGACY_LEFT = 'left-channel-v1'
DEFAULT = 'ffmpeg-source-clock-v2'
LEFT = 'left-source-clock-v2'
CLOCK_FILTER = 'aresample=16000:async=1000:first_pts=0'


def normalized_policy(value):
    value={LEGACY_DEFAULT:DEFAULT,LEGACY_LEFT:LEFT}.get(value,value)
    if value not in (DEFAULT,LEFT):raise ValueError('Unsupported source audio preprocessing')
    return value


def policy(source_sha):
    config=json.loads((Path(__file__).parent/'asr_production_config.json').read_text())
    value=config.get('source_overrides',{}).get(source_sha,{}).get('audio_preprocessing',DEFAULT)
    return normalized_policy(value)


def matches(reports, expected):
    return all(r.get('audio_preprocessing',LEGACY_DEFAULT)==expected for r in reports)


def asr_args(expected):
    if expected==LEFT:return ['-af','pan=mono|c0=c0,'+CLOCK_FILTER,'-ac','1']
    if expected==DEFAULT:return ['-af',CLOCK_FILTER,'-ac','1']
    raise ValueError('Unsupported source audio preprocessing')


def render_prefix(expected):
    # Keep delivery on the same presentation clock; retain its native sample
    # rate rather than unnecessarily converting the published audio to 16k.
    clock='aresample=async=1000:first_pts=0,'
    if expected==LEFT:return 'pan=mono|c0=c0,'+clock
    if expected==DEFAULT:return clock
    raise ValueError('Unsupported source audio preprocessing')


def clock_proof(source, pcm_duration, expected):
    """Verify the actual decoded PCM against the source presentation timeline."""
    raw=subprocess.check_output(['ffprobe','-v','error','-select_streams','a:0',
        '-show_entries','stream=start_time,duration:format=start_time,duration',
        '-of','json',str(source)],text=True,timeout=60)
    data=json.loads(raw);stream=data['streams'][0];fmt=data.get('format') or {}
    start=float(stream.get('start_time') or 0)-float(fmt.get('start_time') or 0)
    end=start+float(stream.get('duration') or fmt['duration'])
    drift=abs(float(pcm_duration)-end)
    if not math.isfinite(drift) or drift>.25:
        raise ValueError(f'转写PCM与来源媒体时钟不一致：PCM {pcm_duration:.3f}s / PTS {end:.3f}s')
    return dict(version=2,audio_preprocessing=expected,source_audio_end_sec=end,
        pcm_duration_sec=pcm_duration,drift_sec=drift,tolerance_sec=.25,passed=True)
