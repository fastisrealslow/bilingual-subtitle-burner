"""Source-bound channel correction; never infer acceptance from audio energy."""
import json
from pathlib import Path

DEFAULT = 'ffmpeg-mono-v1'
LEFT = 'left-channel-v1'


def policy(source_sha):
    config=json.loads((Path(__file__).parent/'asr_production_config.json').read_text())
    value=config.get('source_overrides',{}).get(source_sha,{}).get('audio_preprocessing',DEFAULT)
    if value not in (DEFAULT,LEFT):
        raise ValueError('Unsupported source audio preprocessing')
    return value


def matches(reports, expected):
    return all(r.get('audio_preprocessing',DEFAULT)==expected for r in reports)


def asr_args(expected):
    if expected==LEFT:return ['-af','pan=mono|c0=c0','-ac','1']
    if expected==DEFAULT:return ['-ac','1']
    raise ValueError('Unsupported source audio preprocessing')


def render_prefix(expected):
    if expected==LEFT:return 'pan=mono|c0=c0,'
    if expected==DEFAULT:return ''
    raise ValueError('Unsupported source audio preprocessing')
