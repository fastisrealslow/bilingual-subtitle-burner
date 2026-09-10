# -*- coding: utf-8 -*-
"""林园流水线 · 境内执行端（阿里云函数计算 FC）

部署
----
1. 函数计算控制台 → 创建函数（Python 3.10+，运行时选「自定义」或标准 Python）
2. 本文件为入口 index.py，配两个定时触发器：
     dispatch_handler   每天 10:00（cron: 0 0 10 * * *）
     publish_handler    每小时（cron: 0 30 * * * *）
3. 依赖层（Layer）：pip install biliup requests -t python/
4. 环境变量：
     GITHUB_TOKEN        GitHub PAT（repo + actions 权限）
     BILIBILI_COOKIES    cookies.json 全文（biliup login 产出）

为什么需要这个函数
------------------
- 微博/B站 CDN 封海外 IP（GitHub runner 下不了源视频）→ 选片下载须在境内
- B站 upos 投稿对海外 IP 零吞吐（CI 实测 16 分钟零字节）→ 投稿须在境内
- 境内阿里云实测：下载正常、投稿 30MB/s 秒传（2026-08-15 验证）
"""
import difflib
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

# Repo scripts load index from linyuan/fc; deployed ZIP modules sit together.
if str(Path(__file__).resolve().parent.parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import editorial_policy as editorial

log = logging.getLogger()
log.setLevel(logging.INFO)

REPO = "fastisrealslow/bilingual-subtitle-burner"
API = f"https://api.github.com/repos/{REPO}"
WF_PRODUCE = "linyuan-produce-cn.yml"
DATA_JSON = "linyuan/dashboard/data.json"
RELEASE_TAG = "staging"
DELIVERY_RELEASE_TAG = "deliver"

MIN_DUR, MAX_DUR = 120, 5400            # 源片须足够产出至少2分钟的连续完整观点
# 竞品号：监控但不抄（视频在 data.json 供分析，选片/出片时跳过，2026-08-29）
COMPETITOR_AUTHORS = {"园园滚雪球"}
MAX_PER_DAY = 10                         # 2026-09-05：目标维持 8-10 条合格库存，失败候选不再挤掉当天供片
MAX_PUBLISH_PER_DAY = 3                  # 2026-09-08：精选三条，长访谈也占当天名额
TARGET_READY_RESERVE = 12
MAX_ACTIVE_SOURCES = 6
SOURCE_INVENTORY_KEY = 'linyuan/.automation/source_inventory.json'
DISPATCH_LEASE_KEY = 'linyuan/.automation/pipeline_lease.json'
# User's 2026-09-06 request excludes the old batch from today's new six.
# Historical totals remain intact. Only these named, individually reviewed
# outputs may use the separate, expiring six-slot allowance.
FRESH_SIX_DATE = "2026-09-06"
LONG_SIX_SLUGS = {f"ly-long-six-0906-{n:02d}" for n in range(1, 10)}
FRESH_SIX_SLUGS = {f"ly-fresh-six-0906-{n:02d}" for n in range(1, 10)} | LONG_SIX_SLUGS
HIDDEN_SHORT_SIX_BVIDS = {"BV1ZGbs6GEdZ","BV16Gbs6GEgv","BV1Zjbs6zEB2",
                        "BV1Zjbs6zELR","BV1Bjbs6zEAm","BV1ojbs6zE5K"}
# Six reviewed long outputs; immutable run IDs and actual-file hashes below.
FRESH_SIX_APPROVED = {
    "ba60c79cfbd8b20d940cf647d8ead19c0715d22658a2553091b7290a8e4a51ae": {
        "slug": "ly-long-six-0906-07",
        "source_url": "https://www.bilibili.com/video/BV1ADetzQEuE",
        "review": "Actual complete MP4 decode, source-conserving subtitle groups, rendered frames and covers reviewed. Continuous 120+ second argument; live target, full-face, QR and black-region gates passed. Narrow source-supported ASR corrections retain original raw evidence.",
        "part_index": 0,
        "artifact_name": "long-six-0906-reviewed-p1",
        "final": "final_1.mp4",
        "cover": "cover_1.jpg",
        "title": "林园：这个时候我更要强调不卖",
        "render_mode": "live_video_card",
        "duration_sec": 135.621333,
        "run_id": 34038895681
    },
    "17a8bb2b1e480ce5da154c9e0283707f4c075333fb3657448950d6896fb91ced": {
        "slug": "ly-long-six-0906-09",
        "source_url": "https://www.bilibili.com/video/BV1ADetzQEuE",
        "review": "Actual complete MP4 decode, source-conserving subtitle groups, rendered frames and covers reviewed. Continuous 120+ second argument; live target, full-face, QR and black-region gates passed. Narrow source-supported ASR corrections retain original raw evidence.",
        "part_index": 0,
        "artifact_name": "long-six-0906-reviewed-p2",
        "final": "final_2.mp4",
        "cover": "cover_2.jpg",
        "title": "林园：我们认为有价值的公司是可以入场的",
        "render_mode": "live_video_card",
        "duration_sec": 165.541333,
        "run_id": 34039301459
    },
    "6b983beffc2e3d93ca3706a4918ad0e7e030fa3713ff4a604372f9db076e3178": {
        "slug": "ly-long-six-0906-06",
        "source_url": "https://www.bilibili.com/video/BV1ADetzQEuE",
        "review": "Actual complete MP4 decode, source-conserving subtitle groups, rendered frames and covers reviewed. Continuous 120+ second argument; live target, full-face, QR and black-region gates passed. Narrow source-supported ASR corrections retain original raw evidence.",
        "part_index": 0,
        "artifact_name": "long-six-0906-reviewed-p3",
        "final": "final_3.mp4",
        "cover": "cover_3.jpg",
        "title": "林园：中成药实际上也在创新，市场会非常大",
        "render_mode": "live_video_card",
        "duration_sec": 124.421333,
        "run_id": 34039434560
    },
    "99c471a77855c33859444d96891734092862d4ba4d22448698c72715c3b8686f": {
        "slug": "ly-long-six-0906-09",
        "source_url": "https://www.bilibili.com/video/BV1ADetzQEuE",
        "review": "Actual complete MP4 decode, source-conserving subtitle groups, rendered frames and covers reviewed. Continuous 120+ second argument; live target, full-face, QR and black-region gates passed. Narrow source-supported ASR corrections retain original raw evidence.",
        "part_index": 1,
        "artifact_name": "long-six-0906-reviewed-p4",
        "final": "final_4.mp4",
        "cover": "cover_4.jpg",
        "title": "林园：现在的公司成为龙头的概率并不大",
        "render_mode": "live_video_card",
        "duration_sec": 155.941333,
        "run_id": 34039301459
    },
    "f98dd9b43afa9768f445eac518cf551decb9e7ee6e6043400abf30767090e9b0": {
        "slug": "ly-long-six-0906-08",
        "source_url": "https://www.bilibili.com/video/BV1ADetzQEuE",
        "review": "Actual complete MP4 decode, source-conserving subtitle groups, rendered frames and covers reviewed. Continuous 120+ second argument; live target, full-face, QR and black-region gates passed. Narrow source-supported ASR corrections retain original raw evidence.",
        "part_index": 0,
        "artifact_name": "long-six-0906-reviewed-p5",
        "final": "final_5.mp4",
        "cover": "cover_5.jpg",
        "title": "林园：人口的下降会对白酒有影响",
        "render_mode": "live_video_card",
        "duration_sec": 131.454362,
        "run_id": 34037860686
    },
    "0f9fc57ac3398d11f3ef3b7068631c147460c78e3a8e9efdefb9f979b91829ff": {
        "slug": "ly-long-six-0906-07",
        "source_url": "https://www.bilibili.com/video/BV1ADetzQEuE",
        "review": "Actual complete MP4 decode, source-conserving subtitle groups, rendered frames and covers reviewed. Continuous 120+ second argument; live target, full-face, QR and black-region gates passed. Narrow source-supported ASR corrections retain original raw evidence.",
        "part_index": 1,
        "artifact_name": "long-six-0906-reviewed-p6",
        "final": "final_6.mp4",
        "cover": "cover_6.jpg",
        "title": "林园：炒作和投资的本质区别",
        "render_mode": "live_video_card",
        "duration_sec": 167.221333,
        "run_id": 34038895681
    }
}


def fresh_six_counter(slug):
    return 'replacement_six' if slug in LONG_SIX_SLUGS else 'fresh_six'


def fresh_six_budget(daily, slug, today):
    if today != FRESH_SIX_DATE or slug not in FRESH_SIX_SLUGS:
        return None
    return daily.setdefault(fresh_six_counter(slug), {"date": FRESH_SIX_DATE, "count": 0,
        "live_video_count": 0, "audio_card_count": 0,
        "reason": ("用户已隐藏短六条，明确要求重做并发布六条2～3分钟完整观点；历史保留"
                   if slug in LONG_SIX_SLUGS else "用户要求旧批次不计入今天新六条；历史实际总数保留")})


def replacement_comparison_state(st, meta, slug):
    """Only reviewed replacement hashes may supersede the six user-hidden shorts."""
    approved=FRESH_SIX_APPROVED.get((meta.get('fingerprints') or {}).get('sha256')) or {}
    if slug not in LONG_SIX_SLUGS or approved.get('slug') != slug:
        return st
    published={}
    for old_slug,info in (st.get('published') or {}).items():
        parts=info.get('parts') or []
        if parts:
            retained=[p for p in parts if p.get('bvid') not in HIDDEN_SHORT_SIX_BVIDS]
            if retained:
                published[old_slug]={**info,'parts':retained}
        elif info.get('bvid') not in HIDDEN_SHORT_SIX_BVIDS:
            published[old_slug]=info
    return {**st,'published':published}


def final_live_identity_error(meta):
    if meta.get('render_mode') != 'live_video_card':
        return None
    proof=meta.get('final_live_identity') or {}
    same={n for n in proof.get('same_person_frames',[]) if type(n) is int and 1<=n<=6}
    try:
        confidence=float(proof.get('confidence') or 0)
    except (ValueError,TypeError):
        confidence=0
    if (proof.get('speaker')!='林园' or proof.get('sample_count')!=6
            or len(same)<5 or confidence<.75 or proof.get('watermark_texts')):
        return '实际动态窗口缺少林园本人及台标复检'
    context=meta.get('interview_context')
    if context or proof.get('sampling_scope')=='verified_guest_turns':
        try:
            frames=context['frames'];roles=context['roles'];cursor=0
            counts={'guest':0,'participant':0,'source_illustration':0,'editorial_card':0}
            for row in roles:
                if row['start_frame']!=cursor or row['end_frame']<=cursor:raise ValueError()
                counts[row['role']]+=row['end_frame']-cursor;cursor=row['end_frame']
            times=context['target_sample_times']
            fps=frames/context['encoded_duration']
            version=context['mode']
            if version=='verified_interview_context_v2':
                if (context['source_timeline_preserved'] is not True
                        or context['source_frames_preserved'] is not (counts['editorial_card']==0)
                        or context['editorial_card_frames']!=counts['editorial_card']
                        or context['graphics_profile_source_sha256']!=meta.get('source_sha256')):raise ValueError()
                cards=context['editorial_cards']
                spans=[(r['start_frame'],r['end_frame']) for r in roles if r['role']=='editorial_card']
                if [(r['start_frame'],r['end_frame']) for r in cards]!=spans:raise ValueError()
            elif version!='verified_interview_context_v1' or context['source_frames_preserved'] is not True or counts['editorial_card']:
                raise ValueError()
            if (context['passed'] is not True
                    or cursor!=frames or context['decoded_frames']!=frames or context['encoded_frames']!=frames
                    or counts['guest']!=context['matched_frames'] or counts['participant']!=context['other_face_frames']
                    or counts['source_illustration']!=context['context_picture_frames']
                    or context['context_picture_frames']!=context['no_face_frames']+context['unmatched_detection_frames']
                    or (counts['guest']+counts['participant'])/frames<.7
                    or context['source_sha256']!=meta.get('source_sha256')
                    or proof.get('sampling_scope')!='verified_guest_turns'
                    or proof.get('sample_times')!=times or len(times)!=6
                    or abs(context['encoded_duration']-float(meta['duration_sec']))>.15):raise ValueError()
            for t in times:
                if not any(r['role']=='guest' and r['start_frame']<=int(t*fps)<r['end_frame'] for r in roles):
                    raise ValueError()
        except (TypeError,KeyError,ValueError,ZeroDivisionError):
            return '访谈逐帧人物/资料画面记录不完整，不能用挑选的人脸帧替代整段核验'
    return None


def fresh_six_review_error(meta, video, slug, source_url):
    import hashlib
    sha = (meta.get("fingerprints") or {}).get("sha256")
    approved = FRESH_SIX_APPROVED.get(sha) or {}
    if approved.get("slug") != slug or approved.get("source_url") != source_url:
        return "本次新六条尚无逐条实际MP4验收记录"
    error=final_live_identity_error(meta)
    if error:
        return error
    digest = hashlib.sha256()
    with Path(video).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != sha:
        return "实际MP4与已验收文件哈希不一致"
    return None

PENDING_LIMIT = 24                        # 2026-09-05：允许 8-10 条安全库存；发布仍保持每日 3 条上限
                                          # 2026-09-02 由 10 提到 15：MAX_PER_DAY=7 时一次调度就可能触顶，
                                          # 导致次日调度被永久卡住
MAX_ATTEMPTS = 24                        # 2026-09-05：扩大候选尝试池；质量门禁失败不消耗有效产能
DELAY_LADDER = [5, 8, 11]                # B站定时发布阶梯（必须 >4h）
SAME_VIDEO_COOLDOWN = 48 * 3600          # 同源冷却：同一场会切片不能连发
TOPIC_COOLDOWN = 14 * 24 * 3600          # 相同观点两周内不再发，防标题农场观感
MIN_SHORT_EDGE = 480
PRODUCTION_RULES_VERSION = 2026090604   # final-window target identity + bounded isolated parts
QUALITY_GATE_VERSION = 12                # v12 读取真实ASS并校验文本哈希/已确认ASR污染
VISUAL_STANDARD_VERSION = 3
COVER_STANDARD_VERSION = 4
TITLE_ASR_BLACKLIST = ("手财", "一定折")
# 用户已明确要求：下列两批在新版真实样片验收前不得继续投稿。
# 这是发布端的精确熔断，不改历史回执，也不影响其他正常素材。
REVIEW_PAUSED_SLUGS = {
    "ly-0907-aba5c6",  # Actual ASS has truncated clauses and corrupt names/numbers in both live parts.
    "ly-0907-f95a57",  # Actual ASS: wrong financial terms and incomplete standalone openings.
    "ly-0904-f47739", "ly-parity-v3-14-0905", "ly-fresh-six-0906-05",
                       # run34074613911 final_3: actual ASS contains many
                       # unintelligible words despite a positive LLM review.
                       "ly-0907-d04876"}
REJECT_REFILL_LIMIT = 10                 # 2026-09-05：质量淘汰立即换候选，直到找到合格库存或达到安全上限
TID, COPYRIGHT = 207, 2                  # 财经商业 / 转载（转载必须带 source）

# 搜索噪音：标题命中即排除
# - 「虎林园」「东北虎林园」是老虎公园，不是林园本人
# - 「林园群」是人名「林园群」，不是投资人林园
# - 「横道河子」是东北虎产地
# - 「二埋汰」是东北虎网红名
NOISE = re.compile(r"虎林园|东北虎|横道河子|二埋汰|林园群|周瑜|雕像|泼漆|通报")
# AI 问答噪音：不是林园本人视频，是「网友问 AI 得到的回答」（2026-08-31 元宝问答混入）
AI_NOISE = re.compile(r"问了下|问一下|元宝|豆包|DeepSeek|deepseek|文心一言|通义千问|ChatGPT|Kimi|kimi|AI回答|AI问答|AI解读")

# B站源「机构白名单」（与 monitor_v2.py/stage_and_dispatch.py 保持一致）：
# 只保留明确的一手机构/官方号，其余二创个人号全排除。
# 「完整原片」识别（2026-08-27 重构）：不认作者、认内容形态。
# 真相：微博 764 条里官方媒体仅 12 条、B站 141 条里机构仅 8 条，纯一手撑不起每天 5 条。
# 改为按内容判断——「完整原片」（完整访谈/发言/直播/实录，哪怕自媒体转发）收，
# 「剪辑二创」（金句/观点/碎片/标题党）拒。
FULL_TITLE_PAT = re.compile(
    r"完整|全纪录|全记录|访谈|实录|直播|演讲|全程|发言|现场|对话|采访|股东会|路演|专访")
CLIP_TITLE_PAT = re.compile(
    r"金句|十大观点|秘诀|股神|曝光|惊人|精华|速看|语录|震撼|必看|揭秘|真相|名场面|划重点|一分钟|三分钟|解读|盘点|总结|五大|几条|个方法|条铁律")

# “标题里出现林园”不等于“视频里的人是林园”。2026-09-03 的坏样本
# 「林园也这样看！」实际全片是另一位戴眼镜的男性，却被默认 speaker=林园，
# 最终连标题和封面都被错误归因。调度层先要求标题能证明这是本人发言；下载后
# produce_cn.py 还会用参考照做多帧人物复核，形成第二道闸门。
THIRD_PARTY_TITLE_PAT = re.compile(
    r"林园(?:好友|朋友|客户|粉丝|学生).{0,12}(?:谈|说|评价|讲)|(?:好友|朋友|客户|粉丝|学生)(?:谈|评价|讲述)林园|"
    r"与林园(?:并肩|齐名|同框)|林园(?:也这样|遭(?:点名|处罚|调查)|"
    r"被(?:点名|处罚|调查)|基金|私募)|#远离#.*#林园#|"
    r"(?:怎么看|如何看)林园|林园(?:和|与)(?:但斌|段永平)")
DIRECT_SPEECH_PAT = re.compile(
    r"林园\s*[：:]|林园(?:说|表示|认为|指出|直言|强调|回应|分享|谈|称)|"
    r"(?:采访|专访|对话|演讲|股东会|路演|直播|实录|发言).*林园")


def title_has_target_speaker(title):
    """标题是否有足够证据表明素材是林园本人发言，而非仅仅提到他。"""
    title = (title or "").strip()
    if "林园" not in title or THIRD_PARTY_TITLE_PAT.search(title):
        return False
    return bool(DIRECT_SPEECH_PAT.search(title) or FULL_TITLE_PAT.search(title))

# 普通投稿好时段（北京时间）。FC 的兼容触发器仍可每小时唤醒，但只有这些
# 小时真正检查并投稿；批量任务带 batch_slug，明确绕过本限制。
PUBLISH_HOURS = {10, 16, 21}
WEEKLY_FULL_SLOT = (6, 21)               # 周日21点（北京时间，周一=0）
UPLOAD_LEASE_SECONDS = 45 * 60


def is_regular_publish_hour(now=None):
    """普通队列只在三个北京时间窗口运行。"""
    stamp = time.time() if now is None else float(now)
    return time.gmtime(stamp + 8 * 3600).tm_hour in PUBLISH_HOURS


def slot_published(st, now=None):
    local = time.gmtime((time.time() if now is None else now) + 8 * 3600)
    daily = st.get('daily_publish') or {}
    if daily.get('date') == time.strftime('%Y-%m-%d', local):
        if local.tm_hour in daily.get('published_hours', []):
            return True
    # Existing receipts predate published_hours; preserve their occupied slot.
    slot = time.strftime('%Y-%m-%d %H', local)
    for info in st.get('published', {}).values():
        for part in info.get('parts') or [info]:
            if part.get('bvid') and part.get('status') != 'skipped':
                old = time.gmtime(float(part.get('ts') or 0) + 8 * 3600)
                if time.strftime('%Y-%m-%d %H', old) == slot:
                    return True
    return False


def content_fits_slot(meta, entry=None, now=None):
    local = time.gmtime((time.time() if now is None else now) + 8 * 3600)
    full_slot = (local.tm_wday, local.tm_hour) == WEEKLY_FULL_SLOT
    full = meta.get('content_type') == 'full_interview'
    if (entry or {}).get('weekly_full_week') and not full:
        return False  # Keep this mother's full interview unused for Sunday.
    return full == full_slot


def weekly_full_request(candidate, st, now=None):
    local = time.gmtime((time.time() if now is None else now) + 8 * 3600)
    week = time.strftime('%G-W%V', local)
    if any(e.get('weekly_full_week') == week and not e.get('failed')
           for e in _latest_dispatches(st)):
        return ''
    extra = candidate.get('extra') or {}
    try:
        duration = float(extra.get('duration') or 0)
    except (AttributeError, TypeError, ValueError):
        return ''
    if duration >= 1200 and re.search(r'完整|全程|全纪录|全记录', candidate.get('title', '')):
        return week
    return ''


def has_active_upload_lease(candidate, now=None):
    """另一个调用刚声明上传时先让路，避免定时器与批处理撞车。"""
    if not candidate.get("uploading") or not candidate.get("uploading_ts"):
        return False
    stamp = time.time() if now is None else float(now)
    return stamp - float(candidate["uploading_ts"]) < UPLOAD_LEASE_SECONDS


def platform_of(source):
    """把监控源名归一化成平台名（供日志页显示）。"""
    s = (source or "").lower()
    if s.startswith("bilibili"):
        return "bilibili"
    if s.startswith("weibo"):
        return "weibo"
    if s.startswith("tencent"):
        return "tencent"
    if s.startswith("xueqiu"):
        return "xueqiu"
    if s.startswith("douyin"):
        return "douyin"
    if s.startswith("haokan"):
        return "haokan"
    if s.startswith("netease"):
        return "netease"
    return s or "unknown"


def clean_publish_desc(value):
    """投稿前最后一道简介清洗，兼容修复前生成的缓存和旧 Artifact。"""
    text = re.sub(r"https?://\S+|www\.\S+|t\.cn/\S+", "", value or "",
                  flags=re.I)
    return re.sub(r"[ \t]+\n", "\n", text).strip()


def publication_source_label(item):
    """转载来源字段只给文字归因，绝不把原站 URL 暴露到稿件信息区。"""
    platform = (item.get("source_platform")
                or platform_of(item.get("source", "")))
    labels = {
        "bilibili": "公开访谈资料（哔哩哔哩）",
        "weibo": "公开访谈资料（微博）",
        "tencent": "公开访谈资料（腾讯视频）",
        "xueqiu": "公开访谈资料（雪球）",
        "douyin": "公开访谈资料（抖音）",
        "haokan": "公开访谈资料（好看视频）",
        "netease": "公开访谈资料（网易）",
    }
    return labels.get(platform, "公开访谈资料")


TOKEN = os.environ.get("GITHUB_TOKEN", "")
COOKIES_JSON = os.environ.get("BILIBILI_COOKIES", "")

# FC 实例可复用（热启动），状态文件放 /tmp 能在短时间内防重；
# 冷启动会丢，所以 dispatched 名单同时维护在 GitHub 侧（见 state 函数）
STATE_KEY = "linyuan/.automation/fc_state.json"
# 日志页（GitHub Pages）镜像副本：Pages 不服务 .automation 点目录，
# 所以另存一份到非点路径 site/ 供 log.html 同域读取。
SITE_STATE_KEY = "site/fc_state.json"
# 运行日志：FC 每次运行的关键事件追加到这，log.html 展示，便于追踪
LOGS_KEY = "linyuan/.automation/fc_logs.json"
MAX_LOG_ENTRIES = 200


# ---------- GitHub 基础 ----------

def gh(method, path, payload=None, raw=False, timeout=120):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
        "Content-Type": "application/json", "Accept-Encoding": "gzip"})
    import gzip
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        if raw:
            return body
        return json.loads(body.decode() or "{}")


def _download_release_asset_parallel(asset, dest, max_time=1620):
    """为指定 V4 批次并行拉取 Release 分段，解决境内单连接吞吐过低。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    url = asset.get("browser_download_url") or asset.get("url")
    size = int(asset.get("size") or 0)
    if not url or size <= 0:
        return False
    # 与已验证的单条投稿链路一致：只经过 GitHub 一次，后续所有 Range
    # 直接读取同一个签名 Blob；不向 Blob 转发 GitHub 凭据。
    try:
        import requests
        redirect = requests.get(
            str(url), headers={"Authorization": f"Bearer {TOKEN}"},
            allow_redirects=False, timeout=60)
        location = redirect.headers.get("Location", "")
        if redirect.status_code in (301, 302, 303, 307, 308) \
                and location.startswith("https://"):
            url = location
    except Exception as exc:
        log.warning(f"解析 Release 签名地址失败，回退原 URL: {exc}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 2MB 以下不并行；大文件最多 16 路，最终文件按原始字节数复核。
    workers = min(16, max(2, (size + 2 * 1024 * 1024 - 1)
                           // (2 * 1024 * 1024)))
    chunk_size = (size + workers - 1) // workers
    parts = []
    for index in range(workers):
        start = index * chunk_size
        end = min(size - 1, start + chunk_size - 1)
        if start > end:
            break
        parts.append((index, start, end, dest.parent / f".{dest.name}.part{index:02d}"))

    log_event("download", f"并行下载 {dest.name}",
              f"bytes={size} segments={len(parts)}")
    flush_logs()

    def fetch_part(item):
        index, start, end, part_path = item
        result = subprocess.run([
            "curl", "-sS", "-fL", "--retry", "2",
            "--connect-timeout", "20", "--max-time", str(max_time),
            "--range", f"{start}-{end}",
            "-o", str(part_path), str(url),
        ], capture_output=True)
        expected = end - start + 1
        actual = part_path.stat().st_size if part_path.is_file() else 0
        return index, result.returncode, expected, actual, result.stderr

    results = []
    with ThreadPoolExecutor(max_workers=len(parts)) as pool:
        futures = [pool.submit(fetch_part, item) for item in parts]
        for future in as_completed(futures):
            results.append(future.result())

    failures = [x for x in results if x[1] != 0 or x[2] != x[3]]
    if failures:
        detail = "; ".join(
            f"part={x[0]} rc={x[1]} expected={x[2]} actual={x[3]} "
            f"err={x[4].decode(errors='replace')[:60]}"
            for x in failures[:3])
        log_event("download_fail", f"并行下载失败 {dest.name}", detail)
        flush_logs()
        for _, _, _, part_path in parts:
            part_path.unlink(missing_ok=True)
        return False

    with dest.open("wb") as output:
        for _, _, _, part_path in parts:
            with part_path.open("rb") as source:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            part_path.unlink(missing_ok=True)
    ok = dest.stat().st_size == size
    log_event("download_ok" if ok else "download_fail",
              f"并行下载完成 {dest.name}",
              f"bytes={dest.stat().st_size} expected={size}")
    flush_logs()
    return ok


def download_release_asset(asset, dest, max_time=1620):
    """流式下载单个 Release 资产；目标 V4 视频使用境内并行分段。"""
    url = asset.get("browser_download_url") or asset.get("url")
    if not url:
        return False
    size = int(asset.get("size") or 0)
    if ("ly-parity-v3-14-0905." in str(url)
            and size >= 2 * 1024 * 1024):
        return _download_release_asset_parallel(asset, dest, max_time=max_time)
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["curl", "-sfL", "--retry", "3", "--max-time", str(max_time),
           "-H", f"Authorization: Bearer {TOKEN}",
           "-o", str(dest), str(url)]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


_delivery_release_assets = None
_batch_delivery_release_assets = {}


def delivery_release_asset(name):
    """优先返回带原始尺寸的 Release 元数据，供分段下载与完整性校验。"""
    global _delivery_release_assets
    # One release per production batch avoids GitHub's 1000-asset shared limit.
    # Keep the old shared release readable for previously generated batches.
    slug=str(name).split('.',1)[0]
    if re.fullmatch(r'[A-Za-z0-9_-]+',slug):
        tag='deliver-'+slug
        assets=_batch_delivery_release_assets.get(tag)
        if assets is None:
            try:
                release=gh('GET',f'/releases/tags/{tag}')
                assets={str(a.get('name') or ''):a for a in release.get('assets',[])}
                if assets:_batch_delivery_release_assets[tag]=assets
            except Exception:
                assets={}
        if name in assets:return assets[name]
    if _delivery_release_assets is None:
        try:
            release = gh("GET", f"/releases/tags/{DELIVERY_RELEASE_TAG}")
            _delivery_release_assets = {
                str(asset.get("name") or ""): asset
                for asset in release.get("assets", [])
            }
        except Exception as exc:
            log.warning(f"读取 Release 元数据失败，回退可预测 URL: {exc}")
            _delivery_release_assets = {}
    asset = _delivery_release_assets.get(name)
    if asset:
        return asset
    return {"browser_download_url": (
        f"https://github.com/{REPO}/releases/download/"
        f"{DELIVERY_RELEASE_TAG}/{name}")}


def diagnose_release_download(event=None, context=None):
    """只读探测境内 FC 到逐条 Release 的链路；不进入投稿器。"""
    event = event if isinstance(event, dict) else {}
    slug = str(event.get("batch_slug") or "").strip()
    if slug != "ly-parity-v3-14-0905":
        log_event("probe_fail", "Release 探针拒绝非目标批次", slug)
        flush_logs()
        return {"ok": False, "reason": "unsupported_slug"}

    results = []
    probe_dir = Path(tempfile.mkdtemp(prefix="release-probe-"))
    try:
        for suffix in ("meta.json", "cover_1.jpg", "final_1.mp4"):
            name = f"{slug}.{suffix}"
            url = delivery_release_asset(name)["browser_download_url"]
            dest = probe_dir / suffix
            started = time.time()
            log_event("probe", f"开始探测 {suffix}", "range=0-1048575")
            flush_logs()
            result = subprocess.run([
                "curl", "-sS", "-fL", "--retry", "1",
                "--connect-timeout", "15", "--max-time", "50",
                "--range", "0-1048575",
                "-H", f"Authorization: Bearer {TOKEN}",
                "-o", str(dest), url,
            ], capture_output=True)
            elapsed = round(time.time() - started, 2)
            size = dest.stat().st_size if dest.is_file() else 0
            detail = (f"rc={result.returncode} bytes={size} seconds={elapsed} "
                      f"stderr={result.stderr.decode(errors='replace')[:100]}")
            kind = "probe_ok" if result.returncode == 0 and size > 0 else "probe_fail"
            log_event(kind, f"探测 {suffix}", detail)
            flush_logs()
            results.append({"name": suffix, "rc": result.returncode,
                            "bytes": size, "seconds": elapsed})
            if result.returncode != 0 or size <= 0:
                break
        return {"ok": all(x["rc"] == 0 and x["bytes"] > 0 for x in results),
                "results": results}
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def download_reviewed_zip(artifact_id, archive_path, attempts=3, timeout_sec=120, max_bytes=256*1024*1024):
    """Refresh signed URLs after a bounded failed/slow read; never expose tokens."""
    import requests
    archive_path=Path(archive_path)
    for attempt in range(attempts):
        try:
            redirect=requests.get(API+f'/actions/artifacts/{artifact_id}/zip',
                params={'_':str(time.time_ns())},
                headers={'Authorization':f'Bearer {TOKEN}'},
                allow_redirects=False,timeout=(10,20))
            location=redirect.headers.get('Location','')
            if redirect.status_code!=302 or not location.startswith('https://'):
                raise RuntimeError('Signed artifact redirect unavailable')
            result=subprocess.run(['curl','-fsSL','--connect-timeout','15',
                '--max-time',str(timeout_sec),'--speed-time','20','--speed-limit','32768',
                '--max-filesize',str(max_bytes),'-o',str(archive_path),location],
                capture_output=True,timeout=timeout_sec+10)
            if result.returncode!=0 or not archive_path.is_file():
                raise RuntimeError('Bounded artifact transfer failed')
            size=archive_path.stat().st_size
            if not 0<size<=max_bytes:
                raise RuntimeError('Reviewed artifact size invalid')
            with zipfile.ZipFile(archive_path) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError('Reviewed artifact CRC invalid')
            return size
        except Exception as exc:
            archive_path.unlink(missing_ok=True)
            log_event('download_retry',f'成片取件重试 {attempt+1}/{attempts}',type(exc).__name__)
            flush_logs()
    raise RuntimeError(f'Reviewed artifact exhausted {attempts} bounded attempts')


def download_v4_fast_part(slug, part_index, dest_dir):
    """Download an exact reviewed part through its signed artifact blob."""
    reviewed = next((row for row in FRESH_SIX_APPROVED.values()
                     if row.get("slug") == slug
                     and row.get("part_index") == int(part_index)), None)
    if reviewed:
        name = reviewed["artifact_name"]
        wanted = {"meta.json", reviewed["final"], reviewed["cover"]}
    elif slug == "ly-parity-v3-14-0905" and 0 <= int(part_index) < 14:
        name = f"linyuan-v4-fast-part-{int(part_index) + 1}"
        wanted = {"meta.json", f"final_{int(part_index)+1}.mp4",
                  f"cover_{int(part_index)+1}.jpg"}
    else:
        return False
    import requests
    try:
        listing = gh("GET", f"/actions/artifacts?name={name}&per_page=20")
        artifacts = [a for a in listing.get("artifacts", [])
                     if a.get("name") == name and not a.get("expired")]
        if not artifacts:
            return False
        artifact = max(artifacts, key=lambda a: int(a.get("id") or 0))
        artifact_id = int(artifact["id"])
        log_event("download", f"快通道下载 part {int(part_index)+1}",
                  f"artifact={artifact_id} bytes={artifact.get('size_in_bytes', 0)}")
        flush_logs()
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        archive_path = dest_dir / "part.zip"
        size = download_reviewed_zip(artifact_id,archive_path)
        found = set()
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                base = Path(member.filename).name
                if base not in wanted or member.is_dir():
                    continue
                with archive.open(member) as source, (dest_dir / base).open("wb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
                found.add(base)
        archive_path.unlink(missing_ok=True)
        ok = found == wanted
        log_event("download_ok" if ok else "download_fail",
                  f"快通道取件 part {int(part_index)+1}",
                  f"archive_bytes={size} files={','.join(sorted(found))}")
        flush_logs()
        return ok
    except Exception as exc:
        log_event("download_fail", f"快通道异常 part {int(part_index)+1}",
                  repr(exc)[:180])
        flush_logs()
        return False


def download_release_part(slug, part_index, dest_dir):
    """只取当前 part 的元数据、视频和封面；缺任一必需文件即回退旧 Artifact。"""
    dest_dir = Path(dest_dir)
    meta_dest = dest_dir / "meta.json"
    if not download_release_asset(
            delivery_release_asset(f"{slug}.meta.json"), meta_dest,
            max_time=120):
        return False
    try:
        payload = json.loads(meta_dest.read_text(encoding="utf-8"))
        parts = payload if isinstance(payload, list) else [payload]
        part = parts[part_index] if part_index < len(parts) else {}
        final_name = str(part.get("final") or "final.mp4")
        cover_name = str(part.get("cover") or "")
        # 封面体积小，先确认它存在；旧批次缺封面时不要先下载几百 MB 视频。
        if cover_name and not download_release_asset(
                delivery_release_asset(f"{slug}.{cover_name}"),
                dest_dir / cover_name, max_time=120):
            return False
        # v12 checks the actual ASS payload. Fetch it on the normal Release
        # path as well; otherwise every otherwise valid new MP4 is rejected.
        for subtitle_name in part.get("subtitle_files") or []:
            if not isinstance(subtitle_name, str) or Path(subtitle_name).name != subtitle_name:
                return False
            if not download_release_asset(
                    delivery_release_asset(f"{slug}.{subtitle_name}"),
                    dest_dir / subtitle_name, max_time=120):
                return False
        if not download_release_asset(
                delivery_release_asset(f"{slug}.{final_name}"),
                dest_dir / final_name):
            return False
        return True
    except (ValueError, OSError, IndexError):
        return False


def download_inventory_part(artifact_id, part_index, dest_dir):
    """Use the inspected artifact's own metadata and actual ASS, with a deadline."""
    dest_dir=Path(dest_dir)
    archive_path=dest_dir/'inventory.zip'
    try:
        download_reviewed_zip(artifact_id,archive_path,attempts=2)
        with zipfile.ZipFile(archive_path) as archive:
            payload=json.loads(archive.read('meta.json'))
            parts=payload if isinstance(payload,list) else [payload]
            part=parts[part_index]
            names=[part.get('final'),part.get('cover'),*(part.get('subtitle_files') or [])]
            if any(not isinstance(n,str) or Path(n).name!=n for n in names):
                raise ValueError('Invalid delivery file path')
            for name in names:
                (dest_dir/name).write_bytes(archive.read(name))
            (dest_dir/'meta.json').write_text(json.dumps(parts,ensure_ascii=False))
        return True
    except Exception as exc:
        log.warning('Bounded inventory download unavailable: %s',type(exc).__name__)
        return False
    finally:
        archive_path.unlink(missing_ok=True)


def load_state():
    try:
        raw = gh("GET", f"/contents/{STATE_KEY}?ref=main", raw=True)
        return json.loads(raw.decode())
    except Exception:
        return {"dispatched": [], "rejected": [], "published": {}}


# ---------- 运行日志（log.html 展示，便于追踪）----------

_log_buffer = []


def log_event(kind, msg, detail=""):
    """记录一条运行事件到本地缓冲，运行结束时统一追加到 GitHub。
    kind: dispatch|publish|skip|fail|publish_ok|dispatch_ok|retry|dedup
    """
    _log_buffer.append({"ts": int(time.time()), "kind": kind, "msg": msg[:120],
                        "detail": (detail or "")[:200]})
    log.info(f"[{kind}] {msg}")


def flush_logs():
    """把本次运行的事件追加到 fc_logs.json（保留最近 MAX_LOG_ENTRIES 条）。
    best-effort：失败只告警，绝不影响主流程。"""
    if not _log_buffer:
        return
    import base64
    try:
        try:
            cur = gh("GET", f"/contents/{LOGS_KEY}?ref=main&_={time.time()}")
            entries = json.loads(base64.b64decode(cur["content"]).decode())
            sha = cur.get("sha")
        except Exception:
            entries, sha = [], None
        entries.extend(_log_buffer)
        entries = entries[-MAX_LOG_ENTRIES:]
        content = base64.b64encode(json.dumps(entries, ensure_ascii=False).encode()).decode()
        payload = {"message": "chore(fc): 追加运行日志", "content": content}
        if sha:
            payload["sha"] = sha
        gh("PUT", f"/contents/{LOGS_KEY}", payload)
        # 同步镜像到 site/ 供日志页同域读取
        try:
            site_payload = {"message": "chore(fc): 同步日志页运行日志", "content": content}
            try:
                scur = gh("GET", f"/contents/site/fc_logs.json?ref=main&_={time.time()}")
                if isinstance(scur, dict) and scur.get("sha"):
                    site_payload["sha"] = scur["sha"]
            except Exception:
                pass
            gh("PUT", "/contents/site/fc_logs.json", site_payload)
        except Exception:
            pass
        _log_buffer.clear()
    except Exception as e:
        log.warning(f"运行日志追加失败（不影响主流程）: {e}")


def save_state(st, retries=3):
    """写回 fc_state.json。防御性设计：
    - PUT 前重新 GET 拿最新 sha（并发/缓存会让旧 sha 失效）
    - 任何失败重试 3 次，仍失败也只报错不抛出 —— 状态丢失可恢复，
      但保存失败绝不能把主流程搞崩（实测：422 直接炸了整个 dispatch）。
    """
    import base64
    content = base64.b64encode(json.dumps(
        st, ensure_ascii=False, indent=1).encode()).decode()
    for attempt in range(retries):
        try:
            payload = {"message": "chore(fc): 更新流水线状态", "content": content}
            try:
                cur = gh("GET", f"/contents/{STATE_KEY}?ref=main&_={time.time()}")
                if isinstance(cur, dict) and cur.get("sha"):
                    payload["sha"] = cur["sha"]
            except Exception as e:
                log.warning(f"取 sha 失败（首次创建时正常）：{e}")
            gh("PUT", f"/contents/{STATE_KEY}", payload)
            break
        except Exception as e:
            log.warning(f"save_state 第 {attempt+1} 次失败：{e}")
            time.sleep(2 * (attempt + 1))
    else:
        log.error("save_state 多次失败，本轮状态未保存（下轮会基于仓库里的旧状态重试）")
        _log_buffer.append({"ts": int(time.time()), "kind": "state_lost",
                            "msg": "⚠️ 状态保存失败！本轮状态未落盘（可能造成重复投稿/日志页不准）",
                            "detail": ""})
        return
    # 镜像到 site/ 供 GitHub Pages 日志页读取（best-effort，失败不影响主流程）
    try:
        payload = {"message": "chore(fc): 同步日志页状态", "content": content}
        try:
            cur = gh("GET", f"/contents/{SITE_STATE_KEY}?ref=main&_={time.time()}")
            if isinstance(cur, dict) and cur.get("sha"):
                payload["sha"] = cur["sha"]
        except Exception:
            pass
        gh("PUT", f"/contents/{SITE_STATE_KEY}", payload)
    except Exception as e:
        log.warning(f"site 镜像同步失败（不影响主流程）：{e}")


# ---------- 选片 ----------

def video_id_of(page_url, video_url):
    m = re.search(r"(BV\w+)", page_url or "")
    if m:
        from urllib.parse import parse_qs, urlparse
        page = parse_qs(urlparse(page_url).query).get('p', ['1'])[0]
        return m.group(1) + (f':p{int(page)}' if page.isdigit() and int(page) > 1 else '')
    return (video_url or "").split("?")[0] or page_url


def mp4_duration(path):
    """纯 Python 读 mvhd atom，FC 没有 ffmpeg。
    moov atom 可能 在文件头部或尾部，两头都找。
    """
    f = Path(path).open("rb")
    # 先读头部 500KB
    data = f.read(500_000)
    i = data.find(b"mvhd")
    if i < 0:
        # 头部没找到 → 读尾部 500KB
        f.seek(0, 2)
        size = f.tell()
        tail = min(500_000, size)
        f.seek(-tail, 2)
        data = f.read(tail)
        i = data.find(b"mvhd")
    f.close()
    if i < 0:
        return 0
    ver = data[i + 4]
    if ver == 1:
        # mvhd: type(4) + version(1) + flags(3) + creation(8) + modification(8) + timescale(4) + duration(8)
        ts = int.from_bytes(data[i+20:i+24], "big")
        dur = int.from_bytes(data[i+24:i+32], "big")
    else:
        # mvhd: type(4) + version(1) + flags(3) + creation(4) + modification(4) + timescale(4) + duration(4)
        ts = int.from_bytes(data[i+16:i+20], "big")
        dur = int.from_bytes(data[i+20:i+24], "big")
    return dur / ts if ts else 0


def title_similarity(a, b):
    """简单标题相似度：共同字符占比。"""
    if not a or not b:
        return 0
    # 取前 20 字符比较
    a, b = a[:20], b[:20]
    common = sum(1 for c in a if c in b)
    return common / max(len(a), len(b))


def dedup_by_title(cands, threshold=0.6):
    """同内容去重：标题相似度 > threshold 只保留一条。
    保留质量更好的：有直链 > 无直链，时长更长的优先。
    """
    result = []
    for c in cands:
        dup = False
        for i, r in enumerate(result):
            # Different cids in one collection are separate recordings even
            # when their series title is shared. Final media fingerprints still
            # reject an episode duplicated under another URL.
            ce, re_ = c.get('extra') or {}, r.get('extra') or {}
            if (isinstance(ce, dict) and isinstance(re_, dict)
                    and ce.get('bvid') == re_.get('bvid')
                    and ce.get('cid') and re_.get('cid')
                    and ce['cid'] != re_['cid']):
                continue
            if title_similarity(c["title"], r["title"]) >= threshold:
                # 比较质量：有直链的优先，都没有直链的看 extra 中的时长
                c_score = (1 if c.get("video_url") else 0)
                r_score = (1 if r.get("video_url") else 0)
                c_extra = c.get("extra", {})
                r_extra = r.get("extra", {})
                # extra 可能是 JSON 字符串，需要解析
                if isinstance(c_extra, str):
                    try:
                        c_extra = json.loads(c_extra)
                    except Exception:
                        c_extra = {}
                if isinstance(r_extra, str):
                    try:
                        r_extra = json.loads(r_extra)
                    except Exception:
                        r_extra = {}
                # 有 duration 信息的优先
                c_dur = c_extra.get("duration", 0)
                r_dur = r_extra.get("duration", 0)
                if isinstance(c_dur, str):
                    c_dur = 0
                if isinstance(r_dur, str):
                    r_dur = 0
                c_score += c_dur / 10000  # 时长加权
                r_score += r_dur / 10000
                
                if c_score > r_score:
                    result[i] = c
                dup = True
                break
        if not dup:
            result.append(c)
    return result


_TOPIC_BOILERPLATE = re.compile(
    r"林园|股神|最新|完整版|完整|现场|发言|分享|揭秘|解析|观点|投资逻辑|"
    r"投资|为什么|为何|如何|表示|认为|指出|直言|强调|回应|[年月日]")


def normalize_topic(title):
    """保留真正区分观点的字词，去掉每条标题都有的包装词。"""
    text = _TOPIC_BOILERPLATE.sub("", (title or "").lower())
    return re.sub(r"[^0-9a-z%\u4e00-\u9fff]+", "", text)


def topic_similarity(a, b):
    """标题主题相似度：字符序列 + 中文二元词交集，取更保守的高值。"""
    a, b = normalize_topic(a), normalize_topic(b)
    if min(len(a), len(b)) < 5:
        return 0.0
    seq = difflib.SequenceMatcher(None, a, b).ratio()
    aa = {a[i:i + 2] for i in range(len(a) - 1)}
    bb = {b[i:i + 2] for i in range(len(b) - 1)}
    jac = len(aa & bb) / max(1, len(aa | bb))
    return max(seq, jac)


def iter_published_parts(st):
    """兼容新旧 state，逐条返回真正发布过的 part。"""
    seen = set()
    for slug, info in (st.get("published") or {}).items():
        parts = info.get("parts") or []
        records = parts if parts else [info]
        for part in records:
            if part.get("status") == "skipped":
                continue
            marker = (part.get("bvid") or info.get("bvid") or "",
                      part.get("title") or info.get("title") or "")
            if marker in seen:
                continue
            seen.add(marker)
            yield slug, {
                "bvid": marker[0],
                "title": marker[1],
                "ts": part.get("ts") or info.get("ts") or 0,
                "fingerprints": (part.get("fingerprints")
                                 or info.get("fingerprints") or {}),
            }


def find_recent_topic(title, st, now=None, threshold=0.55, exclude_slug=None):
    """查找 14 天内已发布的同主题内容。"""
    now = time.time() if now is None else now
    for slug, old in iter_published_parts(st):
        if exclude_slug and slug == exclude_slug:
            continue
        if now - old["ts"] > TOPIC_COOLDOWN:
            continue
        score = topic_similarity(title, old["title"])
        if score >= threshold:
            return {"slug": slug, "bvid": old["bvid"],
                    "title": old["title"], "score": round(score, 3)}
    return None


def _hamming_hex(a, b):
    try:
        return (int(a, 16) ^ int(b, 16)).bit_count()
    except (TypeError, ValueError):
        return 10 ** 9


def _fingerprint_match_ratio(current, previous, max_distance):
    if not current or not previous:
        return 0.0
    matched = sum(any(_hamming_hex(a, b) <= max_distance for b in previous)
                  for a in current)
    return matched / len(current)


def fingerprint_duplicate(current, previous):
    """判断两条成片是否同内容，返回命中依据；三种模糊指纹避免单路误杀。"""
    if not current or not previous:
        return None
    if current.get("sha256") and current.get("sha256") == previous.get("sha256"):
        return "文件 SHA256 完全相同"

    cur_ngrams = set(current.get("transcript_ngrams") or [])
    old_ngrams = set(previous.get("transcript_ngrams") or [])
    shared = len(cur_ngrams & old_ngrams)
    containment = shared / max(1, min(len(cur_ngrams), len(old_ngrams)))
    if shared >= 8 and containment >= 0.45:
        return f"转写片段重合 {containment:.0%}"

    cur_text = current.get("transcript_simhash") or []
    old_text = previous.get("transcript_simhash") or []
    text_ratio = _fingerprint_match_ratio(cur_text, old_text, 10)
    text_matches = round(text_ratio * len(cur_text))
    if text_ratio >= 0.60 and text_matches >= min(2, len(cur_text)):
        return f"转写内容重合 {text_ratio:.0%}"

    video_ratio = _fingerprint_match_ratio(
        current.get("video_dhash") or [], previous.get("video_dhash") or [], 8)
    cur_audio = current.get("audio_chromaprint") or current.get("audio_spectral") or []
    old_audio = previous.get("audio_chromaprint") or previous.get("audio_spectral") or []
    audio_ratio = _fingerprint_match_ratio(cur_audio, old_audio, 3)
    if audio_ratio >= 0.625 and video_ratio >= 0.50:
        return f"音频 {audio_ratio:.0%} + 画面 {video_ratio:.0%} 重合"
    return None


def find_content_duplicate(fingerprints, st):
    """跨 URL、跨平台查找相同成片。历史记录没指纹时自动跳过。"""
    for slug, old in iter_published_parts(st):
        reason = fingerprint_duplicate(fingerprints, old["fingerprints"])
        if reason:
            return {"slug": slug, "bvid": old["bvid"], "reason": reason}
    return None


def diversify_source_candidates(candidates, limit, per_family=2):
    """Try several source families before spending all six slots on one archive."""
    families={}
    for candidate in candidates:
        extra=candidate.get('extra') or {}
        if extra.get('collection_title') and extra.get('bvid'):
            family=('collection',extra['bvid'])
        else:
            family=('author',candidate.get('author') or candidate.get('source') or 'unknown')
        families.setdefault(family,[]).append(candidate)
    selected=[]
    while len(selected)<limit and any(families.values()):
        for queue in families.values():
            count=min(per_family,len(queue),limit-len(selected))
            selected.extend(queue[:count])
            del queue[:count]
    return selected


def pick(items, st, n):
    now = time.time()
    done = {e.get("key") for e in st["dispatched"] if e.get("key")} | {e.get("key") for e in st["rejected"] if e.get("key")}
    # 已发布过的 key/source_url：绝不能因 pending_retry 残留被重新派发
    # （2026-08-25 事故：同一视频 BV1yM8x6ZEZy 连续 4 天被重复投稿）
    published_slugs = set(st.get("published", {}).keys())
    published_keys = {e.get("key") for e in st["dispatched"] if e.get("key") and e.get("slug") in published_slugs}
    published_srcs = {info.get("source_url", "") for info in st.get("published", {}).values() if info.get("source_url")}
    # 重试项：3 次失败后会进 rejected，这里从 retry_list 重新加回候选
    retry_ready = []
    for x in st.get("pending_retry", []):
        if x.get("key") in published_keys:
            continue  # 已发布过，不再重试
        if (x.get("page_url") or "").strip() in published_srcs:
            continue
        if now - x.get("ts", 0) > 30 * 60 and x.get("retries", 0) < 3:
            retry_ready.append(x)
    done -= {x["key"] for x in retry_ready}
    cooling = {e.get("video_id") for e in st["dispatched"]
               if e.get("video_id") and now - e.get("ts", 0) < SAME_VIDEO_COOLDOWN}
    cooling |= {e.get("video_id") for e in st["rejected"] if e.get("video_id")}
    cooling -= {x["video_id"] for x in retry_ready}
    # 排除已发布的视频（防止重复采集自己发的）
    published_bvs = {info.get("bvid", "") for info in st.get("published", {}).values()}
    # 微博噪音：不是林园本人视频的常见噪音关键词
    NOISE_EXTRA = re.compile(
        r"超话|小说|灯花笑|张子墨|好事多墨|木槿|"
        r"白居易|绿含滋|水风清|四韵|诗词|古诗|"
        r"陈育哲|张甜甜|胡以信|案例黑料|跟风后悔|姐妹|"
        r"两园共|打榜|应援|周边|同人|"
        r"假价值|假投机|假风险|假茅|卖书|会员|"
        r"远离林园|大势已去|时运已去|"
        r"墓园|公墓|墓地|陵园|骨灰|"
        r"灯花笑|宏成|版税|打赏|签约|出版"
    )
    cands = []
    for it in items:
        url, page = it.get("video_url") or "", it.get("url") or ""
        # 兼容旧 data.json：video_url 可能在 extra 的 video_url / mp4_url 字段
        # （2026-08-29 修复：抖音/网易/好看的直链在 extra.mp4_url，之前只查顶层 video_url 导致被当「无视频」过滤）
        extra = it.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        if not url:
            url = extra.get("video_url", "") or extra.get("mp4_url", "")
        # 必须有视频：有直链、或B站链接、或腾讯页面（dispatch 会解析）、或 extra 标记 has_video
        has_video = (bool(url) or "bilibili.com/video/" in page
                     or "news.qq.com" in page or extra.get("has_video", False))
        if not has_video:
            continue
        # 有直链或B站链接 → 可用；有页面链接 → 也可用
        if not url and "bilibili.com/video/" not in page and not page:
            continue
        key = it.get("id") or page or url
        vid = video_id_of(page, url)
        if not key or key in done or vid in cooling:
            continue
        # 排除已发布的视频（BV 号匹配）
        if vid in published_bvs:
            continue
        title = it.get("title") or ""
        # 剪辑二创（碎片/标题党）→ 拒；完整原片 → 收
        if CLIP_TITLE_PAT.search(title) and not FULL_TITLE_PAT.search(title):
            continue
        if NOISE.search(title):
            continue                                     # 老虎公园不是林园
        if NOISE_EXTRA.search(title):
            continue                                     # 微博噪音
        if AI_NOISE.search(title):
            continue                                     # AI 问答噪音（元宝/豆包等，非林园本人视频）
        # 标题必须能证明是林园本人发言；只“提到林园”的二手解说不再放行。
        if not title_has_target_speaker(title):
            continue
        # 已发主题两周内不再调度。最终成片标题和三重内容指纹还会在投稿前复检，
        # 这里先挡住明显重复，避免浪费下载、ASR 和编码算力。
        topic_dup = find_recent_topic(title, st, now=now)
        if topic_dup:
            log_event("dedup", f"候选主题冷却中，跳过 {key}",
                      f"与 {topic_dup['bvid'] or topic_dup['slug']} 相似 {topic_dup['score']:.0%}")
            continue
        # 竞品目录全部进入素材库作溯源线索，但永不直接调度。除了作者名，
        # 再检查 source_role，避免后续改作者字段时意外把参考条目当成片源。
        if (it.get("author", "") in COMPETITOR_AUTHORS
                or extra.get("source_role") == "reference"
                or extra.get("direct_dispatch") is False):
            continue
        cands.append({"key": key, "video_id": vid,
                      "title": title[:60],
                      "video_url": url, "page_url": page,
                      "source": it.get("source", ""),
                      "author": it.get("author", ""),
                      "publish_time": it.get("publish_time") or "",
                      "extra": extra})
    # 同内容去重：标题相似度 > 60% 只保留一条，保留质量更好的
    # 同内容提前去重：微博同条内容被大量转发/重发，重复候选会污染排序。
    # 进池子前就按标题相似度去重（阈值 0.6），只留质量最好的 1 条。
    cands = dedup_by_title(cands)
    # 二次去重：标题前 12 字完全相同也视为同内容（转发时只改尾部的场景）
    seen_prefix = set()
    deduped = []
    for c in cands:
        pfx = (('cid', c['extra']['cid']) if c.get('extra', {}).get('cid')
               else (c["title"] or "")[:12])
        if pfx in seen_prefix:
            continue
        seen_prefix.add(pfx)
        deduped.append(c)
    cands = deduped
    # 过滤时长过短的（< 60 秒）和过长的（> 30 分钟）
    def _dur_ok(c):
        extra = c.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        dur = extra.get("duration", 0) or 0
        if isinstance(dur, str):
            try:
                dur = int(dur)
            except Exception:
                dur = 0
        # 2026-09-02 修正：上限原为 1800（30分钟），与常量 MAX_DUR=5400 不一致，
        # 导致「奖励完整原片」的打分被架空 —— 40~60 分钟的完整采访（如被 9 个号
        # 搬运的 59 分钟财联社直播）在打分前就被过滤掉了。
        return MIN_DUR <= dur <= MAX_DUR or dur == 0  # 0=未知交给下载后检查
    cands = [c for c in cands if _dur_ok(c)]

    def source_score(c):
        """来源权威性评分。注意：B站搜索很多是二创，不绝对优先。"""
        src = c.get("source", "")
        if src.startswith("xueqiu"):
            return 30   # 雪球：通常是一手访谈/股东大会
        if src.startswith("tencent"):
            return 28   # 腾讯新闻：官方媒体
        if src.startswith("bilibili_api") or src.startswith("bilibili_space"):
            return 25   # B站官方 API/空间：较可靠
        if src.startswith("weibo"):
            return 20   # 微博：可能一手，也可能片段
        if src.startswith("bilibili_search"):
            return 15   # B站搜索：二创可能性高
        if src.startswith("douyin"):
            return 12
        if src.startswith("netease"):
            return 10
        return 10

    def title_score(t):
        t = t.lower()
        score = 0
        # 高质量关键词：完整访谈/路演/直播回放/最新采访
        high = ["完整版", "完整", "访谈", "路演", "直播回放", "最新采访", "最新发声", "全集", "最新", "股东大会"]
        for k in high:
            if k in t:
                score += 10
                break
        # 有具体数字/观点
        if __import__('re').search(r"\d+", t):
            score += 8
        # 核心投资主题
        for k in ["投资", "医药", "消费", "茅台", "垄断", "AI", "风险", "回报"]:
            if k in t:
                score += 4
        # "林园"开头（本人视频）
        if t.startswith("林园"):
            score += 5
        # 二创/剪辑类减分
        low = ["精华版", "剪辑", "混剪", "恶搞", "鬼畜", "吐槽", "Reaction", " reaction"]
        for k in low:
            if k in t:
                score -= 15
        return score

    def freshness_score(c):
        """发布时间越新越好，但同一场内容不重复。"""
        try:
            from datetime import datetime
            pt = datetime.fromisoformat(c.get("publish_time", ""))
            age_hours = (__import__('time').time() - pt.timestamp()) / 3600
            if age_hours < 0:
                return 20
            if age_hours <= 24:
                return 15
            if age_hours <= 72:
                return 10
            if age_hours <= 168:
                return 5
            return 0
        except Exception:
            return 5

    def duration_score(c):
        """已知时长且合适的加分。"""
        extra = c.get("extra") or {}
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except Exception:
                extra = {}
        dur = extra.get("duration", 0) or 0
        if isinstance(dur, str):
            try:
                dur = int(dur)
            except Exception:
                dur = 0
        # 2026-09-01 修正：原规则给 120~600s 最高分，而那正是「短二创切片」的时长，
        # 直接导致一直抓二创。素材应该是「完整原片」，短片由我们自己拆条产出。
        # 依据：同期 B站林园内容实测，10~30 分钟完整版播放中位 1055（最高段），
        # 1~3 分钟切片中位 514（最低段）；而竞品高播放长视频都是 20~60 分钟完整采访。
        if dur >= 1800:            # 30 分钟以上：完整采访/直播回放，最优
            return 16
        if 900 <= dur < 1800:      # 15~30 分钟：较完整
            return 14
        if 600 <= dur < 900:       # 10~15 分钟
            return 11
        if 300 <= dur < 600:       # 5~10 分钟
            return 6
        if 120 <= dur < 300:       # 2~5 分钟：多半是二创切片
            return 2
        if 60 <= dur < 120:
            return 0
        return 0  # 未知或太短

    # 画面重包装的搬运号：素材上有大面积自制贴片，裁不掉（2026-09-01 逐帧看图确认）
    HEAVY_PACKAGING = {
        "投资就是滚雪球": "左上角常驻黄色大字标题 + 红字日期，左侧无裁切空间",
        "昕礽果复利增长": "左中部水印，位置在画面核心区，无法裁切",
        "股海淘沙": "竖版拼贴 + 上下黑边大字",
    }

    def packaging_score(c):
        a = (c.get("author") or "").strip()
        return -12 if a in HEAVY_PACKAGING else 0

    def quality_score(c):
        return (source_score(c) + title_score(c["title"]) +
                freshness_score(c) + duration_score(c) + packaging_score(c))

    # 按综合质量分降序
    cands.sort(key=lambda c: quality_score(c), reverse=True)
    return diversify_source_candidates(cands,n)


# ---------- 下载 ----------

_BILI_OPENER = None


def bili_opener():
    """带 buvid 指纹的会话。B站 CDN 对 curl/裸 UA 直接 403（FC 实测），
    必须走 fetch_bilibili.py 那套：首页 → finger/spi 拿 buvid3/4 → 同会话下载。"""
    global _BILI_OPENER
    if _BILI_OPENER is not None:
        return _BILI_OPENER
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0.0.0 Safari/537.36"),
                     ("Accept-Language", "zh-CN,zh;q=0.9"),
                     ("Referer", "https://www.bilibili.com/")]
    try:
        op.open("https://www.bilibili.com/", timeout=20).read()
        spi = json.loads(op.open(
            "https://api.bilibili.com/x/frontend/finger/spi", timeout=20).read().decode())
        for n, val in (("buvid3", spi["data"]["b_3"]), ("buvid4", spi["data"]["b_4"])):
            jar.set_cookie(http.cookiejar.Cookie(
                0, n, val, None, False, ".bilibili.com", True, False,
                "/", True, False, None, False, None, None, {}))
    except Exception as e:
        log.warning(f"buvid cookie 获取失败（可能触发 403/412）: {e}")
    _BILI_OPENER = op
    return op


def download(cand, dest, _retried=False):
    try:
        return _download_inner(cand, dest)
    except urllib.error.HTTPError as e:
        if e.code in (412, 403) and not _retried and not cand["video_url"]:
            # B站 WAF 是按会话/IP 时间窗挑战的，换新会话重来一次常能过
            global _BILI_OPENER
            _BILI_OPENER = None
            log.info(f"    {e.code}，换新会话重试一次")
            time.sleep(3)
            return download(cand, dest, _retried=True)
        raise


def weibo_refresh_url(page_url):
    """从微博页面 URL 重新获取视频直链（直链会过期）。"""
    import http.cookiejar, urllib.parse, re as _re
    mid = page_url.rstrip("/").split("/")[-1]
    log.info(f"    微博刷新直链: mid={mid}")
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")]
    # 访客票据
    fp = json.dumps({"os": "1", "browser": "Chrome120,0,0,0", "fonts": "undefined",
                     "screenInfo": "1920*1080*24", "plugins": ""})
    body = urllib.parse.urlencode({"cb": "gen_callback", "fp": fp}).encode()
    txt = op.open(urllib.request.Request(
        "https://passport.weibo.com/visitor/genvisitor",
        data=body, headers={"Content-Type": "application/x-www-form-urlencoded",
                           "Referer": "https://passport.weibo.com/visitor/visitor"}),
        timeout=25).read().decode("utf-8", "ignore")
    m = _re.search(r"\((\{.*\})\)", txt, _re.S)
    if not m:
        raise RuntimeError("微博 genvisitor 失败")
    tid = json.loads(m.group(1))["data"]["tid"]
    op.open(urllib.request.Request(
        f"https://passport.weibo.com/visitor/visitor?a=incarnate&t={urllib.parse.quote(tid)}"
        "&w=2&c=095&gc=&cb=cross_domain&from=weibo&_rand=0"), timeout=25).read()
    try:
        op.open("https://weibo.com/", timeout=20).read()
    except Exception:
        pass
    xsrf = next((c.value for c in jar if c.name == "XSRF-TOKEN"), "")
    # 搜索 API 找到这条微博
    data = json.loads(op.open(urllib.request.Request(
        f"https://weibo.com/ajax/statuses/show?id={mid}",
        headers={"Accept": "application/json", "X-XSRF-TOKEN": xsrf,
                 "Referer": f"https://weibo.com/"}), timeout=25).read().decode())
    media = (data.get("page_info") or {}).get("media_info") or {}
    vurl = (media.get("stream_url_hd") or media.get("stream_url")
            or media.get("mp4_hd_url") or media.get("mp4_sd_url") or "")
    if not vurl:
        raise RuntimeError("微博无视频或直链提取失败")
    log.info(f"    ✓ 新直链: {vurl[:60]}")
    return vurl


def yicai_refresh_url(page_url):
    """第一财经文章页重新换取带签名的 MP4 直链。"""
    import html
    req = urllib.request.Request(page_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
        "Referer": "https://www.yicai.com/",
    })
    page = urllib.request.urlopen(req, timeout=45).read().decode("utf-8", "ignore")
    match = re.search(r'https?://[^\s"\'<>]+?\.mp4[^\s"\'<>]*', page, re.I)
    if not match:
        raise RuntimeError("第一财经原文页未找到 MP4 直链")
    return html.unescape(match.group(0).replace("\\/", "/"))


def tencent_resolve_url(page_url):
    """腾讯新闻页面 URL → (视频真直链, 时长秒)。两段解析：
    getWebVideo 拿 vid → getinfo 换真直链。
    playurl 字段是假直链（跳转页），必须走 getinfo。
    时长用于下载前预检，短视频直接跳过。"""
    m = re.search(r"(\d{8}[A-Z]\w+)", page_url or "")
    if not m:
        raise RuntimeError(f"无法提取腾讯文章 ID: {page_url}")
    art_id = m.group(1)
    log.info(f"    腾讯解析: article={art_id}")
    req = urllib.request.Request(
        f"https://i.news.qq.com/getWebVideo?id={art_id}&appver=29_android_7.6.10",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore"))
    if data.get("ret") != 0:
        raise RuntimeError(f"getWebVideo 失败 ret={data.get('ret')}")
    video = ((data.get("video_channel") or {}).get("video") or {})
    vid = video.get("vid") or ""
    if not vid:
        raise RuntimeError("腾讯文章无视频")
    # 时长格式 "MM:SS" → 秒，用于下载前预检
    dur = 0
    dm = re.match(r"(\d+):(\d+)(?::(\d+))?", str(video.get("duration") or ""))
    if dm:
        parts = [int(x) for x in dm.groups() if x is not None]
        dur = (parts[0] * 60 + parts[1]) if len(parts) == 2 else (parts[0] * 3600 + parts[1] * 60 + parts[2])
    info_req = urllib.request.Request(
        f"https://vv.video.qq.com/getinfo?vids={vid}"
        "&platform=101001&charge=0&otype=json&defn=shd",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
                 "Referer": "https://v.qq.com/"})
    raw = urllib.request.urlopen(info_req, timeout=25).read().decode("utf-8", "ignore")
    if raw.startswith("<?xml"):
        em = re.search(r"<em>(\w+)</em>", raw)
        raise RuntimeError(f"getinfo 拒绝 vid={vid} code={em.group(1) if em else '?'}（视频可能已被限制播放）")
    info = json.loads(re.sub(r"^QZOutputJson=|;$", "", raw.strip()))
    videos = ((info.get("vl") or {}).get("vi")
              or (info.get("vl") or {}).get("vl") or [{}])
    item = videos[0]
    host = ((item.get("ul") or {}).get("ui") or [{}])[0].get("url", "")
    fn, vkey = item.get("fn", ""), item.get("fvkey", "")
    if not (host and fn and vkey):
        raise RuntimeError("getinfo 字段缺失")
    url = f"{host.rstrip('/')}/{fn}?vkey={vkey}"
    log.info(f"    ✓ 腾讯直链: {url[:70]}")
    return url, dur


def _curl_download(url, dest, referer, user_agent=None):
    """可续传的长视频下载；失败时保留 .part，下一次重试从断点继续。"""
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    cmd = [
        # FC's system curl predates --retry-all-errors. Keep the compatible
        # transient retry and resume options; unknown flags abort before I/O.
        "curl", "-fL", "--retry", "5",
        "--retry-delay", "2", "--connect-timeout", "30",
        "--max-time", "900", "--continue-at", "-",
        "-H", f"Referer: {referer}",
    ]
    if user_agent:
        cmd += ["-H", f"User-Agent: {user_agent}"]
    cmd += ["-o", str(part), url]
    subprocess.run(cmd, check=True, timeout=960)
    if not part.exists() or part.stat().st_size < 10240:
        raise RuntimeError("下载结果为空或异常小")
    part.replace(dest)


def _hls_download(url, dest, referer, user_agent):
    """让 ffmpeg 从 HLS 主播放表选择最高码率，并在瞬断后重连。"""
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part.mp4")
    part.unlink(missing_ok=True)
    headers = f"User-Agent: {user_agent}\r\nReferer: {referer}\r\n"
    try:
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-rw_timeout", "30000000", "-reconnect", "1",
            "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
            "-headers", headers, "-i", url, "-map", "0:v:0", "-map", "0:a?",
            "-c", "copy", "-movflags", "+faststart", str(part),
        ], check=True, timeout=960)
        if not part.exists() or part.stat().st_size < 10240:
            raise RuntimeError("HLS 下载结果为空或异常小")
        part.replace(dest)
    except Exception:
        part.unlink(missing_ok=True)
        raise


def _download_inner(cand, dest):
    if cand["video_url"] and "weibocdn" in cand["video_url"]:
        # 微博直链 → 带 Referer 下载
        log.info("    微博直链，带 Referer 下载")
        try:
            _curl_download(cand["video_url"], dest, "https://weibo.com/")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError):
            # 直链过期/403 → 刷新直链重试
            log.info("    直链下载失败，刷新微博直链")
            new_url = weibo_refresh_url(cand["page_url"])
            cand["video_url"] = new_url
            dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
            _curl_download(new_url, dest, "https://weibo.com/")
        if not dest.exists() or dest.stat().st_size < 10240:
            # 文件太小 → 可能是错误页面，刷新直链重试
            if cand["video_url"] and "weibocdn" in cand.get("video_url", ""):
                log.info("    文件异常，刷新微博直链")
                new_url = weibo_refresh_url(cand["page_url"])
                cand["video_url"] = new_url
                dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
                _curl_download(new_url, dest, "https://weibo.com/")
            if not dest.exists() or dest.stat().st_size < 10240:
                raise RuntimeError("微博直链下载失败")
    elif cand["video_url"]:
        # 其他直链 → 直接下载（带浏览器 UA + 防盗链 Referer）
        # （2026-08-29 修复：网易/好看/抖音 CDN 有防盗链，裸 curl 无 UA/Referer 会被 403）
        _ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
        _ref = "https://www.baidu.com/"
        _v = cand["video_url"]
        if "163.com" in _v or "netease" in str(cand.get("source", "")):
            _ref = "https://www.163.com/"
        elif "bdstatic" in _v or "haokan" in str(cand.get("source", "")):
            _ref = "https://haokan.baidu.com/"
        elif "snssdk" in _v or "douyin" in str(cand.get("source", "")):
            _ref = "https://www.douyin.com/"
        elif "yicai" in _v or "yicai" in str(cand.get("source", "")):
            _ref = "https://www.yicai.com/"
        # 网易记录同时保留 m3u8 和推导出来的 SD mobile MP4。优先让 ffmpeg
        # 从 HLS 主播放表选择最高码率；HLS 失效时再回退可续传的 MP4。
        _extra = cand.get("extra") or {}
        _hls = _extra.get("m3u8_url", "") if isinstance(_extra, dict) else ""
        if "netease" in str(cand.get("source", "")) and _hls:
            try:
                log.info("    网易 HLS 最高码率下载")
                _hls_download(_hls, dest, _ref, _ua)
            except Exception as exc:
                log.warning(f"    网易 HLS 失败，回退 MP4 续传：{exc}")
                _curl_download(cand["video_url"], dest, _ref, _ua)
        else:
            try:
                _curl_download(cand["video_url"], dest, _ref, _ua)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError):
                if "yicai" not in str(cand.get("source", "")):
                    raise
                log.info("    第一财经签名直链失效，从原文页刷新")
                cand["video_url"] = yicai_refresh_url(cand["page_url"])
                dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
                _curl_download(cand["video_url"], dest, _ref, _ua)
    else:                                                # B站：带指纹的会话走全程
        op = bili_opener()
        bvid = cand["video_id"]
        log.info(f"    view {bvid}")
        v = json.loads(op.open(
            f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
            timeout=30).read())
        cid = v["data"]["cid"]
        log.info("    playurl")
        p = json.loads(op.open(
            f"https://api.bilibili.com/x/player/playurl?bvid={bvid}&cid={cid}"
            "&qn=80&fnval=1&high_quality=1", timeout=30).read())
        durl = (p.get("data") or {}).get("durl") or []
        if not durl:
            raise RuntimeError("无可用流")
        log.info("    下载流")
        req = urllib.request.Request(
            durl[0]["url"],
            headers={"Referer": cand["page_url"] or "https://www.bilibili.com/"})
        with op.open(req, timeout=240) as r, open(dest, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
    dur = mp4_duration(dest)
    log.info(f"    文件大小: {dest.stat().st_size/1024:.0f} KB, 时长: {dur:.0f}s")
    if not (MIN_DUR <= dur <= MAX_DUR):
        raise RuntimeError(f"时长 {dur:.0f}s 不在 [{MIN_DUR},{MAX_DUR}]")
    return dur


# ---------- staging release 中转 ----------

def staging_release_id():
    try:
        return gh("GET", f"/releases/tags/{RELEASE_TAG}")["id"]
    except Exception:
        return gh("POST", "/releases", {
            "tag_name": RELEASE_TAG, "target_commitish": "main",
            "name": "Staging 素材中转", "prerelease": True})["id"]


def upload_asset(rel_id, path):
    name = Path(path).name
    assets = gh("GET", f"/releases/{rel_id}/assets?per_page=100")
    for a in (assets if isinstance(assets, list) else []):
        if a["name"] == name:
            gh("DELETE", f"/releases/assets/{a['id']}")
    up = f"https://uploads.github.com/repos/{REPO}/releases/{rel_id}/assets?name={name}"
    req = urllib.request.Request(up, data=Path(path).read_bytes(), method="POST",
                                 headers={"Authorization": f"Bearer {TOKEN}",
                                          "Content-Type": "video/mp4"})
    with urllib.request.urlopen(req, timeout=600) as r:
        assert json.loads(r.read())["state"] == "uploaded"
    return f"https://github.com/{REPO}/releases/download/{RELEASE_TAG}/{name}"


# ---------- FC 路由（一个函数挂两个定时触发器）----------

def handler(event, context):
    """FC 统一入口：按触发器名字路由。

    触发器名含 dispatch → 每日调度；其余 → 投稿。
    （定时触发器的 event 是 JSON：{"triggerName": "...", "triggerTime": "..."}）
    """
    try:
        evt = json.loads(event or "{}")
    except Exception:
        evt = {}
    name = str(evt.get("triggerName", ""))
    log.info(f"触发器: {name or '（手动测试）'}")
    log_event("run", f"触发器 {name or '手动'} 开始运行")
    # 入口事件立即落盘，长下载/上传即使超时也能证明请求实际进入函数。
    flush_logs()
    try:
        if name == "diagnose-production":
            import hashlib
            st = load_state()
            payload = json.loads(gh("GET", f"/contents/{DATA_JSON}?ref=main", raw=True).decode())
            items = payload if isinstance(payload, list) else payload.get("items", [])
            return {"ok": True, "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "daily_limit": MAX_PUBLISH_PER_DAY, "live_min_per_day": 3, "audio_max_per_day": 0,
                    "weekly_full_slot_beijing": {"weekday": 6, "hour": 21},
                    "presentation_versions": [1, 2], "quality_gate_version": QUALITY_GATE_VERSION,
                    "production_rules_version": PRODUCTION_RULES_VERSION,
                    "editorial_policy_version": editorial.VERSION, "minimum_final_seconds": editorial.MIN_SECONDS,
                    "editorial_code_sha256": hashlib.sha256(Path(editorial.__file__).read_bytes()).hexdigest(),
                    "dispatch_workflow_ref": "main",
                    "in_flight_placeholders": _pending_final_count(st),
                    "source_inventory": source_inventory(st),
                    "candidate_count": len(pick(items, st, MAX_ATTEMPTS)),
                    "daily_publish": st.get("daily_publish", {}), "publish_hours_beijing": sorted(PUBLISH_HOURS)}
        if name == "diagnose-ping":
            log_event("probe_ok", "FC 同步入口 ping 成功", "")
            return {"ok": True, "ts": int(time.time())}
        if name == "diagnose-fresh-six-publication":
            return fresh_six_publication_status()
        if name == 'repair-reviewed-media-0907':
            import media_repair
            import sys
            return run_with_lease('publish',lambda:media_repair.repair_known_media(evt,sys.modules[__name__]))
        if name == "publish-tv-wine-review-once":
            return publish_tv_wine_review_once(evt)
        if name == "diagnose-release":
            return diagnose_release_download(evt, context)
        if "dispatch" in name:
            return dispatch_handler(evt, context)
        return run_with_lease('publish',lambda: publish_catchup(evt,context)
                              if name=='publish-catchup' else publish_handler(evt,context))
    finally:
        flush_logs()


# ---------- Handler 1：每日调度 ----------

def dispatch_handler(event=None, context=None):
    """Serialize admission so timer/refill/deploy cannot dispatch in parallel."""
    return run_with_lease('dispatch',lambda:_dispatch_admitted(event,context))


def release_pipeline_lease(owner, kind, attempts=3):
    """Retry ref conflicts, but never release a lease that changed owners."""
    import base64
    for attempt in range(attempts):
        try:
            current=gh('GET',f'/contents/{DISPATCH_LEASE_KEY}?ref=main',timeout=15)
            lease=json.loads(base64.b64decode(current['content']))
            if lease.get('owner')!=owner:return False
            if not lease.get('expires_at'):return True
            gh('PUT',f'/contents/{DISPATCH_LEASE_KEY}',dict(
                message=f'chore(supply): release {kind} lease',sha=current['sha'],
                content=base64.b64encode(json.dumps(dict(owner=owner,expires_at=0)).encode()).decode()),timeout=15)
            return True
        except Exception as exc:
            log.warning('%s lease release attempt %s: %s',kind,attempt+1,type(exc).__name__)
            if attempt+1<attempts:time.sleep(attempt+1)
    log_event('lease_release_failed','调度锁释放失败，等待保守租约过期',kind)
    return False


def run_with_lease(kind, action):
    import base64
    import uuid
    from urllib.error import HTTPError
    owner = uuid.uuid4().hex
    now = int(time.time())
    # Dispatch and publishing share the state file. Serialize both writers so
    # an older dispatch snapshot cannot erase a just-written upload receipt.
    key=DISPATCH_LEASE_KEY
    busy={'dispatched':0,'admission_busy':1} if kind=='dispatch' else {'published':0,'publisher_busy':1}
    current = None
    try:
        current = gh('GET', f'/contents/{key}?ref=main', timeout=20)
        lease = json.loads(base64.b64decode(current['content']))
        if int(lease.get('expires_at') or 0) > now:
            return busy
    except HTTPError as exc:
        if exc.code != 404:
            raise
    payload = {'message': f'chore(supply): claim {kind} lease',
               'content': base64.b64encode(json.dumps(dict(owner=owner,expires_at=now+7200)).encode()).decode()}
    if current:
        payload['sha'] = current['sha']
    try:
        claim = gh('PUT', f'/contents/{key}', payload, timeout=30)
    except HTTPError as exc:
        if exc.code in (409,422):
            return busy
        raise
    try:
        return action()
    finally:
        release_pipeline_lease(owner,kind)


def catchup_deficit(st, now=None):
    now=time.time() if now is None else now
    if not is_regular_publish_hour(now) or slot_published(st, now):
        return 0
    local=time.gmtime(now+8*3600)
    today=time.strftime('%Y-%m-%d',local)
    daily=st.get('daily_publish') or {}
    count=int(daily.get('count') or 0) if daily.get('date')==today else 0
    due=sum(h<=local.tm_hour for h in PUBLISH_HOURS)
    return min(1,max(0,min(MAX_PUBLISH_PER_DAY,due)-count))


def publish_catchup(event, context=None):
    st=load_state()
    if not catchup_deficit(st):
        return {'published':0,'schedule_caught_up':1}
    stock=source_inventory(st)
    if not stock['inventory_fresh'] or stock.get('publishable_now',stock['daily_mix_usable'])<=0:
        return {'published':0,'verified_stock_empty':1}
    return publish_handler({**event,'force_publish':True,'batch_remaining':1},context)


def source_inventory(st, payload=None):
    """Count actual inspected MP4s separately from running workflow jobs."""
    if payload is None:
        try:
            payload=json.loads(gh('GET',f'/contents/{SOURCE_INVENTORY_KEY}?ref=main',raw=True,timeout=20))
        except Exception:
            payload={}
    valid=(payload.get('quality_gate_version')==QUALITY_GATE_VERSION
           and payload.get('editorial_policy_version')==editorial.VERSION
           and time.time()-float(payload.get('updated_at') or 0)<3*3600)
    latest={e['slug']:e for e in _latest_dispatches(st)}
    live=audio=0
    if valid:
        for record in payload.get('artifacts',[]):
            slug=record.get('slug'); e=latest.get(slug)
            if not e or e.get('failed') or slug in REVIEW_PAUSED_SLUGS:continue
            done=processed_part_indices(e)
            for part in record.get('parts',[]):
                if part.get('status')!='verified' or int(part.get('index',-1)) in done:continue
                if e.get('weekly_full_week') and part.get('content_type') != 'full_interview':continue
                if part.get('render_mode')=='audio_card':audio+=1
                else:live+=1
    today=time.strftime('%Y-%m-%d',time.gmtime(time.time()+8*3600))
    daily=st.get('daily_publish') or {}
    if daily.get('date')!=today:daily={}
    audio_now=bool(audio and not daily_mix_error(dict(render_mode='audio_card'),daily))
    publishable=max(0,min(live+int(audio_now),MAX_PUBLISH_PER_DAY-int(daily.get('count') or 0)))
    return dict(verified_live=live,verified_audio_card=audio,publishable_now=publishable,
                daily_mix_usable=live,target_reserve=TARGET_READY_RESERVE,
                inventory_fresh=valid)


def _dispatch_admitted(event=None, context=None):
    st = load_state()
    # Production/source gate failures happen in Actions after dispatch returns.
    # Consume their evidence before picking candidates so a terminally rejected
    # source cannot become retry-ready and get dispatched again.
    source_rejected = _collect_source_rejections(st)
    if source_rejected:
        save_state(st)
        log.info(f"已在调度前淘汰 {source_rejected} 条失败素材")
    # Person verification is CPU-local; an old external VLM 402 circuit no
    # longer blocks source production after this release.
    st.pop('quality_service_blocked_until',None)
    st.pop('quality_service_block_reason',None)
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time()+8*3600))
    if (today == FRESH_SIX_DATE and FRESH_SIX_APPROVED
            and int(((st.get("daily_publish") or {}).get(fresh_six_counter(next(iter(FRESH_SIX_APPROVED.values()))["slug"])) or {}).get("count", 0)) < 6):
        # The six accepted files are already in hand. Avoid background state
        # writers racing with their sequential receipt updates during release.
        return {"dispatched": 0, "fresh_six_publication_in_progress": 1}
    target = MAX_PER_DAY
    if isinstance(event, dict) and event.get("_refill_count"):
        target = max(1, min(MAX_PER_DAY, int(event["_refill_count"])))
    inventory=source_inventory(st)
    if inventory['daily_mix_usable']>=TARGET_READY_RESERVE:
        return {'dispatched':0,'reserve_full':1,**inventory}
    active=sum(len(gh('GET',f'/actions/workflows/{WF_PRODUCE}/runs?status={status}&per_page=100',timeout=30)
                       .get('workflow_runs',[])) for status in ('in_progress','queued'))
    if active>=MAX_ACTIVE_SOURCES:
        return {'dispatched':0,'active_sources':active,**inventory}
    target=min(target,MAX_ACTIVE_SOURCES-active,TARGET_READY_RESERVE-inventory['daily_mix_usable'])
    # Admission uses actual active runs, never historical pending placeholders.
    items_raw = gh("GET", f"/contents/{DATA_JSON}?ref=main", raw=True)
    j = json.loads(items_raw.decode())
    items = j if isinstance(j, list) else j.get("items", [])
    cands = pick(items, st, MAX_ATTEMPTS)
    log.info(f"候选 {len(cands)} 条，本轮目标成功 {target} 条")

    rel = staging_release_id()
    tmp = Path(tempfile.mkdtemp())
    success = 0
    # A new editorial rule must not leave already transcribed mothers stranded
    # until the next publication hour. Reuse their raw offline evidence first.
    try:
        reserve=json.loads(gh('GET',f'/contents/{SOURCE_INVENTORY_KEY}?ref=main',raw=True,timeout=20))
        if source_inventory(st,reserve)['inventory_fresh']:
            for entry,record in obsolete_review_candidates(st,reserve):
                if success>=target:break
                reason='当前母片需重新执行逐字原文审核，复用实际CPU原始转写'
                if _request_quality_reprocess(st,entry,entry['slug'],reason,record['artifact_id']):
                    success+=1
    except Exception as exc:
        log.warning('原始转写再利用队列暂不可读：%s',type(exc).__name__)
    # A unavailable checker is not evidence of a bad mother. Retry its exact
    # candidate with backoff, bounded by the same six-running-source limit.
    for entry in sorted(_latest_dispatches(st),key=lambda e:(not bool(e.get('reviewed_parts')),
                                                           float(e.get('source_check_retry_after') or 0))):
        if success>=target:break
        retry_at=entry.get('source_check_retry_after')
        if not retry_at or time.time()<float(retry_at) or entry.get('failed'):continue
        source=entry.get('asset_url') or entry.get('source_url')
        if not source:continue
        gh('POST',f'/actions/workflows/{WF_PRODUCE}/dispatches',{
            'ref':'main','inputs':{'source':source,'slug':entry['slug'],'speaker':'林园',
                'occasion':entry.get('title','')[:30],'auto_publish':'false',
                'include_full':'true' if entry.get('weekly_full_week') else 'false',
                **({'reviewed_parts':str(entry['reviewed_parts'])} if entry.get('reviewed_parts') else {}),
                'source_platform':platform_of(entry.get('source',''))}})
        entry['source_check_attempts']=int(entry.get('source_check_attempts') or 0)+1
        entry.pop('source_check_retry_after',None)
        entry['ts']=int(time.time())
        save_state(st)
        success+=1
    for i, c in enumerate(cands):
        if success >= target:
            log.info(f"已达到本轮目标 {target} 条，停止调度")
            break
        import hashlib
        c["slug"] = "ly-" + time.strftime("%m%d") + "-" + \
                    hashlib.md5(c["key"].encode()).hexdigest()[:6]
        log.info(f"[{i+1}/{len(cands)}] {c['title'][:40]}")
        try:
            if not c["video_url"] and "bilibili.com/video/" in c["page_url"]:
                # B站 API 在 CI 侧反而通（FC 的阿里云 IP 被 WAF 412 整体拉黑），
                # 页面 URL 直接传给 CI，取源阶段由 ci_fetch_bilibili.py 拉流。
                asset_url = c["page_url"]
                dur = 0
                log.info("    B站源 → 透传页面 URL 给 CI 下载")
            elif "news.qq.com" in (c["page_url"] or ""):
                # 腾讯新闻：yt-dlp 下不了 blob 页面，FC 直接解析真直链下载
                vurl, dur_hint = tencent_resolve_url(c["page_url"])
                if dur_hint and not (MIN_DUR <= dur_hint <= MAX_DUR):
                    raise RuntimeError(f"腾讯时长预检 {dur_hint:.0f}s 不在 [{MIN_DUR},{MAX_DUR}]")
                dest = tmp / f"{c['slug']}.mp4"
                c2 = dict(c)
                c2["video_url"] = vurl
                dur = download(c2, dest)
                asset_url = upload_asset(rel, dest)
            elif "weibo.c" in (c["page_url"] or ""):
                # 微博 → 透传页面 URL 给 CI（2026-09-02 实测对比，见 test-source-fetch）：
                #   FC 下直链：720p / 62MB / 2分19秒，且直链带 Expires 会过期；
                #   CI 用 yt-dlp 吃页面 URL：**1080p** / 128MB / 1分47秒，且页面 URL 不过期。
                # 画质更高、FC 不再占用 600s 超时预算、直链过期问题一并消失。
                asset_url = c["page_url"]
                dur = 0
                log.info(f"    微博源 → 透传页面 URL 给 CI（yt-dlp 取 1080p）: {c['page_url'][:60]}")
            elif c["video_url"]:
                # 其余有直链的 → FC 下载后上传到 staging release
                dest = tmp / f"{c['slug']}.mp4"
                dur = download(c, dest)
                asset_url = upload_asset(rel, dest)
            elif c["page_url"]:
                # 非B站但无直链（腾讯新闻/抖音等）→ 透传页面 URL 给 CI
                # CI 用 yt-dlp 下载，可能失败
                asset_url = c["page_url"]
                dur = 0
                log.info(f"    非B站源 → 透传页面 URL 给 CI 下载: {c['page_url'][:60]}")
            else:
                raise RuntimeError("无可用 URL")
            full_week = weekly_full_request(c, st)
            gh("POST", f"/actions/workflows/{WF_PRODUCE}/dispatches", {
                "ref": "main",
                "inputs": {"source": asset_url, "slug": c["slug"],
                           "include_full": "true" if full_week else "false",
                           "speaker": "林园", "occasion": c["title"][:30],
                           "delay_hours": "0", "auto_publish": "false",
                           "source_platform": platform_of(c.get("source", ""))}})
            st["dispatched"].append({"key": c["key"], "video_id": c["video_id"],
                                     "required_presentation_version": 2,
                                     "production_rules_version": PRODUCTION_RULES_VERSION,
                                     "weekly_full_week": full_week,
                                     "slug": c["slug"], "ts": int(time.time()),
                                     "source_url": c["page_url"] or c["video_url"],
                                     "asset_url": asset_url,
                                     "title": c["title"], "delay_hours": 0,
                                     "source": c.get("source", ""),
                                     "author": c.get("author", ""),
                                     "publish_time": c.get("publish_time", "")})
            save_state(st)
            success += 1
            log_event("dispatch_ok", f"已调度 {c['slug']}（{dur:.0f}s）", c["title"][:60])
            log.info(f"    ✓ 已调度 {c['slug']}（{dur:.0f}s）")
        except Exception as e:
            log_event("fail", f"调度失败 {c.get('slug', c['key'])}", str(e)[:150])
            _record_failure(st, c, e)

    _process_retries(st)
    return {"dispatched": success, "attempted": len(cands)}


def _record_failure(st, c, e):
    """记录调度失败，3 次后才真正 rejected。"""
    retry_list = st.setdefault("pending_retry", [])
    existing = next((x for x in retry_list if x.get("key") == c["key"]), None)
    if existing:
        existing["retries"] = existing.get("retries", 0) + 1
        existing["last_error"] = str(e)
        existing["ts"] = int(time.time())
        if existing["retries"] >= 3:
            st["rejected"].append({"key": c["key"], "video_id": c["video_id"],
                                   "ts": int(time.time()), "error": str(e)})
            st["pending_retry"] = [x for x in retry_list if x.get("key") != c["key"]]
            log.warning(f"    ✗ {c.get('slug', c['key'])} 失败 3 次，移入 rejected: {e}")
        else:
            log.warning(f"    ✗ {c.get('slug', c['key'])} 失败 {existing['retries']}/3: {e}")
    else:
        retry_list.append({
            "key": c["key"], "video_id": c["video_id"],
            "title": c["title"], "video_url": c.get("video_url"),
            "page_url": c.get("page_url"), "source": c.get("source"),
            "extra": c.get("extra"), "retries": 1,
            "last_error": str(e), "ts": int(time.time())
        })
        log.warning(f"    ✗ {c.get('slug', c['key'])} 失败 1/3: {e}，稍后重试")
    save_state(st)


def _process_retries(st):
    """把冷却完成的重试项重新加入候选队列（在下次 dispatch 时重试）。"""
    retry_list = st.get("pending_retry", [])
    if not retry_list:
        return
    now = time.time()
    ready = [x for x in retry_list if now - x.get("ts", 0) > 30 * 60]
    if not ready:
        return
    # 重试项不在这里直接调度，只是从 retry 移到候选可见状态
    # 实际重试会在下次 dispatch 时由 pick() 重新处理
    log.info(f"有 {len(ready)} 条失败项达到重试冷却时间")


OWNER_MID = os.environ.get("BILI_MID", "275211725")  # 园来滚雪球


def fresh_six_publication_status():
    """Read actual public archive state after exact-hash upload receipts."""
    st=load_state()
    opener=bili_opener()
    rows=[]
    for slug,info in st.get('published',{}).items():
        for part in info.get('parts',[]):
            sha=(part.get('fingerprints') or {}).get('sha256')
            if (sha not in FRESH_SIX_APPROVED or part.get('status')!='published'
                    or part.get('fresh_six_date')!=FRESH_SIX_DATE or not part.get('bvid')):
                continue
            row={'sha256':sha,'bvid':part['bvid'],'title':part.get('title'),'public':False}
            try:
                req=urllib.request.Request('https://api.bilibili.com/x/web-interface/view?bvid='+part['bvid'],
                    headers={'Referer':'https://www.bilibili.com/'})
                response=json.loads(opener.open(req,timeout=15).read())
                data=response.get('data') or {}
                row.update(api_code=response.get('code'),archive_state=data.get('state'),
                    duration=data.get('duration'),owner_mid=(data.get('owner') or {}).get('mid'))
                row['public']=(response.get('code')==0 and data.get('state')==0
                    and str((data.get('owner') or {}).get('mid'))==str(OWNER_MID)
                    and data.get('bvid')==part['bvid'])
            except Exception as exc:
                row['error']=str(exc)[:160]
            rows.append(row)
    return {'receipts':len(rows),'public_count':sum(r['public'] for r in rows),'videos':rows}


def bili_find_duplicate(title):
    """查自己 B站空间是否已传过同标题视频。
    2026-08-19 事故复盘：publish 上传成功但 save_state 失败 → 每小时重复上传，
    单日出现 5 个相同视频。这是最后防线：投稿前查重，已存在直接补记状态。
    2026-08-21 加强：模糊匹配——标题前 15 字相同即视为同一视频
    （同一素材重出片时 LLM 会生成不同标题，精确匹配拦不住）。"""
    if not title:
        return None
    try:
        import urllib.parse as _up
        url = (f"https://api.bilibili.com/x/series/recArchivesByKeywords"
               f"?mid={OWNER_MID}&keywords={_up.quote(title[:12])}&ps=10&pn=1")
        # 裸请求会被 B站 WAF 412，必须走带 buvid 指纹的会话
        op = bili_opener()
        req = urllib.request.Request(url, headers={
            "Referer": f"https://space.bilibili.com/{OWNER_MID}/video",
            "Accept": "application/json"})
        data = json.loads(op.open(req, timeout=20).read().decode("utf-8", "ignore"))
        for v in (data.get("data") or {}).get("archives") or []:
            vt = (v.get("title") or "").strip()
            # 精确匹配 或 前 15 字相同（同一素材重出的不同 LLM 标题）
            if vt == title.strip() or (len(vt) >= 15 and len(title.strip()) >= 15
                                       and vt[:15] == title.strip()[:15]):
                return v.get("bvid")
    except Exception as e:
        log.warning(f"B站查重失败（不阻断，但无法防重复）: {e}")
    return None


# ---------- Handler 2：投稿 ----------

def processed_part_indices(entry):
    """Keep old contiguous progress while supporting independently handled parts."""
    return (set(range(max(0,int(entry.get('published_parts') or 0))))
            | {int(i) for i in entry.get('processed_part_indices',[]) if int(i)>=0})


def mark_part_processed(entry, index):
    done=processed_part_indices(entry)
    done.add(int(index))
    prefix=int(entry.get('published_parts') or 0)
    while prefix in done:
        prefix+=1
    entry['published_parts']=prefix
    entry['processed_part_indices']=sorted(i for i in done if i>=prefix)


def inventory_part_index(entry, artifact_id, records, daily):
    """Pick an inspected usable part without letting an early audio card block live ones.

    None means a known verified reserve is temporarily blocked by today's mix;
    absent inspection falls back to the normal full checks at the current index.
    """
    for record in records:
        if record.get('slug')!=entry['slug'] or record.get('artifact_id')!=artifact_id:
            continue
        ready=sorted((p for p in record.get('parts',[])
                      if p.get('status')=='verified'
                      and int(p['index']) not in processed_part_indices(entry)),
                     key=lambda p:int(p['index']))
        if ready:
            return next((int(p['index']) for p in ready if not daily_mix_error(p,daily)),None)
    return int(entry.get('published_parts') or 0)


def obsolete_review_candidates(state, inventory):
    """Only reprocess obsolete reviews, never overwrite usable inspected parts."""
    latest={e['slug']:e for e in _latest_dispatches(state)}
    for record in inventory.get('artifacts',[]):
        entry=latest.get(record.get('slug'))
        if (not entry or entry.get('failed') or entry['slug'] in REVIEW_PAUSED_SLUGS
                or int(entry.get('quality_retries') or 0)>=2
                or entry.get('quality_reprocess_artifact_id')==record.get('artifact_id')):
            continue
        remaining=[p for p in record.get('parts',[])
                   if int(p['index']) not in processed_part_indices(entry)]
        if (remaining and all(p.get('status')=='rejected' for p in remaining)
                and any('CPU Qwen 成片尚未通过逐字开场/结尾与识别疑点复核' in str(p.get('reason',''))
                        for p in remaining)):
            yield entry,record


def _has_unpublished_part(e, st):
    """判断 dispatched 条目是否还有未投的 part（长视频多条分次投稿）。
    单条/旧记录（无 parts_total 或 parts_total<=1）已投完就不算 pending。"""
    pub = st.get("published", {}).get(e["slug"])
    if not pub:
        total = e.get("parts_total", 0)
        return not total or e.get("published_parts", 0) < total
    parts_total = pub.get("parts_total", 1)
    # 本次已授权 V4 批次曾被旧同源规则误记为已发布，但实际新批次进度仍为 0。
    # 保留历史记录本身，只把该精确状态视为“仍待投”，不删除任何 BV 证据。
    if (e.get("slug") == "ly-parity-v3-14-0905"
            and int(e.get("published_parts") or 0) == 0
            and pub.get("note") == "同源素材已发布过，防重入拦截"):
        return True
    if parts_total <= 1:
        return False  # 单条/旧记录已投完，不再 pending
    published_parts = e.get("published_parts", 0)
    return published_parts < parts_total


def _record_skipped_part(st, e, slug, part, parts_total, index, title, reason):
    """重复 part 视为已处理，避免每小时反复尝试同一个文件。"""
    e["parts_total"] = parts_total
    mark_part_processed(e,index)
    prev = dict((st.get("published") or {}).get(slug) or {})
    parts_log = list(prev.get("parts") or [])
    parts_log.append({
        "status": "skipped", "title": title, "ts": int(time.time()),
        "part_index": index,
        "reason": reason, "fingerprints": part.get("fingerprints") or {},
    })
    prev.update({
        "parts": parts_log, "parts_total": parts_total,
        "title": prev.get("title") or title,
        "source_url": prev.get("source_url") or e.get("source_url", ""),
        "source_platform": (prev.get("source_platform")
                            or part.get("source_platform")
                            or platform_of(e.get("source", ""))),
    })
    st.setdefault("published", {})[slug] = prev
    save_state(st)


def presentation_quality_error(meta):
    """Validate the selected layout against real encoded dimensions, not portrait constants."""
    layout = meta.get("layout_proof") or {}
    resolution = meta.get("resolution") or {}
    canvas = layout.get("canvas") or {}
    mode = layout.get("mode")
    try:
        w, h = int(canvas["width"]), int(canvas["height"])
        region = layout["subtitle_region"]
        x,y,rw,rh = (int(region[k]) for k in ("x","y","width","height"))
        font = int(layout["subtitle_font_px"])
    except (KeyError, TypeError, ValueError):
        return "多版式尺寸证明缺失"
    if (w,h) != (resolution.get("width"),resolution.get("height")):
        return "版式与实际成片尺寸不一致"
    if not (0<=x and 0<=y and rw>0 and rh>0 and x+rw<=w and y+rh<=h):
        return "字幕安全区域越界"
    if mode not in {"landscape","portrait","square","audio_card"}:
        return "未知版式"
    if mode == "landscape" and w <= h*1.15:
        return "横版尺寸不符"
    if mode == "portrait" and h <= w*1.15:
        return "竖版尺寸不符"
    if mode == "square" and not (w<=h*1.15 and h<=w*1.15):
        return "方版尺寸不符"
    if mode == "audio_card" and ((w,h)!=(720,1280) or meta.get("render_mode") not in {"audio_card", "live_video_card"}):
        return "人物资料卡模式不符"
    if (layout.get("subtitle_max_lines")!=2 or layout.get("subtitle_vertical_alignment")!="center"
            or layout.get("subtitle_layout_version",0)<3 or not 28<=font<=min(w,h)*.10
            or rh < 2*font
            or layout.get("word_boundary_policy")!="semantic-v1"
            or meta.get("subtitle_word_boundaries_verified") is not True):
        return "缺少完整词句字幕证明"
    checks=meta.get("render_checks") or {}
    if (checks.get("frames_checked",0)<12 or checks.get("dimensions_match") is not True
            or checks.get("qr_detected") is not False):
        return "缺少实际多版式抽帧证明"
    cover=meta.get("cover_proof") or {}
    if (cover.get("font_px",0)<96 or cover.get("thumbnail_font_px",0)<12
            or not 1<=len(cover.get("headline_lines") or [])<=2
            or cover.get("no_overflow") is not True or not cover.get("thumbnail")):
        return "封面未通过列表缩略图大字门禁"
    return None


def daily_mix_error(meta, daily):
    """Three daily releases retain the >=70% live rule, so all three are live.

    Unknown historical modes do not count as verified live footage.
    """
    if meta.get("render_mode") in {"live_video_card", "direct", "delogo", "crop", "crop_delogo"}:
        return None
    if meta.get("render_mode") != "audio_card":
        return "内容形态不明，不能计入真人动态配额"
    audio = int(daily.get("audio_card_count") or 0) + 1
    live = int(daily.get("live_video_count") or 0)
    if audio > int(MAX_PUBLISH_PER_DAY * 0.30) or audio * 10 > (audio + live) * 3:
        return "音频卡额度暂不可用，三条日上限下保持真人动态比例，继续选择真人片"
    return None


def artifact_quality_error(meta):
    """校验成片携带的新质量证明；旧 artifact 默认不可信，必须重做。"""
    if not isinstance(meta, dict):
        return "meta.json 不是对象"
    try:
        version = int(meta.get("quality_gate_version", 0))
    except (TypeError, ValueError):
        version = 0
    if version < QUALITY_GATE_VERSION:
        return f"旧成片缺少质量闸门 v{QUALITY_GATE_VERSION} 证明"
    if meta.get('asr_model') == 'sensevoice':
        return 'SenseVoice 在多场林园母片出现影响理解的识别错误，须用新版 CPU 离线识别重做并复核'
    if (meta.get('asr_model')=='qwen3'
            and not editorial.model_review_skipped(meta.get('editorial_review'))
            and (meta.get('editorial_review') or {}).get('review_protocol') not in {2,3}):
        return 'CPU Qwen 成片尚未通过逐字开场/结尾与识别疑点复核，不能仅凭旧摘要放行'
    editorial_error = editorial.metadata_error(meta)
    if editorial_error:
        return editorial_error
    if meta.get("speaker") != "林园":
        return "成片人物字段不是林园"
    if int(meta.get("visual_standard_version") or 0) < VISUAL_STANDARD_VERSION:
        return "成片没有应用 v3 对标视觉标准"
    if int(meta.get("cover_standard_version") or 0) < COVER_STANDARD_VERSION:
        return "封面没有应用 v4 真人图强制标准"
    if meta.get("cover_person_image_verified") is not True:
        return "封面没有已核验真人图证明"
    if meta.get("cover_person_image_source") not in {
            "authority_reference", "verified_source_frame"}:
        return "封面人物图来源不可验证"
    if meta.get("title_quality_verified") is not True:
        return "标题没有通过原话/重复/ASR 污染质检"
    if any(word in (meta.get("title") or "") for word in TITLE_ASR_BLACKLIST):
        return "标题仍含 ASR 污染词"
    if meta.get("review_assets_verified") is not True:
        return "缺少30秒预览和6帧接触表质检证明"
    if not meta.get("preview_30s") or not meta.get("contact_sheet_6"):
        return "预发布质检产物记录不完整"

    layout = meta.get("layout_proof") or {}
    if meta.get("presentation_version") in (1, 2):
        error = presentation_quality_error(meta)
        if error:
            return error
    elif meta.get("presentation_version") not in (None, 0):
        return "不支持的版式版本"
    elif (layout.get("live_region") != {"x": 44, "y": 360,
                                      "width": 632, "height": 470}
            or layout.get("subtitle_region") != {
                "x": 38, "y": 874, "width": 644, "height": 166}
            or int(layout.get("subtitle_max_lines") or 0) != 2
            or int(layout.get("subtitle_layout_version") or 0) < 2
            or layout.get("subtitle_vertical_alignment") != "center"
            or not 38 <= int(layout.get("subtitle_font_px") or 0) <= 42):
        return "v3 画面/字幕固定版式证明不完整"

    visual = meta.get("visual_identity") or {}
    same = {x for x in (visual.get("same_person_frames") or [])
            if isinstance(x, int) and x > 0}
    try:
        confidence = float(visual.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0
    if visual.get("speaker") != "林园" or len(same) < 2 or confidence < 0.75:
        return "人物多帧核验记录不完整或未通过"

    resolution = meta.get("resolution") or {}
    try:
        short_edge = int(resolution.get("short_edge", 0))
    except (TypeError, ValueError):
        short_edge = 0
    if short_edge < MIN_SHORT_EDGE:
        return f"裁切后成片短边 {short_edge} < {MIN_SHORT_EDGE}"
    if meta.get("watermark_verified") is not True:
        return "成片没有通过外部角标复检"
    if meta.get("live_region_verified") is not True:
        return "真人动态区没有通过逐帧复检"
    if meta.get("no_qr_verified") is not True:
        return "成片没有通过二维码复检"
    if meta.get("render_mode") == "live_video_card" and meta.get("partial_qr_verified") is not True:
        return "真人窗口缺少残缺二维码复检，旧漏检成片不得投稿"
    if meta.get("render_mode") == "live_video_card" and int(meta.get("full_face_frames") or 0) < 5:
        return "真人窗口缺少完整人脸取景复检，旧裁头成片必须重做"
    identity_error=final_live_identity_error(meta)
    if identity_error:
        return identity_error
    if meta.get("subtitle_semantic_groups_verified") is not True:
        return "缺少完整意群字幕复检，旧碎句成片必须重做"
    if (not meta.get("subtitle_files")
            or not re.fullmatch(r"[a-f0-9]{64}", str(meta.get("subtitle_text_sha256") or ""))):
        return "缺少真实ASS字幕文本指纹，不能只信完整意群布尔字段"
    if meta.get("no_black_bars_verified") is not True:
        return "成片没有通过黑边/取景复检"
    if meta.get("brand_watermark_applied") is not True:
        return "成片没有叠加园来滚雪球品牌水印"
    if meta.get("has_existing_subtitles") is not False:
        return "源素材含内嵌字幕或缺少无源字幕证明"
    if meta.get("subtitles_burned") is not True:
        return "成片没有烧录统一字幕"
    if meta.get("clean_strategy") not in {
            "direct", "delogo", "crop", "crop_delogo", "audio_card"}:
        return "成片缺少可复现的干净画面策略"
    if (meta.get("clean_strategy") == "audio_card"
            and meta.get("render_mode") not in {"live_video_card", "audio_card"}):
        return "音频卡渲染模式不可验证"
    if meta.get("clean_filter_verified") is not True:
        return "成片清理方案未经复检"

    fp = meta.get("fingerprints") or {}
    if (not fp.get("sha256") or len(fp.get("video_dhash") or []) < 4
            or len(fp.get("audio_chromaprint") or []) < 4
            or not ((fp.get("transcript_ngrams") or [])
                    or (fp.get("transcript_simhash") or []))):
        return "成片内容指纹不完整"
    return None


def artifact_subtitle_error(meta, delivery_dir):
    """Inspect the actual ASS files carried beside the MP4 before upload."""
    try:
        text = editorial.subtitle_files_text(delivery_dir, meta.get("subtitle_files"))
    except (OSError, UnicodeError, ValueError) as exc:
        return f"真实ASS字幕读取失败：{exc}"
    if not text:
        return "真实ASS字幕为空"
    if editorial.text_digest(text) != meta.get("subtitle_text_sha256"):
        return "真实ASS字幕与meta文本指纹不一致"
    return editorial.transcript_integrity_error(text)


def _collect_source_rejections(st):
    """读取失败工作流的素材质检报告，立即淘汰，避免无成片干等 12 小时。"""
    prefixes = ("source-reject-", "production-reject-")
    rejected = 0
    runs = gh("GET", f"/actions/workflows/{WF_PRODUCE}/runs"
                     "?status=completed&per_page=30").get("workflow_runs", [])
    by_slug = {}
    for entry in st.get("dispatched", []):
        slug = entry.get("slug")
        if not slug:
            continue
        current = by_slug.get(slug)
        if current is None or float(entry.get("ts") or 0) >= float(current.get("ts") or 0):
            by_slug[slug] = entry
    for run in runs:
        artifacts = gh("GET", f"/actions/runs/{run['id']}/artifacts").get(
            "artifacts", [])
        for artifact in artifacts:
            name = artifact.get("name", "")
            prefix = next((p for p in prefixes if name.startswith(p)), None)
            if not prefix or artifact.get("expired"):
                continue
            slug = name[len(prefix):]
            candidate = by_slug.get(slug)
            if not candidate or candidate.get("failed"):
                continue
            if candidate.get('source_check_report_id')==artifact['id']:
                continue
            # 同一 slug 可能曾有失败重跑。只处理候选创建之后产生的拒绝报告；
            # 更早的 source-reject artifact 属于历史运行，不能污染新成片批次。
            artifact_created = str(artifact.get("created_at") or "")
            candidate_created = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime(int(candidate.get("ts") or 0)))
            if artifact_created and artifact_created <= candidate_created:
                continue
            reason = "素材质量门禁未通过"
            try:
                # Obtain the signed URL separately. The GitHub credential must
                # not follow the redirect to the blob host (which rejects it).
                with tempfile.TemporaryDirectory() as report_dir:
                    report_zip=Path(report_dir)/'report.zip'
                    download_reviewed_zip(artifact['id'],report_zip,attempts=1,
                                          timeout_sec=30,max_bytes=2_000_000)
                    payload=report_zip.read_bytes()
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    report_name = next(
                        n for n in archive.namelist()
                        if n.endswith(("source_quality.json", "batch_report.json")))
                    report = json.loads(archive.read(report_name).decode("utf-8"))
                if prefix == "production-reject-" and report.get("accepted", 0):
                    continue  # 有合格片的严格批次交给其显式验收流程，不淘汰全源
                failures = report.get("rejected") or []
                if (prefix == 'production-reject-' and not report.get('accepted')
                        and not failures and not report.get('reason')
                        and report.get('selection_completed') is not True):
                    # Old selectors swallowed inference timeouts and wrote an
                    # empty report. That report is not a quality verdict.
                    report['retryable'] = True
                    report['reason'] = '旧版空选段报告没有完成审核的证据，保留CPU转写后有界重试'
                reason = report.get("reason") or (failures[0].get("reason") if failures else None) or reason
            except Exception as exc:
                log.warning(f"{slug} 素材拒绝报告读取失败: {exc}")
                # Do not fabricate a source verdict from an unavailable ZIP.
                continue
            if report.get('retryable') is True or reason.startswith(('人物 VLM 校验不可用','素材质检不可用')):
                candidate['source_check_report_id']=artifact['id']
                candidate['last_error']=reason
                candidate['failure_stage']='quality-service'
                attempts=int(candidate.get('source_check_attempts') or 0)
                # 402 means the configured visual service cannot accept more
                # work. Retrying other mothers only burns Actions minutes and
                # produces the same non-verdict, so open one shared circuit.
                if '402' in reason or 'Payment Required' in reason:
                    candidate['source_check_exhausted']=True
                    candidate.pop('source_check_retry_after',None)
                    st['quality_service_blocked_until']=int(time.time())+6*3600
                    st['quality_service_block_reason']=reason
                elif attempts<2:
                    candidate['source_check_retry_after']=int(time.time())+900*(attempts+1)
                else:
                    candidate['failed']=True
                    candidate['source_check_exhausted']=True
                save_state(st)
                log_event('quality',f'{slug} 质检服务未完成，保留原素材',reason[:150])
                continue
            candidate["failed"] = True
            candidate["last_error"] = reason
            candidate["source_quality_rejected"] = True
            candidate['failure_stage']='editorial-or-render' if prefix=='production-reject-' else 'source-quality'
            # A deterministic slug may have several dispatch rows after prior
            # retries.  The artifact rejects the source/slug, not merely the
            # newest row, so close every duplicate row as well.
            for row in st.get("dispatched", []):
                if row.get("slug") == slug:
                    row["failed"] = True
                    row["last_error"] = reason
                    row["source_quality_rejected"] = True
            # A gate rejection is terminal for this exact source.  Leaving the
            # old retry record behind would make pick() subtract the rejected
            # key from `done` after its cooldown and dispatch it again.
            candidate_key = candidate.get("key")
            candidate_source = (candidate.get("source_url") or "").strip()
            st["pending_retry"] = [
                row for row in st.get("pending_retry", [])
                if row.get("key") != candidate_key
                and (row.get("page_url") or row.get("video_url") or "").strip()
                != candidate_source
            ]
            if not any(x.get("slug") == slug for x in st.setdefault("rejected", [])):
                st["rejected"].append({
                    "slug": slug, "key": candidate.get("key"),
                    "video_id": candidate.get("video_id"),
                    "ts": int(time.time()), "error": reason,
                })
            log_event("fail", f"素材淘汰 {slug}", reason[:150])
            log.info(f"✗ 素材门禁淘汰 {slug}: {reason}")
            rejected += 1
            try:
                gh("DELETE", f"/actions/artifacts/{artifact['id']}")
            except Exception as exc:
                log.warning(f"删除素材拒绝 artifact 失败: {exc}")
    return rejected


def _request_quality_reprocess(st, e, slug, reason, artifact_id=None):
    """隔离旧 artifact，并用原素材触发新版流水线重新生成。"""
    active=sum(len(gh('GET',f'/actions/workflows/{WF_PRODUCE}/runs?status={status}&per_page=100',timeout=30)
                       .get('workflow_runs',[])) for status in ('in_progress','queued'))
    if active>=MAX_ACTIVE_SOURCES:
        e['quality_failure']=reason
        save_state(st)
        log_event('quality',f'{slug} 等待 CPU 重识别空位',reason)
        return False
    if artifact_id:
        try:
            gh("DELETE", f"/actions/artifacts/{artifact_id}")
            log.info(f"✓ 已隔离旧 artifact: deliver-{slug}")
        except Exception as exc:
            log.warning(f"隔离旧 artifact 失败（仍不会放行）: {exc}")

    source = e.get("asset_url") or e.get("source_url")
    retries = e.get("quality_retries", 0)
    if not source or retries >= 2:
        e["failed"] = True
        e["quality_failure"] = reason
        save_state(st)
        log_event("fail", f"⛔ {slug} 旧成片无法安全重做", reason)
        return False
    try:
        gh("POST", f"/actions/workflows/{WF_PRODUCE}/dispatches", {
            "ref": "main",
            "inputs": {"source": source, "slug": slug,
                       "speaker": "林园", "occasion": e.get("title", "")[:30],
                       "delay_hours": "0", "auto_publish": "false",
                       **({'reviewed_parts':str(e['reviewed_parts'])} if e.get('reviewed_parts') else {}),
                       # 固定 14 条验收批次必须保持 13 条切片 + 1 条完整版；
                       # 否则常规模式允许空片段，会出现“运行成功但仅产出 3 条”。
                       **({"include_full": "true"} if e.get("weekly_full_week") else {}),
                       **({"target_parts": "13", "include_full": "true"}
                          if slug == "ly-parity-v3-14-0905" else {})}})
    except Exception as exc:
        e["quality_failure"] = f"{reason}；重做触发失败：{exc}"
        save_state(st)
        log_event("fail", f"⛔ {slug} 旧成片重做触发失败", str(exc)[:120])
        return False
    e["quality_retries"] = retries + 1
    e['quality_reprocess_artifact_id']=artifact_id
    e["last_retry"] = int(time.time())
    e["reprocessing_quality"] = True
    e["production_rules_version"] = PRODUCTION_RULES_VERSION
    e["ts"] = int(time.time())
    e["quality_failure"] = reason
    save_state(st)
    log_event("quality", f"♻️ {slug} 旧成片已隔离并重新出片", reason)
    return True


def _continue_after_rejection(event, context, slug, result, cleanup_dir=None):
    """本候选被拦后在同一时段换下一条，仍保证最多实际上传一条。"""
    if cleanup_dir:
        shutil.rmtree(cleanup_dir, ignore_errors=True)
    attempted = set((event or {}).get("_attempted_slugs") or []) \
        if isinstance(event, dict) else set()
    attempted.add(slug)
    if len(attempted) >= REJECT_REFILL_LIMIT:
        return result
    next_event = dict(event or {}) if isinstance(event, dict) else {}
    next_event["_attempted_slugs"] = sorted(attempted)
    follow = publish_handler(next_event, context)
    merged = dict(result)
    for key, value in (follow or {}).items():
        if isinstance(value, (int, float)):
            merged[key] = merged.get(key, 0) + value
        else:
            merged[key] = value
    return merged


def _latest_dispatches(st):
    """Return one current state row per deterministic production slug."""
    latest_by_slug = {}
    for entry in st.get("dispatched", []):
        slug = entry.get("slug")
        if not slug:
            continue
        current = latest_by_slug.get(slug)
        if current is None or float(entry.get("ts") or 0) >= float(current.get("ts") or 0):
            latest_by_slug[slug] = entry
    return list(latest_by_slug.values())


def _pending_final_count(st):
    """待投成片总数（所有素材剩余未投 part 之和），调度端用它防积压。"""
    total = 0
    # Retries keep historical dispatch rows for auditability.  Inventory is a
    # current-state metric, so count only the newest row of each slug.
    for e in _latest_dispatches(st):
        if not (e.get("slug") and not e.get("failed")):
            continue
        if e.get("production_rules_version") != PRODUCTION_RULES_VERSION:
            continue  # Obsolete unverified inventory cannot block fresh production.
        pub = st.get("published", {}).get(e["slug"])
        if pub:
            parts_total = pub.get("parts_total", 1)
            if parts_total <= 1:
                continue  # 单条已投完
            total += len(set(range(parts_total))-processed_part_indices(e))
        elif time.time() - float(e.get("ts") or 0) < 6 * 3600:
            total += 1  # 只把六小时内在制任务计入库存；老失败占位不能阻塞补量
    return total


def publish_handler(event=None, context=None):
    event = event if isinstance(event, dict) else {}
    batch_slug = str(event.get("batch_slug") or "").strip()
    try:
        requested_artifact_id = int(event.get("artifact_id") or 0)
    except (TypeError, ValueError):
        requested_artifact_id = 0
    explicit_v4_batch = batch_slug == "ly-parity-v3-14-0905"
    force_publish = bool(event.get("force_publish"))
    if batch_slug in REVIEW_PAUSED_SLUGS:
        log.warning(f"{batch_slug} 等待新版真实样片验收，投稿已熔断")
        return {"published": 0, "review_paused": 1, "slug": batch_slug}
    if not batch_slug and not force_publish and not is_regular_publish_hour():
        hour = time.gmtime(time.time() + 8 * 3600).tm_hour
        log.info(f"北京时间 {hour:02d} 时不在普通投稿窗口，跳过")
        return {"published": 0, "outside_publish_window": 1}
    st = load_state()
    if not batch_slug and slot_published(st):
        return {'published': 0, 'slot_already_published': 1}
    if batch_slug and not any(e.get("slug") == batch_slug
                              for e in st.get("dispatched", [])):
        st.setdefault("dispatched", []).append({
            "slug": batch_slug,
            "title": str(event.get("title") or "林园完整访谈对标批次"),
            "source_url": str(event.get("source_url") or ""),
            "asset_url": str(event.get("source_url") or ""),
            "source": str(event.get("source_url") or ""),
            "ts": int(time.time()),
            "published_parts": 0,
            "production_rules_version": PRODUCTION_RULES_VERSION,
            "required_presentation_version": 2,
        })
        save_state(st)
    now = time.time()
    # 指定的已验收批次已有独立 14 条门禁证据，无需每个 part 重扫
    # 最近 30 次出片运行；普通自动队列仍保留素材淘汰扫描。
    reviewed_batch = any(row.get("slug") == batch_slug
                         for row in FRESH_SIX_APPROVED.values())
    source_rejected = (0 if explicit_v4_batch or reviewed_batch
                       else _collect_source_rejections(st))
    if source_rejected:
        save_state(st)
        try:
            dispatch_handler({"_refill_count": source_rejected}, context)
            st = load_state()
            log.info(f"已为 {source_rejected} 条不合格素材补调候选")
        except Exception as exc:
            log.warning(f"素材淘汰后的自动补位失败: {exc}")
    # 每天投片上限：长视频拆多条排队分天发，每天最多投 MAX_PUBLISH_PER_DAY 条成片
    today = time.strftime("%Y-%m-%d", time.gmtime(now + 8 * 3600))  # 北京时间
    dp = st.get("daily_publish") or {}
    if dp.get("date") != today:
        dp = {"date": today, "count": 0}
        st["daily_publish"] = dp
    fresh_budget = fresh_six_budget(dp, batch_slug, today)
    budget = fresh_budget if fresh_budget is not None else dp
    if budget.get("count", 0) >= MAX_PUBLISH_PER_DAY:
        log.info(f"本配额已投 {budget['count']} 条，达上限 {MAX_PUBLISH_PER_DAY}")
        save_state(st)
        return {"published": 0}
    attempted = set((event or {}).get("_attempted_slugs") or []) \
        if isinstance(event, dict) else set()
    pending = [e for e in _latest_dispatches(st)
               if e.get("slug") and not e.get("failed")
               and e.get("slug") not in REVIEW_PAUSED_SLUGS
               and e.get("slug") not in attempted
               and (not batch_slug or e.get("slug") == batch_slug)
               and _has_unpublished_part(e, st)]
    # 轮转：已投条数最少的素材优先（防长视频霸占额度、新素材饿死 2026-08-27）
    pending.sort(key=lambda e: (e.get("production_rules_version") != PRODUCTION_RULES_VERSION,
                                e.get("published_parts", 0)))
    if not batch_slug:
        local = time.gmtime(now + 8 * 3600)
        if (local.tm_wday, local.tm_hour) == WEEKLY_FULL_SLOT:
            pending.sort(key=lambda e: not bool(e.get('weekly_full_week')))
        else:
            pending = [e for e in pending if not e.get('weekly_full_week')]
    if not pending:
        log.info("无待投稿件")
        return {"published": 0}

    arts = {}
    art_ids = {}
    reserve_records = []
    if batch_slug:
        # 用户验收后可绑定精确 Artifact。Release 成品库长期累积后可能达到
        # asset 数量上限；此时不能让可发布视频因为可选镜像失败而丢失。
        if requested_artifact_id > 0:
            artifact = gh("GET", f"/actions/artifacts/{requested_artifact_id}")
            if (artifact.get("name") != f"deliver-{batch_slug}"
                    or artifact.get("expired")):
                log.error(f"✗ {batch_slug} 指定 Artifact 与 slug 不匹配或已过期")
                return {"published": 0, "artifact_mismatch": 1}
            arts[batch_slug] = artifact.get("archive_download_url")
            art_ids[batch_slug] = requested_artifact_id
        else:
            # 指定批次优先从 deliver Release 逐条取件，不重扫最近 30 次运行。
            arts[batch_slug] = None
    else:
        runs = gh("GET", f"/actions/workflows/{WF_PRODUCE}/runs"
                         "?status=completed&per_page=30").get("workflow_runs", [])
        for run in runs:
            for a in gh("GET", f"/actions/runs/{run['id']}/artifacts").get("artifacts", []):
                if a["name"].startswith("deliver-") and not a.get("expired"):
                    slug_key = a["name"][8:]
                    # Runs are newest first. Keep the latest accepted delivery;
                    # a later optional Release-mirror error does not invalidate it.
                    if slug_key not in arts:
                        arts[slug_key] = a["archive_download_url"]
                        art_ids[slug_key] = a["id"]
        try:
            reserve=json.loads(gh('GET',f'/contents/{SOURCE_INVENTORY_KEY}?ref=main',raw=True,timeout=20))
            if source_inventory(st,reserve)['inventory_fresh']:
                reserve_records=reserve.get('artifacts',[])
                for record in reserve.get('artifacts',[]):
                    s=record['slug']
                    if s not in arts and any(p.get('status')=='verified' for p in record.get('parts',[])):
                        aid=int(record['artifact_id'])
                        arts[s]=API+f'/actions/artifacts/{aid}/zip'
                        art_ids[s]=aid
        except Exception as exc:
            log.warning('Reserve artifact index unavailable: %s',type(exc).__name__)

    # 遍历 pending，找到第一个有 artifact 的
    # 无 artifact 且未超重试次数 → 自动重试出片
    # 超过 12 小时无 artifact → 标记为 failed，避免永远 pending
    e = None
    slug = None
    retried = 0
    selected_part_index = None
    for candidate in pending:
        s = candidate["slug"]
        # 上轮上传中断的（uploading 标记仍在）：绝不能盲目重传——先查 B站
        if candidate.get("uploading"):
            lease_age = now - float(candidate.get("uploading_ts") or 0)
            if has_active_upload_lease(candidate, now):
                log.info(f"{s} 另一投稿调用仍在执行（{lease_age:.0f}s），本轮跳过")
                continue
            dup = bili_find_duplicate(candidate.get("upload_title") or "")
            if dup:
                st["published"][s] = {"bvid": dup, "ts": int(time.time()),
                                       "title": candidate.get("upload_title") or candidate.get("title", "")}
                candidate.pop("uploading", None)
                candidate.pop("uploading_ts", None)
                log_event("dedup", f"{s} 上轮其实已传过（{dup}），补记状态，不再重传")
                log.info(f"{s} 上轮其实已传过（{dup}），补记状态，不再重传")
                save_state(st)
            else:
                candidate["failed"] = True
                candidate.pop("uploading", None)
                candidate.pop("uploading_ts", None)
                log_event("fail", f"{s} 上轮上传状态未知且 B站查无此片，标记失败待人工确认（绝不自动重传）")
                log.error(f"{s} 上轮上传状态未知且 B站查无此片，标记失败待人工确认（绝不自动重传）")
                save_state(st)
            continue
        if s in arts:
            selected_part_index=inventory_part_index(candidate,art_ids.get(s),reserve_records,budget)
            if selected_part_index is None:
                log.info('%s 已验证余量暂不符合今日形态比例，继续找真人片',s)
                continue
            e = candidate
            slug = s
            break
        age = now - max(candidate.get("ts", 0), candidate.get("last_retry", 0))
        retries = candidate.get("retries", 0)
        last_retry = candidate.get("last_retry", 0)
        if age > 6 * 3600:
            candidate["failed"] = True
            log_event("fail", f"{s} 超过 12h 无成片，标记失败", (candidate.get("title") or "")[:60])
            log.info(f"{s} 超过 12h 无 artifact，标记失败")
        elif retries < 2 and now - max(candidate.get("ts", 0), last_retry) > 2 * 3600:
            # 自动重试出片：用之前保存的 asset_url 重新触发 workflow
            # 注意用 max(ts, last_retry)：刚调度的条目（出片还在跑）不能重触发
            asset_url = candidate.get("asset_url") or candidate.get("source_url")
            if asset_url:
                try:
                    gh("POST", f"/actions/workflows/{WF_PRODUCE}/dispatches", {
                        "ref": "main",
                        "inputs": {"source": asset_url, "slug": s,
                                   "speaker": "林园", "occasion": candidate["title"][:30],
                                   "delay_hours": "0", "auto_publish": "false",
                                   "include_full": "true" if candidate.get("weekly_full_week") else "false"}})
                    candidate["retries"] = retries + 1
                    candidate["last_retry"] = int(now)
                    retried += 1
                    log.info(f"{s} 无 artifact，自动重试出片 ({retries+1}/2)")
                except Exception as retry_err:
                    log.warning(f"{s} 重试触发失败: {retry_err}")
            else:
                log.warning(f"{s} 无可用 asset_url，无法重试")
        else:
            log.info(f"{s} 成片未就绪，跳过")

    # 保存 failed 标记和重试记录
    if any(c.get("failed") for c in pending) or retried > 0:
        save_state(st)

    if e is None:
        if retried > 0:
            log.info(f"已触发 {retried} 条重试，等待出片完成")
        else:
            log.info("无可投稿件（所有待发布视频都无 artifact 或已超时）")
        return {"published": 0}

    log.info(f"准备投稿: {slug} (共 {len(pending)} 条待发布，本次只投 1 条)")

    tmp = Path(tempfile.mkdtemp())
    tmp.mkdir(parents=True, exist_ok=True)
    with open(tmp / "cookies.json", "w") as f:
        f.write(COOKIES_JSON)
    os.chmod(tmp / "cookies.json", 0o600)

    done = 0
    used_release = False
    delivery_dir = tmp / slug
    delivery_dir.mkdir(parents=True, exist_ok=True)
    if slug in art_ids:
        used_release=download_inventory_part(art_ids[slug],selected_part_index,delivery_dir)
        if not used_release:
            # A transient download failure is not a source-quality rejection.
            # Do not spend 27 minutes on another unbounded copy of this bundle.
            shutil.rmtree(tmp,ignore_errors=True)
            return {'published':0,'artifact_download_retryable':1}
    if not used_release and not e.get("reprocessing_quality"):
        part_index = selected_part_index
        used_release = download_v4_fast_part(slug, part_index, delivery_dir)
        if not used_release and any(row.get('slug')==slug and row.get('part_index')==part_index
                                    for row in FRESH_SIX_APPROVED.values()):
            # Reviewed packages can select sparse original files. A Release's
            # original indices may point at a different, unapproved part.
            shutil.rmtree(tmp,ignore_errors=True)
            return {'published':0,'artifact_download_retryable':1}
        if not used_release:
            used_release = download_release_part(slug, part_index, delivery_dir)

    if not used_release:
        shutil.rmtree(delivery_dir, ignore_errors=True)
        delivery_dir.mkdir(parents=True, exist_ok=True)
        zf = tmp / f"{slug}.zip"
        artifact_url = arts.get(slug)
        if not artifact_url:
            log.error(f"✗ {slug} 逐条 Release 不完整且无 Artifact 可回退")
            shutil.rmtree(tmp, ignore_errors=True)
            return {"published": 0}
        dl = subprocess.run(["curl", "-sfL", "--max-time", "1620", "-C", "-",
                             "-H", f"Authorization: Bearer {TOKEN}",
                             "-o", str(zf), artifact_url], capture_output=True)
        if dl.returncode != 0:
            log.error(f"✗ {slug} artifact 下载失败: {dl.stderr.decode()[:200]}")
            e["failed"] = True
            save_state(st)
            log.info(f"{slug} artifact 不可用，标记为失败")
            shutil.rmtree(tmp, ignore_errors=True)
            return {"published": 0}
        subprocess.run(["unzip", "-oq", str(zf), "-d", str(tmp / slug)], check=True)
    else:
        log.info(f"{slug} 使用 Release 逐条下载，未拉取整批 Artifact")
    # 长视频拆多条：检测所有 final*.mp4（final.mp4 / final_1.mp4 ...）
    final_videos = sorted((tmp / slug).glob("final*.mp4"), key=lambda p: p.name)
    if not final_videos:
        log.error(f"✗ {slug} 成片不存在")
        shutil.rmtree(tmp, ignore_errors=True)
        return {"published": 0}

    # meta.json：可能是 dict（单条）或 list（多条），统一成 parts 列表
    meta_info = {}
    meta_f = tmp / slug / "meta.json"
    parts = []
    if meta_f.exists():
        try:
            mj = json.loads(meta_f.read_text(encoding="utf-8"))
            parts = mj if isinstance(mj, list) else [mj]
        except Exception:
            parts = []
    if not parts:
        parts = [{}]
    parts_total = len(parts)
    e["parts_total"] = parts_total

    # 库存可独立选择后面的真人片；进度只在实际发布或明确跳过后更新。
    k = selected_part_index
    if k >= parts_total:
        log.info(f"{slug} 的 {parts_total} 条已全部投完")
        shutil.rmtree(tmp, ignore_errors=True)
        return {"published": 0}
    if not batch_slug:
        inspected = next((r for r in reserve_records if r.get('slug') == slug
                          and r.get('artifact_id') == art_ids.get(slug)), None)
        verified = ({int(p['index']) for p in inspected.get('parts', [])
                     if p.get('status') == 'verified'} if inspected else set(range(len(parts))))
        usable = [i for i, item in enumerate(parts)
                  if i not in processed_part_indices(e)
                  and i in verified
                  and content_fits_slot(item, e, now)
                  and not daily_mix_error(item, budget)]
        if not usable:
            return _continue_after_rejection(event, context, slug,
                {'published': 0, 'no_content_for_slot': 1}, tmp)
        k = k if k in usable else usable[0]
    part = parts[k]
    video = tmp / slug / part.get("final", "final.mp4")
    if not video.exists():
        video = final_videos[k] if k < len(final_videos) else final_videos[0]

    expected_sha256 = str(event.get("expected_sha256") or "").strip().lower()
    if expected_sha256:
        import hashlib
        if (not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
                or hashlib.sha256(video.read_bytes()).hexdigest() != expected_sha256
                or (part.get("fingerprints") or {}).get("sha256") != expected_sha256):
            log.error(f"✗ {slug} 指定成片与用户验收哈希不一致")
            shutil.rmtree(tmp, ignore_errors=True)
            return {"published": 0, "reviewed_checksum_mismatch": 1}

    # 优先用当前 part 的 meta 文案 + 封面
    meta_info = part
    title = (part.get("title") or e.get("title") or slug)
    desc = clean_publish_desc(part.get("desc", ""))
    tags = ",".join(part.get("tags", ["林园", "价值投资"]))
    cover = None
    if part.get("cover") and (tmp / slug / part["cover"]).exists():
        cover = tmp / slug / part["cover"]
    # 标题必须含「林园」（硬性要求），但改为前缀式而非「｜林园」后缀。
    # 2026-09-01 竞品实测：高播放标题是「林园：+原话金句」，我们的「…｜林园」
    # 后缀是第三人称摘要体，同期播放中位 23 vs 竞品 584~1956（差 33 倍）。
    if "林园" not in title:
        title = f"林园：{title}"
    title = title[:78]

    # 老库存是在人物/水印/分辨率/指纹闸门上线前生成的，不能凭“文件存在”继续投。
    # 隔离后用原素材重做，并在本时段继续寻找下一条，避免空耗发布时段。
    quality_error = (editorial.metadata_error(part, mp4_duration(video))
                     or artifact_quality_error(part)
                     or artifact_subtitle_error(part, tmp / slug))
    if e.get("required_presentation_version", 0) >= 1 and int(part.get("presentation_version") or 0) < e["required_presentation_version"]:
        quality_error = "新日常任务缺少多版式通用规则证明，禁止沿用旧库存"
    if quality_error:
        started = _request_quality_reprocess(
            st, e, slug, quality_error, art_ids.get(slug))
        result = {"published": 0, "reprocessing": int(started),
                  "quality_rejected": 1}
        return _continue_after_rejection(event, context, slug, result, tmp)
    
    if fresh_budget is not None:
        review_error = fresh_six_review_error(part, video, slug, e.get("source_url") or "")
        if review_error:
            log.warning(f"{slug}: {review_error}")
            shutil.rmtree(tmp, ignore_errors=True)
            return {"published": 0, "review_required": 1}

    mix_error = daily_mix_error(part, budget)
    if mix_error:
        log.info(f"{slug}: {mix_error}")
        return _continue_after_rejection(event, context, slug,
                                         {"published": 0, "mix_deferred": 1}, tmp)

    # FC 依赖层路径：尝试多个可能的路径
    import sys
    possible_paths = ["/opt/python", "/opt/python/lib/python3.10/site-packages", "/code/python"]
    log.info(f"Python 路径: {sys.path}")
    for path in possible_paths:
        if os.path.exists(path):
            log.info(f"层路径存在: {path}")
            if path not in sys.path:
                sys.path.insert(0, path)
                log.info(f"添加层路径: {path}")
        else:
            log.info(f"层路径不存在: {path}")
    
    # 检查 biliup 是否可用
    try:
        import biliup
        log.info(f"✓ biliup 可用: {biliup.__file__}")
    except ImportError as e:
        log.error(f"✗ biliup 不可用: {e}")
        log.error(f"  sys.path: {sys.path}")
        shutil.rmtree(tmp, ignore_errors=True)
        return {"published": 0}
    
    # 使用 subprocess 调用 biliup CLI，设置 PYTHONPATH 环境变量
    log.info(f"准备投稿: {slug}")
    
    # 构造命令
    cmd = [sys.executable, "-m", "biliup",
           "-u", str(tmp / "cookies.json"), "upload", str(video),
           "--title", title, "--tid", str(TID), "--copyright", str(COPYRIGHT),
           "--source", publication_source_label({
               **e,
               "source_platform": (meta_info.get("source_platform")
                                   or e.get("source_platform")),
           }),
           "--desc", desc, "--tag", tags, "--limit", "1"]
    # The web submit route is the route with confirmed production receipts.
    cmd += ["--submit", "web"]
    if cover:
        cmd += ["--cover", str(cover)]
    
    # 立即发布：cron 已按 3 时段（10/16/21）唤醒 + 每次只投 1 条，
    # 天然分散不扎堆，无需再算延迟发布时间（2026-08-29 去掉 pick_publish_slot 双轨制）
    log.info("立即发布（cron 时段已分散，无需延迟）")

    # ── 投稿前双重防护（2026-08-19 五连发事故）──
    # 0) 同母片按连续时间段查重；不同观点可继续使用，未知旧范围仍阻断。
    src_url = (e.get("source_url") or "").strip()
    # 仅本次已明确授权的 V4 全量批次允许同母片重制后再次投递。
    # 其他命名批次仍保留全部历史查重闸门。
    # Reviewed new excerpts can continue a previously used long interview.
    # Their exact MP4 hashes are allowlisted above; content/topic/title checks
    # below still reject old excerpts, including copies under different URLs.
    if src_url and not explicit_v4_batch and fresh_budget is None:
        reuse_error=editorial.source_reuse_error(part,src_url,st)
        if reuse_error:
            _record_skipped_part(st,e,slug,part,parts_total,k,title,reuse_error)
            log_event('dedup',f'同源选段拦截 {slug}',reuse_error)
            return _continue_after_rejection(event,context,slug,
                {'published':0,'skipped':1,'source_overlap':1},tmp)
    # 1) 内容指纹：不同 URL、不同平台、重新压缩/裁切、换标题都要能拦。
    comparison_state = replacement_comparison_state(st, part, slug)
    content_dup = (None if explicit_v4_batch else
                   find_content_duplicate(part.get("fingerprints") or {}, comparison_state))
    if content_dup:
        reason = (f"与 {content_dup['bvid'] or content_dup['slug']} 重复："
                  f"{content_dup['reason']}")
        _record_skipped_part(st, e, slug, part, parts_total, k, title, reason)
        if e['published_parts'] >= parts_total and slug in art_ids:
            try:
                gh("DELETE", f"/actions/artifacts/{art_ids[slug]}")
            except Exception as ae:
                log.warning(f"删除已处理 artifact 失败: {ae}")
        log_event("dedup", f"⛔ 跳过重复成片 {slug}[{k+1}]", reason)
        log.info(f"⛔ 跳过重复成片 {slug}[{k+1}]：{reason}")
        return _continue_after_rejection(
            event, context, slug, {"published": 0, "skipped": 1}, tmp)

    # 2) 主题冷却：即使不是逐字同片，同一个观点 14 天内也不再发布。
    # 同一条长母片的不同 part 本来就要多角度发布；内容指纹已经在前一步拦截
    # 真重复。主题冷却只拦截其他母片的重复观点，不能让同源切片互相误杀。
    topic_dup = (None if explicit_v4_batch else
                 find_recent_topic(title, comparison_state, now=now, exclude_slug=slug))
    if topic_dup:
        reason = (f"主题与 {topic_dup['bvid'] or topic_dup['slug']} "
                  f"相似 {topic_dup['score']:.0%}，14 天冷却")
        _record_skipped_part(st, e, slug, part, parts_total, k, title, reason)
        if e['published_parts'] >= parts_total and slug in art_ids:
            try:
                gh("DELETE", f"/actions/artifacts/{art_ids[slug]}")
            except Exception as ae:
                log.warning(f"删除已处理 artifact 失败: {ae}")
        log_event("dedup", f"⛔ 跳过重复主题 {slug}[{k+1}]", reason)
        log.info(f"⛔ 跳过重复主题 {slug}[{k+1}]：{reason}")
        return _continue_after_rejection(
            event, context, slug, {"published": 0, "skipped": 1}, tmp)

    # 3) 普通批次查同标题；本次授权批次由上传租约和进度防重。
    dup = None if explicit_v4_batch else bili_find_duplicate(title)
    if comparison_state is not st and dup in HIDDEN_SHORT_SIX_BVIDS:
        dup = None  # Explicitly superseded hidden short; all other titles remain protected.
    if dup:
        st["published"][slug] = {"bvid": dup, "ts": int(time.time()), "title": title,
                                 "source_platform": meta_info.get("source_platform") or platform_of(e.get("source", ""))}
        save_state(st)
        log.info(f"⛔ {slug} 已在 B站存在（{dup}），补记状态跳过上传")
        return _continue_after_rejection(
            event, context, slug, {"published": 0, "existing": 1}, tmp)
    # 4) 落盘上传意图：万一上传后崩溃，下轮凭 uploading 标记走恢复逻辑而非重传
    e["uploading"] = True
    e["uploading_ts"] = int(time.time())
    e["upload_title"] = title
    e['upload_part_index'] = k
    save_state(st)
    
    # 设置 PYTHONPATH 环境变量
    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(sys.path)
    
    # 调用 biliup CLI
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=1620, env=env)
    except subprocess.TimeoutExpired:
        log_event("fail", f"✗ {slug} 投稿超过 27 分钟", title[:80])
        log.error(f"✗ {slug} 投稿超时；保留上传租约供下轮查重恢复")
        shutil.rmtree(tmp, ignore_errors=True)
        return {"published": 0, "upload_timeout": 1}
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r'BV\w{10}', out)
    if r.returncode == 0 and m:
        bvid = m.group(0)
        e.pop("uploading", None)
        e.pop("uploading_ts", None)
        e.pop("upload_title", None)
        e.pop("reprocessing_quality", None)
        e.pop("quality_failure", None)
        # 记录这次投到第几条了（长视频多条时分次投稿）
        mark_part_processed(e,k)
        e.pop('upload_part_index',None)
        st["daily_publish"]["count"] = st["daily_publish"].get("count", 0) + 1
        st['daily_publish'].setdefault('published_hours', []).append(
            time.gmtime(now + 8 * 3600).tm_hour)
        mode_counter = "audio_card_count" if part.get("render_mode") == "audio_card" else "live_video_count"
        st["daily_publish"][mode_counter] = st["daily_publish"].get(mode_counter, 0) + 1
        if fresh_budget is not None:
            fresh_budget["count"] += 1
            fresh_budget[mode_counter] = fresh_budget.get(mode_counter, 0) + 1
        prev_pub = st.get("published", {}).get(slug, {})
        prev_bvids = prev_pub.get("bvids", []) + [bvid]
        # parts 列表：每条 part 记 bvid+title+ts，修复「长视频拆多条标题丢全」的 bug（2026-08-27）
        parts_log = list(prev_pub.get("parts", []))
        parts_log.append({"status": "published", "bvid": bvid, "title": title,
                          "part_index": k,
                          "render_mode": part.get("render_mode"),
                          "content_type": part.get("content_type"),
                          "fresh_six_date": FRESH_SIX_DATE if fresh_budget is not None else None,
                          "fresh_six_batch": fresh_six_counter(slug) if fresh_budget is not None else None,
                          "source_segments": part.get("segments") or [],
                          "source_sha256": part.get("source_sha256"),
                          "ts": int(time.time()),
                          "fingerprints": meta_info.get("fingerprints") or {}})
        st["published"][slug] = {
            "bvid": bvid,  # 最新一条的 bvid
            "bvids": prev_bvids,
            "parts": parts_log,
            "parts_total": parts_total,
            "ts": int(time.time()),
            "title": title,
            "source_platform": meta_info.get("source_platform") or platform_of(e.get("source", "")),
            "source_url": e.get("source_url", ""),
            "watermark_cropped": meta_info.get("watermark_cropped", True),
            "subtitles_burned": meta_info.get("subtitles_burned", True),
            "has_existing_subtitles": meta_info.get("has_existing_subtitles", False),
            "vertical": meta_info.get("vertical", False),
            "duration_sec": meta_info.get("duration_sec", 0),
            "resolution": meta_info.get("resolution") or {},
            "fingerprints": meta_info.get("fingerprints") or {},
            "publish_time": e.get("publish_time", "") or time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        # 投稿成功 → 从 pending_retry 清理对应 key/source_url，防止重复派发
        st["pending_retry"] = [x for x in st.get("pending_retry", [])
                               if x.get("key") != e.get("key")
                               and (x.get("page_url") or "").strip() != (e.get("source_url") or "").strip()]
        # 只在所有 part 都投完时才删 artifact（否则下次还要投下一条）
        if slug in art_ids and e['published_parts'] >= parts_total:
            try:
                gh("DELETE", f"/actions/artifacts/{art_ids[slug]}")
                log.info(f"✓ 已删除 artifact: deliver-{slug}")
            except Exception as ae:
                log.warning(f"删除 artifact 失败: {ae}")
        save_state(st)
        log_event("publish_ok", f"✅ 已投[{k+1}/{parts_total}] https://www.bilibili.com/video/{bvid}", title[:50])
        log.info(f"✅ 已投[{k+1}/{parts_total}] https://www.bilibili.com/video/{bvid}")
        done += 1
    else:
        # 输出完整错误信息，方便调试
        log_event("fail", f"✗ {slug} 投稿失败", f"rc={r.returncode} tail={out[-1800:]}")
        log.error(f"✗ {slug} 投稿失败")
        log.error(f"  返回码: {r.returncode}")
        log.error(f"  stdout: {r.stdout[:500]}")
        log.error(f"  stderr: {r.stderr[:500]}")
        # 尝试提取错误代码
        tail = [ln for ln in out.splitlines() if "code" in ln or "Error" in ln or "error" in ln][-3:]
        if tail:
            log.error(f"  错误信息: {'; '.join(tail)[:300]}")

    shutil.rmtree(tmp, ignore_errors=True)
    return {"published": done}

# Deployment marker: visual quality gate v11.


def publish_tv_wine_review_once(event):
    """Exact user-authorized replacement; isolated receipt, no queue resets or refill."""
    import base64
    import hashlib
    slug = "ly-tv-wine-review-0905"
    if (event.get("slug") != slug
            or event.get("review_of_bvid") != "BV1Ngt163EZ4"):
        raise ValueError("review target does not match authorization")
    receipt_path = f"linyuan/.automation/{slug}-receipt.json"
    # A successful creation of this distinct file is our durable upload lock.
    # Existing/unknown attempts never initiate a second upload.
    try:
        old = gh("GET", f"/contents/{receipt_path}?ref=main")
        return json.loads(base64.b64decode(old["content"]))
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    with tempfile.TemporaryDirectory(prefix="tv-review-") as directory:
        tmp = Path(directory)
        artifact_id = int(event.get("artifact_id") or 0)
        if artifact_id <= 0 or not re.fullmatch(r"[a-f0-9]{64}", str(event.get("expected_sha256") or "")):
            raise ValueError("exact reviewed artifact and checksum are required")
        artifact = gh("GET", f"/actions/artifacts/{artifact_id}")
        if artifact.get("name") != "tv-wine-review" or artifact.get("expired"):
            raise ValueError("wrong or expired review artifact")
        import requests
        redirect = requests.get(API + f"/actions/artifacts/{artifact_id}/zip",
            headers={"Authorization": f"Bearer {TOKEN}"}, allow_redirects=False, timeout=60)
        if redirect.status_code != 302 or not redirect.headers.get("Location", "").startswith("https://"):
            raise RuntimeError("could not resolve exact review artifact")
        archive_path = tmp / "review.zip"
        # Do not forward the GitHub credential to the signed blob URL.
        with requests.get(redirect.headers["Location"], stream=True, timeout=120) as response:
            response.raise_for_status()
            size = 0
            with archive_path.open("wb") as target:
                for block in response.iter_content(1024 * 1024):
                    size += len(block)
                    if size > 512 * 1024 * 1024:
                        raise RuntimeError("review artifact exceeds size limit")
                    target.write(block)
        with zipfile.ZipFile(archive_path) as archive:
            # Extract only the three exact top-level files, never arbitrary paths.
            for name in ("meta.json", "final.mp4", "cover.jpg"):
                with archive.open(name) as source, (tmp / name).open("wb") as target:
                    shutil.copyfileobj(source, target)
        part = json.loads((tmp / "meta.json").read_text())
        if (not isinstance(part, dict) or part.get("slug") != slug
                or part.get("review_of_bvid") != "BV1Ngt163EZ4"
                or part.get("source_sha256") !=
                "14ef8887af5fe638e7c731a071695131827f079f0e0309a5bf8cccaad6f977bb"
                or part.get("segments") != [{"start": 239.46, "end": 477.54}]):
            raise ValueError("review metadata does not identify the selected clip")
        error = artifact_quality_error(part)
        if error:
            raise ValueError(error)
        video = tmp / "final.mp4"
        if part["fingerprints"]["sha256"] != event["expected_sha256"]:
            raise ValueError("artifact differs from visually reviewed file")
        if hashlib.sha256(video.read_bytes()).hexdigest() != part["fingerprints"]["sha256"]:
            raise ValueError("review video checksum mismatch")
        receipt = {"slug": slug, "review_of_bvid": "BV1Ngt163EZ4",
                   "status": "uploading", "ts": int(time.time()),
                   "sha256": part["fingerprints"]["sha256"], "title": part["title"]}
        def encoded():
            return base64.b64encode(json.dumps(receipt, ensure_ascii=False).encode()).decode()
        locked = gh("PUT", f"/contents/{receipt_path}", {
            "message": "ops: reserve single authorized TV/wine review upload",
            "content": encoded()})
        lock_sha = locked["content"]["sha"]
        cookies = tmp / "cookies.json"
        cookies.write_text(COOKIES_JSON)
        os.chmod(cookies, 0o600)
        env = os.environ.copy()
        env["PYTHONPATH"] = ":".join(sys.path + ["/opt/python"])
        cmd = [sys.executable, "-m", "biliup", "-u", str(cookies), "upload", str(video),
               "--title", part["title"], "--tid", str(TID), "--copyright", str(COPYRIGHT),
               "--source", "央视公开访谈资料", "--desc", clean_publish_desc(part["desc"]),
               "--tag", ",".join(part["tags"]), "--cover", str(tmp / "cover.jpg"), "--limit", "1"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1620, env=env)
        match = re.search(r"BV[a-zA-Z0-9]{10}", (result.stdout or "") + (result.stderr or ""))
        if result.returncode == 0 and match:
            receipt.update(status="published", bvid=match.group(0), ts=int(time.time()))
        else:
            receipt.update(status="upload_result_unknown", returncode=result.returncode)
        gh("PUT", f"/contents/{receipt_path}", {
            "message": "ops: record single TV/wine review upload result",
            "sha": lock_sha, "content": encoded()})
        log_event("review_publish", json.dumps(receipt, ensure_ascii=False))
        return receipt
