"""Shared daily editing contract, used by rendering and the deployed uploader."""
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

VERSION = 2026090604
MIN_SECONDS = 120.0
TARGET_SECONDS = 180.0
# 2026-09-09: user removed the mandatory model opinion-completeness review.
# This is a declared omission of that review, never a synthetic positive verdict.
MODEL_REVIEW_POLICY_VERSION = 2026090901


def model_review_skipped(review):
    return bool(isinstance(review,dict)
        and review.get('version')==VERSION
        and review.get('status')=='skipped'
        and review.get('review_protocol')=='model-review-disabled-v1'
        and review.get('model_review_policy_version')==MODEL_REVIEW_POLICY_VERSION
        and review.get('reason')=='user_disabled_model_completeness_review'
        and re.fullmatch(r'[0-9a-f]{64}',str(review.get('transcript_sha256','')))
        and not any(key in review for key in ('standalone_opening','complete_argument',
            'reasoning_present','natural_ending','requires_audio_review')))

# Editing inputs verified against the original cue boundaries. This permits
# one specific chronological omission, never publication or subtitle approval.
REVIEWED_OMISSION_RANGES = {
    '9dc2b7c6f82570984a52ccdff5c4a41a7595c0a129b1919df81d7539a266a345':
        ((938.6,1037.16),(1044.44,1121.16)),
}


def reviewed_omission_matches(source_sha, segments):
    expected=REVIEWED_OMISSION_RANGES.get(source_sha)
    actual=intervals(segments)
    return bool(expected and actual and len(actual)==len(expected)
                and all(abs(a-c)<.05 and abs(b-d)<.05
                        for (a,b),(c,d) in zip(actual,expected)))


def source_key(url):
    """A Bilibili collection page is a separate mother; tracking args are not."""
    parsed = urlparse(str(url or ''))
    if parsed.hostname in {'www.bilibili.com', 'bilibili.com', 'm.bilibili.com'}:
        match = re.search(r'/video/(BV[0-9A-Za-z]+)', parsed.path)
        if match:
            page = parse_qs(parsed.query).get('p', ['1'])[0]
            return match.group(1) + ':p' + str(int(page)) if page.isdigit() and int(page)>0 else str(url)
    return str(url or '').strip()


def intervals(segments):
    result=[]
    for row in segments or []:
        try:
            a,b=float(row['start']),float(row['end'])
        except (KeyError,TypeError,ValueError):
            return None
        if not math.isfinite(a+b) or a<0 or b<=a:
            return None
        result.append((a,b))
    return result or None


def source_reuse_error(meta, source_url, state):
    """Permit different arguments from a mother only with usable old ranges.

    This is additional to audio/text/visual fingerprint deduplication. It never
    grants publication approval and refuses legacy receipts with unknown ranges.
    """
    current=intervals(meta.get('segments'))
    digest=meta.get('source_sha256')
    for info in state.get('published',{}).values():
        same_url=bool(source_url and source_key(info.get('source_url'))==source_key(source_url))
        parts=info.get('parts') or ([info] if info.get('bvid') else [])
        for old in parts:
            if old.get('status')=='skipped' or not old.get('bvid'):
                continue
            old_digest=old.get('source_sha256') or info.get('source_sha256')
            same_digest=bool(digest and old_digest==digest)
            if not same_url and not same_digest:
                continue
            previous=intervals(old.get('source_segments'))
            if not current or not previous:
                return '同源历史缺少可核对的起止段，不能确认是未用内容'
            if same_url and digest and old_digest and digest!=old_digest:
                return '同一来源URL的母片内容哈希已改变，须核对旧段对应关系'
            if any(min(b,d)-max(a,c)>.3 for a,b in current for c,d in previous):
                return '与已经发布的母片时间段重叠，保留其他完整观点'
    return None

# These are source-bound, manually confirmed ASR corruptions from the
# 2026-09-07 production artifact.  They are rejection evidence, not guessed
# rewrites: the offline recognizer must not silently turn them into fluent text.
ASR_CONTAMINATION_FRAGMENTS = (
    # Actual Qwen pilot ASS, run 34076248043: changing recognizer is not QA.
    '资本是足力的',
    '生产效率大大不提高',
    '乐视网肯定是因为见不迟',
    '那就我就我们就当时就就挣起来了',
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
    # 2026-09-07 ly-0907-d04876, verified from its real ASS.  Keep the
    # recognizer evidence unchanged and quarantine this exact corrupt render.
    '大部分万象前朝的那个投资总监',
    '现在的这个还子高',
    '甚至还要倾家账',
    '我没有犯的标准就连干这个事呢',
    '白度趋势',
    '我能挣钱我不会拉着你的',
    # 2026-09-07 ly-0907-f95a57, verified from the delivered ASS files.
    # Several parts begin mid-thought or contain unresolved number/term errors;
    # retain the recognizer output as rejection evidence instead of rewriting it.
    '你跟着里边肯定能赚钱不见得',
    '它不见代不见得',
    '那他总是这样',
    '新智生产力',
    '投老人口老龄化',
    '还是要就是企业的目的是为什么',
    '时好时候',
    '今天是有是投资的好时候',
    '有创8%的股息',
    '炒小炒心',
    '大概在115年16年的时候',
    # 2026-09-07 ly-0907-851152, verified from its delivered ASS files.
    # All accepted parts were static audio cards and the recognizer output
    # contains unresolved numbers/names or an incomplete ending.
    '我说细生说的',
    '实际上我是十0年前我们都在说这个事啊',
    '过去1年中国的老龄化比10年前是严重了很多',
    '这就是我我举了个中药的例',
    '大概是667岁',
    '那可不得了那我肯定是花大财',
    # 2026-09-07 ly-0907-a7f0a1, verified from both delivered ASS files.
    # The two audio cards also overlap by about 42 seconds; these fragments
    # preserve the separate ASR/standalone-ending reasons for rejecting them.
    '不不单纯是看书的股',
    '你这个股司不起来哪有钱去消费',
    '7块多是倒着来到15块牛市启动',
    '今天来的不是笨难',
    '你提戚过这怎么涨了这么高了你还跑虑听这',
    '大概的时件不是高位',
    '那无非是这三个',
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
    if model_review_skipped(review):
        return None
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


def title_attribution_error(title):
    if re.search(r'请问|请教您|您(?:觉得|认为|如何|有没有|能不能)|你也聊聊|(?:和|跟)我们分享一下',str(title or '')):
        return '标题引用了采访者提问，不能署为嘉宾本人观点'
    return None


def metadata_error(meta, actual_seconds=None):
    attribution=title_attribution_error(meta.get('title'))
    if attribution:return attribution
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
            review=meta.get('editorial_review') or {}
            if (not reviewed_omission_matches(meta.get('source_sha256'),segments)
                    or review.get('review_protocol')!=3
                    or review.get('omission_preserves_meaning') is not True
                    or review.get('omitted_is_parenthetical') is not True
                    or not review.get('omitted_text_sha256')):
                return '日常观点片须来自一个完整论述；删去插语须有原片范围和独立语义复核'
        source_duration = sum(float(s['end'])-float(s['start']) for s in segments)
        if abs(source_duration - duration) > 1.0:
            return '源选段与成片时长不一致，禁止补空白、重复或变速凑时长'
    except (TypeError, ValueError, KeyError):
        return '成片或源选段时长无效'
    return review_error(meta.get('editorial_review'))
