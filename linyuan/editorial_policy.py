"""Shared daily editing contract, used by rendering and the deployed uploader."""
import hashlib
import json
import math

VERSION = 2026090604
MIN_SECONDS = 120.0
TARGET_SECONDS = 180.0


def text_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def plan_identity(cues, target_seconds):
    return text_digest(json.dumps({'version': VERSION, 'target': target_seconds,
                                   'cues': cues}, ensure_ascii=False, sort_keys=True))


def range_seconds(cues, pick):
    a, b = pick['start'], pick['end']
    if type(a) is not int or type(b) is not int or not 0 <= a <= b < len(cues):
        raise ValueError('选段必须使用有效的0起始字幕序号')
    duration = float(cues[b]['end']) - float(cues[a]['start'])
    if not math.isfinite(duration) or duration < MIN_SECONDS:
        raise ValueError('完整观点不足120秒，换段或补足同一观点上下文，禁止短摘句凑数')
    return duration


def review_error(review):
    if not isinstance(review, dict) or review.get('version') != VERSION:
        return '缺少新版完整观点验收'
    for field in ('standalone_opening', 'complete_argument', 'reasoning_present', 'natural_ending'):
        if review.get(field) is not True:
            return '完整观点验收未通过：' + field
    if review.get('requires_audio_review') is not False:
        return '原话存在影响观点的识别歧义，须听辨或更换素材'
    if not review.get('transcript_sha256') or not review.get('summary'):
        return '缺少完整观点文本指纹和内容摘要'
    return None


def metadata_error(meta, actual_seconds=None):
    try:
        duration = float(meta.get('duration_sec', 0))
        if not math.isfinite(duration) or duration < MIN_SECONDS:
            return '成片不足120秒，不符合用户要求的2～3分钟完整观点'
        if actual_seconds is not None:
            actual = float(actual_seconds)
            if not math.isfinite(actual) or actual < MIN_SECONDS:
                return '实际MP4不足120秒，拒绝用元数据冒充长片'
            if abs(actual - duration) > 1.0:
                return '实际MP4时长与验收记录不一致'
        segments = meta.get('segments') or []
        if len(segments) != 1:
            return '日常观点片须来自一个连续完整论述，禁止拼接无关短句'
        source_duration = float(segments[0]['end']) - float(segments[0]['start'])
        if abs(source_duration - duration) > 1.0:
            return '源选段与成片时长不一致，禁止补空白、重复或变速凑时长'
    except (TypeError, ValueError, KeyError):
        return '成片或源选段时长无效'
    return review_error(meta.get('editorial_review'))
