"""Shared daily editing contract, used by rendering and the deployed uploader."""
import hashlib
import json
import math
import re
from pathlib import Path

VERSION = 2026090604
MIN_SECONDS = 120.0
TARGET_SECONDS = 180.0

# These are source-bound, manually confirmed ASR corruptions from the
# 2026-09-07 production artifact.  They are rejection evidence, not guessed
# rewrites: the offline recognizer must not silently turn them into fluent text.
ASR_CONTAMINATION_FRAGMENTS = (
    '哎印的印一段时间我就对了',
    '买了一个骗公司不挣钱的',
    '你买片公司万丈深渊',
    # 2026-09-07 qg12 re-render, verified from the delivered ASS files.
    # These exact fragments also quarantine the already-rendered MP4s before
    # FC can upload them at the next publication window.
    '不在半山药也在办三药以上',
    '收索掉投对经头',
    '因瑞达老板',
    '受到多体但是我们看他的',
    # 2026-09-07 ly-0907-894bf4, verified from the delivered ASS.  This
    # render crossed an interviewer sign-off into another question and also
    # contained speaker-name / idiom corruptions, so it is not one argument.
    '非常谢谢谢谢林园先生',
    '呃刘源先生',
    '平安化起',
)


def text_digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def transcript_integrity_error(text):
    compact = re.sub(r'\s+', '', str(text or ''))
    hit = next((fragment for fragment in ASR_CONTAMINATION_FRAGMENTS
                if fragment in compact), None)
    if hit:
        return f'实际字幕含已确认的CPU ASR污染片段「{hit}」，须听辨或更换选段'
    return None


def ass_dialogue_text(content):
    """Return the exact visible dialogue text from an ASS file.

    The uploader uses this to inspect the rendered subtitle payload instead of
    trusting a boolean in meta.json.
    """
    rows = []
    for line in str(content or '').splitlines():
        if not line.startswith('Dialogue:'):
            continue
        fields = line.split(',', 9)
        if len(fields) != 10:
            continue
        visible = re.sub(r'\{[^}]*\}', '', fields[9])
        rows.append(visible.replace(r'\N', '').replace(r'\n', ''))
    return re.sub(r'\s+', '', ''.join(rows))


def subtitle_files_text(base_dir, names):
    base = Path(base_dir)
    texts = []
    for name in names or []:
        path = base / str(name)
        if not path.is_file() or path.resolve().parent != base.resolve():
            raise ValueError('实际字幕文件缺失或路径非法')
        texts.append(ass_dialogue_text(path.read_text(encoding='utf-8-sig')))
    return ''.join(texts)


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
    summary = re.sub(r'[^0-9A-Za-z\u4e00-\u9fff]+', '', str(review['summary']))
    if summary in {'主题理由和结论', '主题理由结论'}:
        return '完整观点摘要仍是字段占位文案，未实际核对内容'
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
