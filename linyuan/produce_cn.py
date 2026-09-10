#!/usr/bin/env python3
"""出片:任意中文视频 → 3 分钟双语字幕短片。

和 bilingual-subtitle-burner 的 produce.py 的关系
------------------------------------------------
produce.py 是给**英文片源**设计的(direction 写死 en2zh、srt_lang 写死 en),
且依赖仓库里另外 5 个本地缺失的模块。这里不改它,而是针对中文源单独实现,
复用同一套思路:转写 → 挑金句 → 翻译 → 烧字幕 → 拼接。

针对中文股东会素材做的三处专门处理(produce.py 都没有):
  1. CPU 离线 SenseVoice + 原始 token 时间戳重新组句
  2. 保守的专名纠错；不把有歧义的普通词扩写成药名
  3. loudnorm 响度标准化 -- 观众席手机录音普遍 -36dB,不处理根本听不见

用法:
    python3 produce_cn.py --source videos/xx.mp4 --slug xx --speaker 林园
    python3 produce_cn.py --source xx.mp4 --slug xx --dry-run   # 只挑金句不出片
"""
import argparse
import base64
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent
# Also support importlib loaders used by the CPU ASR regression gate.
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
import editorial_policy as editorial
PRESENTATION_RULES_VERSION = 2
# 中文生产只允许本地 CPU 识别；不自动回退识别 API 或 large-v3。
# legacy Whisper 函数保留供历史代码读取，不进入本生产入口。
WHISPER = os.environ.get("WHISPER_MODEL") or "/home/node/.cache/whisper/large-v3"
ASR_PIPELINE_VERSION = 5
ASR_CHUNK_SEC = 30.0
ASR_OVERLAP_SEC = 3.0
ASR_CPU_THREADS = max(1, min(4, int(os.environ.get("ASR_CPU_THREADS", "2"))))
ASR_BACKEND = os.environ.get("ASR_BACKEND") or "sensevoice"
# SenseVoice-Small 模型目录（model.int8.onnx + tokens.txt）
SENSEVOICE_DIR = os.environ.get("SENSEVOICE_MODEL_DIR") or "/tmp/sv_onnx"
# Fun-ASR-Nano 模型目录（int8 三件套 + tokenizer 目录，弃用仅保留）
FUNASR_DIR = os.environ.get("FUNASR_MODEL_DIR") or "/tmp/funasr_llm"
FUNASR_LANG = os.environ.get("FUNASR_LANG") or "zh"

SF_URL = "https://api.siliconflow.cn/v1/chat/completions"
VISION_MODEL = os.environ.get("VISION_MODEL") or "Qwen/Qwen3-VL-8B-Instruct"
TEXT_BACKEND = (os.environ.get("TEXT_BACKEND") or "local").strip().lower()
LOCAL_LLM_URL = (os.environ.get("LOCAL_LLM_URL") or
                 "http://127.0.0.1:11434/api/chat").strip()
LOCAL_LLM_MODEL = (os.environ.get("LOCAL_LLM_MODEL") or "qwen3:4b").strip()
LOCAL_FACE_MODEL_DIR = Path(os.environ.get("LOCAL_FACE_MODEL_DIR") or "/tmp/linyuan-face-models")
LOCAL_FACE_DETECTOR_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx")
LOCAL_FACE_RECOGNIZER_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx")
LOCAL_FACE_COSINE_THRESHOLD = float(os.environ.get("LOCAL_FACE_COSINE_THRESHOLD") or .363)

# 第一财经 2026-08-22《投资人说》官方节目封面。这里只作为机器人物比对的
# 参考图，不会进入成片或对外分发；可用环境变量替换为自有参考图 URL。
LINYUAN_REFERENCE_URL = os.environ.get("LINYUAN_REFERENCE_URL") or (
    "https://imgcdn.yicai.com/vms-new/2026/08/"
    "b6e325e8-6616-46ed-902e-2987008296f5.jpg"
)
VISUAL_GATE_VERSION = 3
VISUAL_SAMPLE_COUNT = 6
VISUAL_MIN_MATCHES = 2
VISUAL_MIN_MATCH_RATIO = 0.50
VISUAL_MIN_CONFIDENCE = 0.75
MIN_SHORT_EDGE = 480
SOURCE_MIN_DURATION = int(editorial.MIN_SECONDS)
SOURCE_MAX_DURATION = 7200
FINGERPRINT_VERSION = 1
QUALITY_GATE_VERSION = 13
VISUAL_STANDARD_VERSION = 3
COVER_STANDARD_VERSION = 4
# 对标「园园滚雪球」实际成片后的音频卡规格：它的静态人物卡/活动拼图均以
# 9:16 竖版上传，B站桌面播放器自行补黑边；移动端则直接占满屏幕。我们保留
# 这种有效的版式，但不复制对方插画或照片资产，改用自有的通用编辑卡视觉。

# 正式生产目标（2026-09-06 用户实审）：发布候选真人动态 >=70%，
# audio_card <=30%。明显持续黑区/错误取景仍由 V11 淘汰，不能放水。
LIVE_VIDEO_TARGET_RATIO = 0.70
AUDIO_CARD_MAX_RATIO = 0.30

AUDIO_CARD_WIDTH = 720
AUDIO_CARD_HEIGHT = 1280
AUDIO_CARD_TEMPLATE = "live_editorial_v4_readable"
AUDIO_CARD_TOPIC_MAX_CHARS = 42
AUDIO_CARD_DISCLAIMER = "个人观点 · 仅供交流 · 非投资建议"
ALLOW_AUDIO_CARD = os.environ.get("ALLOW_AUDIO_CARD", "1") != "0"
TITLE_ASR_BLACKLIST = ("手财", "一定折")
LIVE_REGION = {"x": 44, "y": 360, "width": 632, "height": 470}
SUBTITLE_REGION = {"x": 38, "y": 874, "width": 644, "height": 166}
SAFE_MARGIN = {"left": 38, "right": 38, "bottom": 64}

# 自有品牌水印：先清掉来源平台/搬运账号角标，再在同一次编码中叠加到右上角。
# 参数可通过环境变量微调，但生产默认值必须保持小尺寸、半透明，避免遮挡内容。
BRAND_WATERMARK = Path(os.environ.get("BRAND_WATERMARK") or
                       (BASE / "assets" / "yuanlai-snowball-watermark.png"))
BRAND_WATERMARK_WIDTH_RATIO = float(
    os.environ.get("BRAND_WATERMARK_WIDTH_RATIO") or 0.15)
BRAND_WATERMARK_OPACITY = float(
    os.environ.get("BRAND_WATERMARK_OPACITY") or 0.68)
BRAND_WATERMARK_MARGIN_RATIO = float(
    os.environ.get("BRAND_WATERMARK_MARGIN_RATIO") or 0.02)

# 免费额度可用的模型,按质量排序;限流时逐个降级
MODELS = ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen3-8B"]

TARGET_SEC = int(editorial.TARGET_SECONDS)  # 日常以2～3分钟完整观点为主
MIN_HIGHLIGHT_SCORE = 7   # 金句评分门槛：低于此分不出片（2026-09-02）
TARGET_SEC_MID = 420     # 中视频目标时长（7分钟话题片，2026-08-29 对标竞品中视频）
MAX_CHARS = 18            # 单条字幕上限（字数）
MAX_CUE_SEC = 6.0         # 单条字幕上限（秒）：ASR 不吐标点时兜底硬断（2026-09-01）
MAX_GAP_SEC = 1.0         # token 间静音超过此值就断句：ASR 稀疏时字幕会横跨大段静音
MAX_TOKEN_SEC = 1.0       # 单个 token 时长封顶：cue 的 end 取自「下一个 token 的 start」，
                          # ASR 稀疏时中间大段静音会被算进上一个字，导致「白」一个字占 22 秒
                          # （2026-09-01 用真实音频重跑 SenseVoice 实测）
# ASR 质量闸门：识别字数/音频秒数，低于此值判为「音频太差、识别大面积失败」，放弃出片。
# 2026-09-01 实测标定：正常片 2.1~8.1 字/秒（多数 4~5）；片仔癀现场收音那条仅 0.73 字/秒
# （450s 只转出 328 字，漏识约 70%，成片字幕全是「隔一牛」这类乱码）。阈值留足余量。
# ASR 质量闸门阈值（2026-09-01 用新代码在真实音频上重跑 SenseVoice 标定）：
#   茅台专访(正常)   条内语速 4.69 / 整段密度 4.39
#   同仁堂讲话(正常) 条内语速 3.73 / 整段密度 2.92
#   片仔癀现场(坏片) 条内语速 2.61 / 整段密度 1.03  ← 41% 时间完全没识别出内容
# 两个指标「同时」偏低才判废，避免「片头音乐长」这类正常片被单指标误杀。
ASR_MIN_SPEECH_RATE = float(os.environ.get("ASR_MIN_SPEECH_RATE") or 3.0)
ASR_MIN_DENSITY = float(os.environ.get("ASR_MIN_DENSITY") or 1.8)
MIN_CHARS = 6
BREAK = "，。！？；：、,.!?;"
# 语气词过滤:ASR 会把 "啊、嗯、呢、吧" 等单独识别为一帧
# 单帧语气词没有信息量,反而让字幕跳动
FILLER_WORDS = set("啊呀呐呢吧嘛哦噢哎唉哼嗯呃哈呵嘿")
# 最小帧间隔(秒):相邻字幕间距太小会闪烁
MIN_GAP = 0.3

# ASR 专名纠错表。现场录音对专有名词识别率很差,而这些词恰恰是内容的核心。
# 只放「读音接近且在本领域无歧义」的,避免误改。
GLOSSARY = {
    "老离化": "老龄化", "老民化": "老龄化",
    "大人堂": "达仁堂", "达人塔": "达仁堂", "大二代": "达仁堂",
    "白耀": "白药", "荷药": "中药", "核药": "中药",
    "片仔皇": "片仔癀", "片仔黄": "片仔癀",
    # ASR 音近错字（2026-08-29 用户反馈）：骨=股、0源/零源=林园
    "骨价": "股价", "骨票": "股票", "骨市": "股市",
    "0源": "林园", "零源": "林园", "0园": "林园", "零园": "林园",
    # 2026-09-04 用 14 份真实跨渠道片段横评捕到的稳定音近错。
    # 只收录在林园财经语境中几乎无歧义的词，不做泛化单字替换。
    "林元": "林园", "林源": "林园", "林远": "林园",
    "达人堂": "达仁堂",
    "高抛低息": "高抛低吸", "选骨": "选股", "骨子占的比例": "股票占的比例",
    "夕洋行业": "夕阳行业", "西洋行业": "夕阳行业",
    "划解这种风险": "化解这种风险", "复荷增长": "复合增长",
    "历史的地位被低估": "历史的低位被低估",
}


class VisualQualityError(RuntimeError):
    """人物或去水印质量不合格；宁可不出片，也不错误归因。"""


class VisualResponseFormatError(VisualQualityError):
    """The service answered, but its frame accounting was incomplete."""


class EditorialReviewUnavailable(VisualQualityError):
    """An ungrounded service response is not evidence against source footage."""


class LocalTextUnavailable(EditorialReviewUnavailable):
    """Local inference did not finish; do not reject or cache an empty selection."""


class SelectionIncomplete(LocalTextUnavailable):
    """The editor did not assess the remaining candidates; not a source verdict."""


class PartProductionUnavailable(EditorialReviewUnavailable):
    """A production time limit is retryable, not evidence of bad source content."""


class CaptionPlanningUnavailable(EditorialReviewUnavailable):
    """Generated screen boundaries failed; the original footage is reusable."""


def text_budget(cloud_seconds):
    """CPU prompt evaluation needs its own bounded budget; no cloud fallback."""
    if TEXT_BACKEND == 'local':
        return min(600, max(cloud_seconds, float(os.environ.get('LOCAL_LLM_TIMEOUT_SEC', '600'))))
    return cloud_seconds


def load_key():
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SILICONFLOW_API_KEY="):
                return line.split("=", 1)[1].strip()
    return (os.environ.get("SILICONFLOW_API_KEY") or "").strip()


def _image_data_url(path):
    mime = "image/png" if str(path).lower().endswith(".png") else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _parse_json_object(text):
    """兼容模型偶尔返回 Markdown 围栏；只接受一个 JSON object。"""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip(),
                  flags=re.I | re.S)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("人物校验没有返回 JSON object")
    out = json.loads(m.group(0))
    if not isinstance(out, dict):
        raise ValueError("人物校验结果不是 object")
    return out


def identity_verdict_passes(verdict, frame_count):
    """多帧参考照比对的硬门槛。无法确认时按不通过处理。"""
    if not isinstance(verdict, dict) or frame_count <= 0:
        return False
    valid = set(range(1, frame_count + 1))

    def indices(name):
        value = verdict.get(name) or []
        if not isinstance(value, list):
            return set()
        return {x for x in value if isinstance(x, int) and x in valid}

    same = indices("same_person_frames")
    different = indices("different_person_frames") - same
    try:
        confidence = float(verdict.get("confidence", 0))
    except (TypeError, ValueError):
        return False
    decisive = len(same) + len(different)
    ratio = len(same) / decisive if decisive else 0.0
    need = min(VISUAL_MIN_MATCHES, frame_count)
    return (len(same) >= need and ratio >= VISUAL_MIN_MATCH_RATIO
            and confidence >= VISUAL_MIN_CONFIDENCE)


def _retry_identity_vlm_in_chunks(reference, frames, speaker, api_key,
                                  chunk_size=3):
    """首轮多图判定失败时分组复核，避免 VLM 漏填后半组帧号。

    复核仍使用相同的参考照和硬门槛，只是把一次六帧拆成两次三帧；任何低置信
    结果都会拉低最终置信度，因此不会把真正的异人素材放行。
    """
    combined = {
        "same_person_frames": [],
        "different_person_frames": [],
        "uncertain_frames": [],
        "best_cover_frame": None,
        "confidence": 1.0,
        "watermark_texts": [],
        "reason": "",
    }
    reasons = []
    for start in range(0, len(frames), chunk_size):
        chunk = frames[start:start + chunk_size]
        try:
            verdict = _call_identity_vlm(reference, chunk, speaker, api_key)
        except VisualResponseFormatError:
            if len(chunk) == 1:
                raise
            verdict = _retry_identity_vlm_in_chunks(
                reference, chunk, speaker, api_key, chunk_size=1)
        valid = set(range(1, len(chunk) + 1))
        for key in ("same_person_frames", "different_person_frames",
                    "uncertain_frames"):
            values = verdict.get(key) or []
            if not isinstance(values, list):
                continue
            combined[key].extend(
                start + value for value in values
                if isinstance(value, int) and value in valid)
        best = verdict.get("best_cover_frame")
        if (combined["best_cover_frame"] is None and isinstance(best, int)
                and best in valid):
            combined["best_cover_frame"] = start + best
        try:
            combined["confidence"] = min(
                combined["confidence"], float(verdict.get("confidence", 0)))
        except (TypeError, ValueError):
            combined["confidence"] = 0.0
        marks = verdict.get("watermark_texts") or []
        if isinstance(marks, list):
            combined["watermark_texts"].extend(
                mark for mark in marks if isinstance(mark, str))
        if verdict.get("reason"):
            reasons.append(str(verdict["reason"]))
    combined["reason"] = "分组复核：" + "；".join(reasons)
    combined["watermark_texts"] = list(dict.fromkeys(
        combined["watermark_texts"]))
    return combined


def _download_speaker_reference(speaker, work):
    """取得人物参考图。林园流水线默认只允许有已配置参考图的人物。"""
    if speaker != "林园":
        raise VisualQualityError(f"没有为人物「{speaker}」配置参考图，无法安全核验")
    out = work / "speaker_reference.jpg"
    if out.exists() and out.stat().st_size > 10_000:
        return out
    req = urllib.request.Request(LINYUAN_REFERENCE_URL, headers={
        "User-Agent": "Mozilla/5.0 (compatible; linyuan-visual-gate/1.0)"})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            data = r.read(3_000_001)
    except Exception as e:
        raise VisualQualityError(f"人物参考图下载失败：{e}") from e
    if not (10_000 <= len(data) <= 3_000_000):
        raise VisualQualityError(f"人物参考图大小异常：{len(data)} bytes")
    out.write_bytes(data)
    return out


def _sample_visual_frames(src, work, count=VISUAL_SAMPLE_COUNT):
    """均匀抽取整片多帧；片头片尾不取，避免节目包装和转场。"""
    try:
        duration = float(probe(src, "format=duration") or 0)
    except (TypeError, ValueError):
        duration = 0
    if duration <= 0:
        raise VisualQualityError("无法取得视频时长，不能做人物核验")
    frames = []
    times = []
    for i in range(count):
        t = duration * (i + 1) / (count + 1)
        fp = work / f"identity_{i + 1}.jpg"
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}",
             "-i", str(src), "-frames:v", "1", "-q:v", "2", str(fp)],
            capture_output=True)
        if r.returncode == 0 and fp.exists() and fp.stat().st_size > 1000:
            frames.append(fp)
            times.append(t)
    if len(frames) < min(3, count):
        raise VisualQualityError(f"人物核验抽帧不足：{len(frames)}/{count}")
    return frames, times


def identity_face_reference(reference):
    """Remove poster lettering and clothing as distractions in the reference only."""
    import cv2
    reference=Path(reference)
    frame=cv2.imread(str(reference))
    if frame is None:
        return reference
    faces=_cascade(cv2.data.haarcascades+'haarcascade_frontalface_default.xml').detectMultiScale(
        cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),1.1,4,minSize=(48,48))
    if not len(faces):
        return reference
    x,y,w,h=map(int,max(faces,key=lambda b:b[2]*b[3]))
    H,W=frame.shape[:2]
    x0,y0=max(0,int(x-w*.12)),max(0,int(y-h*.22))
    x1,y1=min(W,int(x+w*1.12)),min(H,int(y+h*1.10))
    out=reference.with_name(reference.stem+'-face-reference.jpg')
    if not cv2.imwrite(str(out),frame[y0:y1,x0:x1]):
        raise VisualQualityError('人物参考图无法提取清晰面部')
    return out


def _local_face_models():
    """Download OpenCV's ONNX face models once; inference is CPU-only."""
    LOCAL_FACE_MODEL_DIR.mkdir(parents=True,exist_ok=True)
    result=[]
    for name,url,min_bytes in (
        ('face_detection_yunet_2023mar.onnx',LOCAL_FACE_DETECTOR_URL,200_000),
        ('face_recognition_sface_2021dec.onnx',LOCAL_FACE_RECOGNIZER_URL,10_000_000)):
        path=LOCAL_FACE_MODEL_DIR/name
        if not path.exists() or path.stat().st_size<min_bytes:
            tmp=path.with_suffix('.part')
            req=urllib.request.Request(url,headers={'User-Agent':'linyuan-local-face/1.0'})
            try:
                with urllib.request.urlopen(req,timeout=180) as response, tmp.open('wb') as stream:
                    while True:
                        chunk=response.read(1024*1024)
                        if not chunk:break
                        stream.write(chunk)
                if tmp.stat().st_size<min_bytes:raise ValueError('模型文件不完整')
                tmp.replace(path)
            except Exception as exc:
                tmp.unlink(missing_ok=True)
                raise VisualResponseFormatError('本地人物模型准备失败：'+str(exc)) from exc
        result.append(path)
    return result


def _local_identity_verdict(reference,frames,speaker):
    """Compare every detected face with the authority portrait using CPU SFace."""
    import cv2
    detector_path,recognizer_path=_local_face_models()
    detector=cv2.FaceDetectorYN.create(str(detector_path),'',(320,320),score_threshold=.80,
                                       nms_threshold=.3,top_k=5000)
    recognizer=cv2.FaceRecognizerSF.create(str(recognizer_path),'')

    def features(path):
        image=cv2.imread(str(path))
        if image is None:return []
        h,w=image.shape[:2];detector.setInputSize((w,h))
        _ok,found=detector.detect(image)
        if found is None:return []
        rows=sorted(found,key=lambda x:float(x[2]*x[3]),reverse=True)
        result=[]
        for face in rows:
            try:
                result.append(recognizer.feature(recognizer.alignCrop(image,face)))
            except cv2.error:
                continue
        return result

    refs=features(reference)
    if not refs:
        raise VisualResponseFormatError('本地人物核验无法从参考图提取人脸')
    reference_feature=refs[0]
    same=[];different=[];uncertain=[];scores={}
    for index,path in enumerate(frames,1):
        candidates=features(path)
        if not candidates:
            uncertain.append(index);continue
        score=max(float(recognizer.match(reference_feature,x,cv2.FaceRecognizerSF_FR_COSINE))
                  for x in candidates)
        scores[index]=round(score,4)
        (same if score>=LOCAL_FACE_COSINE_THRESHOLD else different).append(index)
    best=max(same,key=lambda i:scores[i]) if same else None
    decisive=[scores[i] for i in same]
    confidence=(min(.99,.75+max(0,(sum(decisive)/len(decisive)-LOCAL_FACE_COSINE_THRESHOLD))*1.5)
                if decisive else 0.0)
    return dict(same_person_frames=same,different_person_frames=different,
                uncertain_frames=uncertain,best_cover_frame=best,
                confidence=round(confidence,3),watermark_texts=[],
                confidence_scope='matched_frames_only',match_fraction=len(same)/max(1,len(frames)),
                reason=f'CPU SFace逐帧比对；匹配{len(same)}/{len(frames)}帧；confidence仅针对匹配帧，并非整段身份概率；阈值{LOCAL_FACE_COSINE_THRESHOLD}；分数{scores}',
                engine='opencv_yunet_sface_cpu')


def _call_identity_vlm(reference, frames, speaker, api_key):
    """把权威参考照和源片多帧一起交给 VLM 做目标人物在场核验。"""
    content = [
        {"type": "text", "text": f"参考图：已确认是目标人物【{speaker}】本人。"},
        {"type": "image_url", "image_url": {"url": _image_data_url(identity_face_reference(reference))}},
    ]
    for i, fp in enumerate(frames, 1):
        content.extend([
            {"type": "text", "text": f"待检视频帧 {i}"},
            {"type": "image_url", "image_url": {"url": _image_data_url(fp)}},
        ])
    content.append({
        "type": "text",
        "text": (
            "请严格比较脸部身份，不要根据视频标题、字幕、财经话题或‘谁在讲话’猜测。"
            "衣服、背景、拍摄年份或表情不同不能作为不同人的依据；只比较脸部特征。"
            f"任务是逐帧判断参考图中的{speaker}本人是否出现在画面任意位置。"
            "一帧可能同时出现主持人、嘉宾或多人：只要目标人物也在场，即归入"
            " same_person_frames，绝不能因为另一个人更大、更居中或正在说话而归入"
            " different_person_frames。只有清楚看到人脸、且能确认目标人物完全不在画面中，"
            "才归入 different_person_frames；遮挡、侧脸过小或看不清则归为 uncertain。"
            "同时记录屏幕叠加的外部账号/平台角标；无文字的彩色图形台标也须记录为[图形台标]，"
            "不要把真实场景里的字画、衣服文字或物品当叠加水印。"
            f"必须逐一分类全部 {len(frames)} 帧，三组索引合起来恰好是 1 到 {len(frames)}，不重不漏。"
            "看不清的帧放 uncertain_frames，不能省略；空组返回空数组。"
            "图表、字幕、空镜或没有可辨认人脸的帧也必须放入uncertain_frames，不能漏填这些帧。"
            "只返回一个JSON对象。字段说明（不是待复制的答案）："
            "reason：逐帧写出实际看见的依据；same_person_frames：确认目标在场的整数帧号数组；"
            "different_person_frames：确认目标不在场的整数帧号数组；"
            "uncertain_frames：确实无法确认的整数帧号数组；"
            "best_cover_frame：实际最清晰的已确认帧号，没有则null；"
            "confidence：根据实际比较给出0到1之间的数字；"
            "watermark_texts：实际看见的叠加水印文字数组。"
            "不得原样复述字段说明，不得把全部帧留空。"
        ),
    })
    messages=[{"role":"user","content":content}]
    last = None
    for attempt in range(3):
        raw_response=None
        try:
            payload=json.dumps({"model":VISION_MODEL,"messages":messages,
                "max_tokens":1200,"temperature":0.0,"stream":False}).encode()
            req=urllib.request.Request(SF_URL,data=payload,headers={
                "Authorization":f"Bearer {api_key}","Content-Type":"application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode())
            raw_response=data["choices"][0]["message"]["content"]
            if frames and Path(frames[0]).exists():
                trace=Path(frames[0]).parent/'visual_response_attempts.jsonl'
                with trace.open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(dict(frame_count=len(frames),attempt=attempt+1,
                        response=raw_response),ensure_ascii=False)+'\n')
            verdict = _parse_json_object(raw_response)
            classified=[]
            for field in ('same_person_frames','different_person_frames','uncertain_frames'):
                values=verdict.get(field)
                if not isinstance(values,list) or any(type(x) is not int for x in values):
                    raise ValueError('人物核验未返回逐帧索引数组')
                classified.extend(values)
            if sorted(classified)!=list(range(1,len(frames)+1)):
                raise ValueError('人物核验遗漏或重复帧，不能把格式示例当作实际核验')
            return verdict
        except Exception as e:
            last = e
            if raw_response is not None and isinstance(e,(ValueError,TypeError,KeyError)):
                messages.extend([{"role":"assistant","content":raw_response},
                    {"role":"user","content":f"上次格式未通过：{e}。请重新逐一核验原图中编号1到{len(frames)}的所有帧，"
                     "每个编号必须且只能出现在一个组中。空数组不可代替遗漏的判断，不允许猜测；看不清放uncertain_frames。"
                     "只输出完整JSON，保留实际判断。"}])
            if attempt < 2:
                time.sleep(2 ** attempt)
    if isinstance(last, (ValueError, TypeError, KeyError)):
        raise VisualResponseFormatError(f"人物 VLM 校验不可用：{last}")
    raise VisualQualityError(f"人物 VLM 校验不可用：{last}")


def verify_source_identity(src, work, speaker, api_key):
    """在 ASR 前确认整片主角确实是指定人物，并返回可用封面帧时间。"""
    reference = _download_speaker_reference(speaker, work)
    frames, times = _sample_visual_frames(src, work)
    verdict = _local_identity_verdict(reference,frames,speaker)
    if not identity_verdict_passes(verdict, len(frames)):
        raise VisualQualityError(
            f"人物不一致或无法确认：{speaker}；"
            f"same={verdict.get('same_person_frames', [])}，"
            f"different={verdict.get('different_person_frames', [])}，"
            f"confidence={verdict.get('confidence', 0)}，"
            f"reason={verdict.get('reason', '')}")
    same = [i for i in verdict.get("same_person_frames", [])
            if isinstance(i, int) and 1 <= i <= len(times)]
    best = verdict.get("best_cover_frame")
    if best not in same:
        best = same[0]
    report = {
        "version": VISUAL_GATE_VERSION,
        "model": "opencv_yunet_sface_cpu",
        "speaker": speaker,
        "same_person_frames": same,
        "different_person_frames": verdict.get("different_person_frames", []),
        "confidence": verdict.get("confidence", 0),
        "reason": verdict.get("reason", ""),
        "watermark_texts": verdict.get("watermark_texts", []),
        "best_cover_time": round(times[best - 1], 2),
    }
    (work / "source_identity.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[人物] ✓ 多帧确认是{speaker}本人：{len(same)}/{len(frames)}，"
          f"置信度 {float(report['confidence']):.0%}")
    return report


def llm(messages, api_key, temperature=0.3, max_tokens=2000, budget_sec=None,
        response_schema=None):
    """Use a loopback Ollama model by default; cloud requires explicit opt-in."""
    cache_dir = BASE / ".llm_cache"
    cache_dir.mkdir(exist_ok=True)
    ckey = hashlib.sha256(json.dumps(
        {"backend":TEXT_BACKEND,"model":LOCAL_LLM_MODEL if TEXT_BACKEND=='local' else MODELS,
         "runtime_version":3,"m":messages,"t":temperature,"mt":max_tokens,"schema":response_schema},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cf = cache_dir / f"{ckey}.json"
    if cf.exists():
        try:
            out = json.loads(cf.read_text(encoding="utf-8"))["content"]
            print("[llm-cache] 命中,不发请求")
            return out
        except (ValueError, KeyError, OSError):
            print("[llm-cache] 缓存损坏,重新请求", file=sys.stderr)

    deadline = time.monotonic() + (budget_sec if budget_sec is not None else text_budget(120))
    if TEXT_BACKEND == 'local':
        if os.environ.get('TEXT_RUNTIME_UNAVAILABLE')=='true':
            raise LocalTextUnavailable('本地文本模型未就绪；原文规则路径可继续，需要模型的部分保留转写后重试')
        from urllib.parse import urlparse
        parsed=urlparse(LOCAL_LLM_URL)
        if parsed.scheme!='http' or parsed.hostname not in {'127.0.0.1','localhost','::1'}:
            raise RuntimeError('LOCAL_LLM_URL 只允许本机回环地址，防止误用收费接口')
        remaining=max(1,deadline-time.monotonic())
        payload=json.dumps({'model':LOCAL_LLM_MODEL,'messages':messages,'stream':False,
                            'think':False,'format':response_schema or 'json','keep_alive':'24h',
                            # Respect each caller's bounded output budget.
                            # A shared 640-token cap truncated real editorial
                            # reports long before their wall-clock deadline.
                            'options':{'temperature':temperature,
                                       'num_ctx':16384,
                                       'num_predict':max_tokens}}).encode()
        started = time.monotonic()
        print(f'[local-llm] model={LOCAL_LLM_MODEL} chars={sum(len(m.get("content", "")) for m in messages)} budget={remaining:.0f}s ctx=16384', flush=True)
        try:
            request=urllib.request.Request(LOCAL_LLM_URL,data=payload,
                headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request,timeout=min(600,remaining)) as response:
                data=json.loads(response.read().decode())
            metrics={k:data.get(k) for k in ('prompt_eval_count','prompt_eval_duration',
                     'eval_count','eval_duration','load_duration','done_reason')}
            metrics['wall_seconds']=round(time.monotonic()-started,2)
            print('[local-llm] '+json.dumps(metrics), flush=True)
            if data.get('done_reason') == 'length':
                raise ValueError('本地模型输出达到长度上限，审核未完成')
            txt=str((data.get('message') or {}).get('content') or '').strip()
            if not txt:raise ValueError('本地模型返回空内容')
            txt=re.sub(r"<think>.*?</think>","",txt,flags=re.S).strip()
            cf.write_text(json.dumps({'model':LOCAL_LLM_MODEL,'backend':'local','content':txt},
                                     ensure_ascii=False),encoding='utf-8')
            return txt
        except Exception as exc:
            raise LocalTextUnavailable(
                f'本地文本推理未完成：{LOCAL_LLM_MODEL}，耗时{time.monotonic()-started:.1f}秒，'
                f'预算{remaining:.0f}秒，{type(exc).__name__}: {exc}；保留ASR并重试，不判素材不合格') from exc
    if TEXT_BACKEND != 'siliconflow':
        raise RuntimeError('TEXT_BACKEND 只能是 local；恢复收费云端须显式设为 siliconflow')
    if not api_key:
        raise RuntimeError('显式云端模式缺少 SILICONFLOW_API_KEY')
    last = None
    for model in MODELS:
        payload = json.dumps({
            "model": model, "messages": messages, "temperature": temperature,
            "max_tokens": max_tokens, "stream": False, "enable_thinking": False,
        }).encode()
        req = urllib.request.Request(
            SF_URL, data=payload,
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"})
        for attempt in range(3):
            remaining = deadline - time.monotonic() if deadline else 120
            if remaining <= 0:
                raise RuntimeError("LLM 调用超出本片时间预算")
            try:
                with urllib.request.urlopen(req, timeout=min(120, remaining)) as r:
                    d = json.loads(r.read().decode())
                txt = d["choices"][0]["message"]["content"]
                txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()
                cf.write_text(json.dumps({"model": model, "content": txt},
                                         ensure_ascii=False), encoding="utf-8")
                return txt
            except urllib.error.HTTPError as e:
                last = f"{model} HTTP {e.code}"
                if e.code in (400, 401, 402, 403):
                    break                      # 换模型也救不了,直接下一个
                time.sleep(2 * (attempt + 1))
            except Exception as e:
                last = f"{model} {type(e).__name__}"
                time.sleep(2 * (attempt + 1))
        print(f"[llm] {model} 不可用({last}),降级", file=sys.stderr)
    raise RuntimeError(f"全部模型不可用,最后错误:{last}")


def fix_terms(text):
    for wrong, right in GLOSSARY.items():
        text = text.replace(wrong, right)
    return text


def _llm_smooth_cues(cues, api_key=None):
    """Historical entry point: ASR text cleanup is now offline and lossless.

    A text-only model cannot verify spoken numbers, negations or names. Preserve
    decoded text instead of requesting an API rewrite and trusting similarity.
    """
    return [dict(c) for c in cues]


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _asr_cache_identity(src, work=None):
    if ASR_BACKEND == 'qwen3':
        source_sha = _sha256_file(src)
        explicit=os.environ.get('QWEN3_EVIDENCE_DIR')
        evidence = Path(explicit) if explicit else (Path(work)/'qwen_cpu' if work else None)
        reports = []
        if evidence is not None and evidence.is_dir():
            from qwen_asr_evidence import load_reports
            reports = load_reports(evidence)
        if reports:
            config = json.loads((BASE / 'asr_production_config.json').read_text())
            choice = {**config, **config.get('source_overrides', {}).get(source_sha, {})}
            revisions = choice.get('model_revisions') or {}
            for report in reports:
                alignment = report.get('alignment') or {}
                if (report.get('source_video_sha256') != source_sha
                        or report.get('device') != 'cpu'
                        or report.get('networking_during_inference') is not False
                        or report.get('model_id') != 'Qwen/Qwen3-ASR-0.6B'
                        or report.get('model_revision') != revisions.get('asr')
                        or alignment.get('device') != 'cpu'
                        or alignment.get('networking_during_inference') is not False
                        or alignment.get('model_id') != 'Qwen/Qwen3-ForcedAligner-0.6B'
                        or alignment.get('model_revision') != revisions.get('aligner')):
                    raise ValueError('离线转写证据与当前母片或固定模型版本不匹配')
            files = sorted(evidence.rglob('aligned.json'))
            return dict(version=ASR_PIPELINE_VERSION, source_sha256=source_sha,
                backend='qwen3', chunk_sec=30, overlap_sec=3, threads=2,
                reviewed_corrections_sha256=_sha256_file(BASE/'reviewed_asr_corrections.py'),
                evidence_sha256=sorted(_sha256_file(path) for path in files),
                model_revisions=revisions)
        paths=[Path(os.environ.get(name,'')) for name in ('QWEN3_ASR_DIR','QWEN3_ALIGNER_DIR')]
        if any(not p.is_dir() or not list(p.glob('*.safetensors')) for p in paths):
            raise ValueError('缺少已验证的CPU离线证据，且未下载Qwen识别或对齐权重')
        files=[f for p in paths for pattern in ('*.safetensors','*.json') for f in p.glob(pattern)]
        return dict(version=ASR_PIPELINE_VERSION,source_sha256=source_sha,
            backend='qwen3',chunk_sec=30,overlap_sec=3,threads=2,
            reviewed_corrections_sha256=_sha256_file(BASE/'reviewed_asr_corrections.py'),
            models={str(p.resolve()):_sha256_file(p) for p in files})
    model_dir = Path(SENSEVOICE_DIR if ASR_BACKEND == "sensevoice" else FUNASR_DIR)
    model_files = sorted(model_dir.rglob("*.onnx")) + sorted(model_dir.rglob("tokens.txt"))
    return dict(version=ASR_PIPELINE_VERSION, source_sha256=_sha256_file(src),
                backend=ASR_BACKEND, chunk_sec=ASR_CHUNK_SEC, overlap_sec=ASR_OVERLAP_SEC,
                threads=ASR_CPU_THREADS, glossary=GLOSSARY,
                models={str(p.resolve()): _sha256_file(p) for p in model_files})


def _audio_duration(src):
    """音频时长（秒），ffprobe 取；失败返回 0。"""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(src)],
                           capture_output=True, text=True, timeout=60)
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def transcribe(src, work, api_key=None):
    """Offline CPU ASR with source/model/config-bound cache and quality gates."""
    if ASR_BACKEND not in ("sensevoice", "funasr", "qwen3"):
        raise ValueError(f"中文生产不支持 ASR_BACKEND={ASR_BACKEND!r}；只允许已验证的 CPU 离线后端")
    src, work = Path(src), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    cache, provenance = work / "cues_raw.json", work / "asr_cache.json"
    identity = _asr_cache_identity(src,work)
    meta={}
    if cache.exists() and provenance.exists():
        try:
            meta = json.loads(provenance.read_text(encoding="utf-8"))
            if meta.get("identity") == identity and meta.get("cues_sha256") == _sha256_file(cache):
                cues = json.loads(cache.read_text(encoding="utf-8"))
                _asr_quality_gate(cues, _audio_duration(src))
                print("[asr] 命中经过来源、模型及配置校验的离线缓存")
                return cues
        except (ValueError, OSError, TypeError, KeyError):
            pass
    # Old cache files have no provenance; do not silently trust a previous model
    # or reuse PCM extracted from a different video at the same work directory.
    stale = set()
    for pattern in ("cues_raw.json", "asr_tokens.json", "asr_raw_chunks.json",
                    "highlights*.json", "copywrite*.json", "translation.json",
                    "chunks_dedup.json", "semantic*.json", "editorial_review*.json", "qwen_cpu"):
        if pattern=='qwen_cpu' and identity.get('evidence_sha256'):
            continue  # Immutable source/model-bound alignment is not a cue cache.
        if (pattern in ('highlights*.json','copywrite*.json')
                and meta.get('identity',{}).get('source_sha256')==identity['source_sha256']):
            continue  # Both caches independently bind the regenerated transcript.
        stale.update(work.glob(pattern))
    if stale:
        archive = work / "asr_stale" / str(time.time_ns())
        archive.mkdir(parents=True)
        for path in stale:
            path.replace(archive / path.name)
    provenance.unlink(missing_ok=True)
    wav = work / "audio_16k.wav"
    if wav.resolve() != src.resolve():
        wav.unlink(missing_ok=True)
    if ASR_BACKEND == "sensevoice":
        cues = _transcribe_sensevoice(src, work)
    elif ASR_BACKEND == 'qwen3':
        cues = _transcribe_qwen_cpu(src,work)
    else:
        cues = _transcribe_funasr(src, work)
    _asr_quality_gate(cues, _audio_duration(src))
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    if ASR_BACKEND=='qwen3':identity=_asr_cache_identity(src,work)
    tmp = provenance.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(identity=identity, cues_sha256=_sha256_file(cache)),
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(provenance)
    return cues


def _transcribe_qwen_cpu(src,work):
    """Explicit CPU backend; no ASR API or automatic model fallback."""
    import wave
    from qwen_asr_evidence import validated_words,load_reports,punctuated_words
    from reviewed_asr_corrections import apply_reviewed_corrections
    wav=work/'audio_16k.wav'
    if wav.resolve()!=src.resolve():
        subprocess.run(['ffmpeg','-y','-v','error','-i',str(src),'-vn','-ar','16000',
            '-ac','1','-c:a','pcm_s16le',str(wav)],check=True,timeout=600)
    with wave.open(str(wav)) as stream:
        duration=stream.getnframes()/stream.getframerate()
        pcm_sha=hashlib.sha256(stream.readframes(stream.getnframes())).hexdigest()
    video_sha=_sha256_file(src)
    evidence=Path(os.environ.get('QWEN3_EVIDENCE_DIR') or work/'qwen_cpu')
    reports=load_reports(evidence)
    if not reports:
        evidence.mkdir(parents=True,exist_ok=True)
        for mode,env in [('decode','QWEN3_ASR_DIR'),('align','QWEN3_ALIGNER_DIR')]:
            subprocess.run([sys.executable,str(BASE/'qwen_cpu_transcript.py'),mode,
                '--audio',str(wav),'--out',str(evidence),'--weights',os.environ[env],
                '--source-video-sha',video_sha],check=True,timeout=max(600,int(duration*4)))
        reports=load_reports(evidence)
    config=json.loads((BASE/'asr_production_config.json').read_text())
    choice={**config,**config.get('source_overrides',{}).get(video_sha,{})}
    revisions=choice.get('model_revisions') or {}
    if any(r.get('model_revision')!=revisions.get('asr')
           or (r.get('alignment') or {}).get('model_revision')!=revisions.get('aligner')
           for r in reports):
        raise ValueError('离线转写缓存与当前识别/对齐权重版本不同')
    words=validated_words(reports,pcm_sha,video_sha,duration)
    (work/'asr_raw_chunks.json').write_text(json.dumps(reports,ensure_ascii=False,indent=1))
    tokens=[w['text'] for w in words];times=[w['start'] for w in words]
    (work/'asr_tokens.json').write_text(json.dumps(dict(tokens=tokens,timestamps=times,
        duration=duration,backend='qwen3',alignment='Qwen3-ForcedAligner-0.6B'),ensure_ascii=False))
    timed=punctuated_words(reports,words)
    corrected,changes=apply_reviewed_corrections(timed,video_sha)
    (work/'asr_reviewed_corrections.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2))
    return _merge_cues(_funasr_tokens_to_cues(
        [w['text'] for w in corrected],[w['start'] for w in corrected],0,duration,
        end_timestamps=[w['end'] for w in corrected]))


def _transcribe_whisper(src, work, api_key):
    """large-v3 + 词级时间戳,按标点和字数重新组句。"""
    from faster_whisper import WhisperModel
    cache = work / "cues_raw.json"

    print(f"[asr] 加载 {WHISPER}")
    model = WhisperModel(WHISPER, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(
        str(src), language="zh", word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400})

    words = []
    for seg in segments:
        if seg.words:
            words.extend(seg.words)
        print(f"  [{seg.start:7.1f}s] {seg.text.strip()[:46]}", flush=True)

    # 优先 LLM 断句（理解语义），失败/不可用回退规则断句
    cues = _llm_punctuate_and_cues(words, api_key, work)
    if cues is None:
        cues = _group_tokens_to_cues(words)

    cues = _merge_cues(cues)
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[asr] {len(words)} 词 → {len(cues)} 条字幕")
    return cues


def _merge_cues(cues):
    """合并间距太小的帧(防止字幕闪烁);拼接时补分隔符避免文字粘连。
    关键约束：合并后长度不超过 MAX_CHARS+2，否则连续说话时硬切的长段
    会被合并回超长条，导致「一页十几行字幕」(2026-08-23 事故根因)。"""
    merged = []
    for c in cues:
        # 合并三重约束：间距近 + 合并后字数不超 + 合并后时长不超。
        # （2026-09-01 修复：原来只卡字数不卡时长，会把硬切开的 6s 条又粘成 12s 条）
        if merged and c["start"] - merged[-1]["end"] < MIN_GAP and \
           len(merged[-1]["text"]) + len(c["text"]) + 1 <= MAX_CHARS + 2 and \
           c["end"] - merged[-1]["start"] <= MAX_CUE_SEC:
            merged[-1]["end"] = c["end"]
            sep = "" if (not merged[-1]["text"] or merged[-1]["text"][-1] in BREAK) else "，"
            merged[-1]["text"] += sep + c["text"]
        else:
            merged.append(dict(c))
    return [c for c in merged if c["text"]]


def _transcribe_funasr(src, work):
    """Fun-ASR-Nano LLM 版转写：自带分词+标点+每个 token 的时间戳。

    相对 whisper 的优势：
    1. 中文 CER ~4.55%（whisper ~20%），远更准。
    2. LLM 架构天然输出标点，省掉 LLM 加标点这一步。
    3. 支持热词（财经专名如「林园、片仔癀、茅台」）。
    4. 输出是语义分词（`茅台`/`都是`）非单字，断句不会切碎词。

    时间戳策略：sherpa-onnx 的 Fun-ASR-Nano 返回 tokens（含标点）+
    timestamps（每个 token 的开始时间，毫秒间隔均匀）。token[i] 的
    end 取 token[i+1] 的 start，末 token 取段尾。
    """
    import numpy as np
    import wave
    from sherpa_onnx import OfflineRecognizer
    cache = work / "cues_raw.json"

    # 1. 提取 16k 单声道 PCM（Fun-ASR-Nano 要求的输入格式）
    wav_path = work / "audio_16k.wav"
    if not wav_path.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                        str(wav_path)], check=True)
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        samples = w.readframes(w.getnframes())
    audio = np.frombuffer(samples, dtype=np.int16).astype(np.float32) / 32768.0
    total_dur = len(audio) / sr
    print(f"[asr] 音频 {total_dur:.0f}s，Fun-ASR-Nano 转写")

    # 2. 加载模型（int8 三件套 + tokenizer）；llm 优先 max_token_1024（长 chunk），回退官方 512 版
    llm_path = f"{FUNASR_DIR}/llm_int8_max_token_1024/llm.int8.onnx"
    if not Path(llm_path).exists():
        llm_path = f"{FUNASR_DIR}/llm.int8.onnx"
    recognizer = OfflineRecognizer.from_funasr_nano(
        encoder_adaptor=f"{FUNASR_DIR}/encoder_adaptor.int8.onnx",
        llm=llm_path,
        embedding=f"{FUNASR_DIR}/embedding.int8.onnx",
        tokenizer=f"{FUNASR_DIR}/Qwen3-0.6B",
        num_threads=1, itn=True, temperature=0.7, max_new_tokens=150,
    )

    # 3. 分 chunk 转写（Fun-ASR-Nano 有 KV 上限，长音频需切段）
    #    temperature=0.7：批量实证死循环阈值——0.3/0.5 对含口吃/BGM 片段仍会死循环，
    #    0.7 死循环消失且专名识别尚可（>0.9 会把「林园」误识别成「李彦忠」）。
    #    max_new_tokens=150 是 20s 音频正常输出(~60 token)的 2.5 倍余量，死循环也早截断。
    #    末尾还有 _de_loop_text 截断 + _dedup_consecutive 去重兜底。
    #    overlap 缓冲：每段前后多转写 OVERLAP 秒，让跨边界的词被完整看到，
    #    但只输出核心区 [t, t+CHUNK_SEC] 的 cue（首尾 overlap 仅当上下文）。
    #    这样边界词（如「多得多」）在前一段能被完整识别输出，不会切成半词。
    #    CHUNK_SEC=20：兼容官方 512 KV 版（~20s=334 audio tokens < 512 上限）；
    #    若有 max_token_1024 版，20s 段更是绰绰有余。
    CHUNK_SEC = 20.0
    OVERLAP = 3.0
    cues = []
    t = 0.0
    while t < total_dur:
        seg_start = max(0.0, t - OVERLAP)
        seg_end = min(t + CHUNK_SEC + OVERLAP, total_dur)
        seg = audio[int(seg_start * sr):int(seg_end * sr)]
        stream = recognizer.create_stream()
        stream.accept_waveform(sr, seg)
        recognizer.decode_stream(stream)
        r = stream.result
        if r.tokens:
            seg_cues = _funasr_tokens_to_cues(
                list(r.tokens), list(r.timestamps), seg_start, seg_end - seg_start)
            for c in seg_cues:
                if t <= c["start"] < t + CHUNK_SEC:
                    cues.append(c)
        print(f"  [chunk {seg_start:6.0f}-{seg_end:6.0f}s] {len(r.tokens)} tokens", flush=True)
        t += CHUNK_SEC

    cues = _merge_cues(cues)
    # 去重 + 截断死循环：Fun-ASR-Nano 的 LLM 偶发死循环重复、ASR 重复识别，
    # 必须在这里清理（2026-08-27 实拍：B站二创字幕「两个两个…」整屏、
    # 「对吧？」×13 条）。顺序：先单条截死循环，再去相邻重复，最后纠错。
    for c in cues:
        c["text"] = _de_loop_text(c["text"])
    cues = _dedup_consecutive(cues)
    # 最后一道 GLOSSARY 专名纠错（Fun-ASR-Nano 对专名仍有盲区）
    for c in cues:
        c["text"] = fix_terms(c["text"])
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[asr] {len(cues)} 条字幕")
    return cues


def _owned_sensevoice_tokens(chunks):
    """Assign timestamped tokens before grouping, so a cue crossing a chunk
    boundary cannot cause the following part of a sentence to disappear.
    Keep raw chunk hypotheses separately for auditable overlap disagreements.
    """
    tokens, timestamps = [], []
    for chunk in chunks:
        if len(chunk["tokens"]) != len(chunk["timestamps"]):
            raise ValueError("ASR token 与时间戳数量不一致，不能伪造时间轴")
        for token, relative in zip(chunk["tokens"], chunk["timestamps"]):
            absolute = chunk["offset"] + float(relative)
            if not (0 <= relative <= chunk["duration"]):
                raise ValueError("ASR token 时间戳超出音频片段")
            if chunk["core_start"] <= absolute < chunk["core_end"]:
                if timestamps and absolute < timestamps[-1]:
                    raise ValueError("ASR token 时间戳逆序")
                tokens.append(token)
                timestamps.append(absolute)
    return tokens, timestamps


def _transcribe_sensevoice(src, work, api_key=None):
    """CPU-only SenseVoice; retain raw hypotheses, never rewrite through an API."""
    import numpy as np
    import wave
    from sherpa_onnx import OfflineRecognizer
    wav_path = work / "audio_16k.wav"
    if not wav_path.exists():
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                        "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                        str(wav_path)], check=True, timeout=600)
    with wave.open(str(wav_path), "rb") as w:
        sr = w.getframerate()
        samples = w.readframes(w.getnframes())
    audio = np.frombuffer(samples, dtype=np.int16).astype(np.float32) / 32768.0
    total_dur = len(audio) / sr
    print(f"[asr] 音频 {total_dur:.0f}s，SenseVoice CPU 离线转写")
    recognizer = OfflineRecognizer.from_sense_voice(
        model=f"{SENSEVOICE_DIR}/model.int8.onnx", tokens=f"{SENSEVOICE_DIR}/tokens.txt",
        num_threads=ASR_CPU_THREADS, use_itn=True, provider="cpu")
    chunks, t = [], 0.0
    while t < total_dur:
        start = max(0.0, t - ASR_OVERLAP_SEC)
        end = min(t + ASR_CHUNK_SEC + ASR_OVERLAP_SEC, total_dur)
        stream = recognizer.create_stream()
        stream.accept_waveform(sr, audio[int(start * sr):int(end * sr)])
        recognizer.decode_stream(stream)
        r = stream.result
        chunks.append(dict(offset=start, duration=end-start, core_start=t,
            core_end=min(t+ASR_CHUNK_SEC, total_dur), text=r.text,
            tokens=list(r.tokens), timestamps=list(r.timestamps)))
        # Checkpoint every chunk; a later failure must not erase the evidence.
        (work / "asr_raw_chunks.json").write_text(
            json.dumps(chunks, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  [chunk {start:6.0f}-{end:6.0f}s] {len(r.tokens)} tokens", flush=True)
        t += ASR_CHUNK_SEC
    tokens, timestamps = _owned_sensevoice_tokens(chunks)
    (work / "asr_tokens.json").write_text(json.dumps(dict(tokens=tokens,
        timestamps=timestamps, duration=total_dur), ensure_ascii=False), encoding="utf-8")
    cues = _funasr_tokens_to_cues(tokens, timestamps, 0.0, total_dur)
    cues = _merge_cues(cues)
    # Non-autoregressive decoding cannot enter an autoregressive loop. Real
    # hesitations and repeated statements must not be deleted by loop heuristics.
    for c in cues:
        c["text"] = fix_terms(c["text"])
    _asr_quality_gate(cues, total_dur)
    print(f"[asr] {len(cues)} 条字幕，原始 token 与识别结果已留存")
    return cues


def _asr_quality_gate(cues, audio_sec):
    """ASR 质量闸门：识别密度过低 → 放弃出片。

    2026-09-01：片仔癀股东大会现场收音（450s）只转出 328 字＝0.73 字/秒，
    成片字幕全是「隔一牛」「片仔大吃偏小比伊利」这类乱码，标题也跟着胡说。
    对照 8 条正常成片：2.1~8.1 字/秒（多数 4~5）。阈值 1.2 留足余量。
    """
    chars = sum(len(c["text"]) for c in cues)
    inner = sum(max(0.0, c["end"] - c["start"]) for c in cues)
    rate = chars / inner if inner > 0 else 0          # 条内语速（主）
    density = chars / audio_sec if audio_sec > 0 else 0  # 整段密度（副）
    print(f"[质检] 条内语速 {rate:.2f} 字/秒（阈值 {ASR_MIN_SPEECH_RATE}）；"
          f"整段密度 {density:.2f} 字/秒（阈值 {ASR_MIN_DENSITY}）；"
          f"{chars} 字 / {len(cues)} 条 / 音频 {audio_sec:.0f}s")
    if not cues:
        raise RuntimeError("ASR 质量不合格：没有识别出任何字幕，放弃出片")
    if rate < ASR_MIN_SPEECH_RATE and density < ASR_MIN_DENSITY:
        raise RuntimeError(
            f"ASR 质量不合格：条内语速 {rate:.2f}（阈值 {ASR_MIN_SPEECH_RATE}）"
            f"且整段密度 {density:.2f}（阈值 {ASR_MIN_DENSITY}）双双偏低。"
            f"音频可能是现场嘈杂/口音重/削顶失真，字幕大概率是乱码，放弃出片")


def _funasr_tokens_to_cues(tokens, timestamps, offset, chunk_dur,
                           end_timestamps=None):
    """Fun-ASR-Nano 的 tokens（含标点）+ timestamps → 字幕 cues。

    断句规则：句末标点（。！？）必断；逗号仅在接近 MAX_CHARS 时断；
    标点不进 buf（避免「吗？」「。」单独成条），end 取末 token 的 end。
    尊重 LLM 的句号边界（语义完整）优先于硬切字数。
    """
    cues, buf, buf_text = [], [], ""
    def _flush():
        nonlocal buf, buf_text
        if buf:
            if buf_text:
                cues.append({"start": round(offset + buf[0][1], 2),
                             "end": round(offset + buf[-1][2], 2),
                             "text": buf_text})
        buf, buf_text = [], ""
    for i, tok in enumerate(tokens):
        st = timestamps[i]
        if end_timestamps is not None:
            # Qwen's reviewed multi-character repairs carry the immutable
            # original phrase interval. Do not discard that evidence or
            # compress an entire corrected sentence into a one-second token.
            en = end_timestamps[i]
        else:
            en = timestamps[i + 1] if i + 1 < len(timestamps) else chunk_dur
            en = min(en, st + MAX_TOKEN_SEC)  # 无显式词尾时防止跨静音拖长
        if tok in "。！？!?":
            if buf:
                buf_text += tok
                _flush()
            elif cues:
                # A length flush may have just emitted the preceding word.
                # Keep its sentence boundary instead of discarding punctuation.
                cues[-1]["text"] += tok
        elif tok in "，、；：,;:":
            if buf:
                buf_text += tok
                if len(buf_text) >= MAX_CHARS - 4:
                    _flush()
            elif cues:
                cues[-1]["text"] += tok
        else:
            # 静音断句：与上一个 token 间隔过大说明中间是静音/没识别出来，
            # 不能把它们塞进同一条字幕（否则字幕横跨十几秒静音，2026-09-01 实测）
            if buf and st - buf[-1][2] > MAX_GAP_SEC:
                _flush()
            buf.append((tok, st, en))
            buf_text += tok
            # 兜底硬断：ASR 没吐标点时，普通字符也必须受字数/时长上限约束，
            # 否则整个 chunk 会挤成一条 60+ 字、跨 50 多秒的字幕（2026-09-01 实测 bug）
            if len(buf_text) >= MAX_CHARS or (buf and (en - buf[0][1]) >= MAX_CUE_SEC):
                _flush()
    _flush()
    return cues


def _de_loop_text(text):
    """截断单条字幕内的 LLM 死循环重复（如「两个两个两个…」「谁谁谁谁…」）。

    Fun-ASR-Nano 是 LLM 架构，temperature 偏低时偶发贪婪解码死循环，
    输出「方法跟你的方法跟你的…」这类无限重复。这里用正则找 1-8 字单元
    连续重复 4 次以上的片段，截断到保留 2 次（容忍真实口吃的一次重复）。
    """
    if not text:
        return text
    return re.sub(r"(.{1,8}?)\1{3,}", lambda match: match.group(1) * 2, text)


def _dedup_consecutive(cues, sim=0.9):
    """Only merge exact duplicates covering overlapping audio.

    Similar sentences can differ in price, date or negation. Consecutive spoken
    repetition at different times is also meaningful and must remain intact.
    """
    out = []
    for c in cues:
        if out and c["text"] == out[-1]["text"] and c["start"] < out[-1]["end"]:
            out[-1]["end"] = max(c["end"], out[-1]["end"])
        else:
            out.append(dict(c))
    return out


def _llm_clean_text(raw_text, api_key):
    """给一段 ASR 文本 LLM 整理（加标点 + 修正明显错误）。
    返回整理后文本（含标点），失败返回 None。"""
    prompt = ("下面是一段语音识别出的中文，可能有识别错误（重复的字、错字、语序颠倒）。"
              "请把它整理成通顺的中文并加上标点（，。！？）。要求："
              "1) 忠实原意，不增删观点、不补充原文没有的内容；"
              "2) 只修正明显的识别错误（如重复的字、明显错字、明显语序颠倒）；"
              "3) 只输出整理后的文字，不要解释、不要加空格换行。\n\n" + raw_text)
    try:
        out = llm([{"role": "user", "content": prompt}], api_key,
                  temperature=0.0, max_tokens=len(raw_text) + 300)
    except Exception as e:
        print(f"[断句] LLM 不可用: {e}", file=sys.stderr)
        return None
    out = re.sub(r"```.*?```", "", out, flags=re.S).strip()
    return re.sub(r"\s+", "", out)


def _llm_punctuate_and_cues(words, api_key, work):
    """LLM 整理字幕：加标点 + 修正明显识别错误，再断句。
    返回 cues；失败/差异过大/太短返回 None（回退规则断句）。

    2026-08-26 升级：
    1. 之前只让 LLM 加标点（不改字），但 ASR 有重复/语序错乱，且校验太严
       稍改字就回退 → 规则硬切无标点。现在让 LLM 顺手修正明显错误，
       用 difflib 对齐时间戳，只要求相似度 > 0.80。
    2. 长文本分片：一次调用输出 token 超 8K 会失败（42 分钟视频 ASR 上万字），
       故每片 ~2500 字独立整理再拼接。
    """
    if not api_key or not work:
        return None
    chars = []  # [(字, start, end)] —— 按顺序对应原文每个字
    for w in words:
        tok = (w.word or "").strip()
        if not tok or (len(tok) == 1 and tok in FILLER_WORDS):
            continue
        for ch in tok:
            chars.append((ch, w.start, w.end))
    if len(chars) < 20:
        return None
    raw_text = "".join(c[0] for c in chars)

    # 分片整理：长文本分成多段，每段独立 LLM 整理再拼接
    MAX_LLM_CHARS = 2500
    outs = []
    for i in range(0, len(chars), MAX_LLM_CHARS):
        chunk_text = "".join(c[0] for c in chars[i:i + MAX_LLM_CHARS])
        out_chunk = _llm_clean_text(chunk_text, api_key)
        if out_chunk is None:
            return None
        outs.append(out_chunk)
    out = "".join(outs)

    # 相似度校验：LLM 可能修正了错字/重复/口语词，只要求整体相似度 > 0.80
    out_no_punct = re.sub(r"[，。！？；：、,.!?;]", "", out)
    ratio = difflib.SequenceMatcher(None, raw_text, out_no_punct).ratio()
    if ratio < 0.80:
        print(f"[断句] LLM 输出与原文差异过大(相似度{ratio:.2f})，回退规则", file=sys.stderr)
        return None
    # 对齐时间戳：每个输出字符映射到原文字符
    aligned = _align_timestamps(chars, out)
    # 断句（两层）：句末标点必断；长句接近上限时逗号也断
    cues, buf, buf_text = [], [], ""
    def _flush():
        nonlocal buf, buf_text
        if buf:
            cues.append({"start": buf[0][1], "end": buf[-1][2], "text": buf_text})
        buf, buf_text = [], ""
    for ch, ws, we in aligned:
        if ch in "。！？!?":
            buf_text += ch
            _flush()
        elif ch in "，、；：,;:":
            buf_text += ch
            if len(buf_text) >= MAX_CHARS - 6:
                _flush()
        else:
            buf.append((ch, ws, we))
            buf_text += ch
    if buf:
        _flush()
    print(f"[断句] LLM 整理: {len(words)} 词 → {len(cues)} 条 (相似度{ratio:.2f})")
    return cues


def _align_timestamps(chars, out):
    """把 LLM 输出（含标点、可能修正字）的每个字符对齐到原文字符。
    返回 [(ch, start, end), ...]，标点时间 = 前一个字的 end。"""
    import difflib
    raw_text = "".join(c[0] for c in chars)
    out_no_punct = re.sub(r"[，。！？；：、,.!?;]", "", out)
    sm = difflib.SequenceMatcher(None, raw_text, out_no_punct)
    out_to_raw = []  # out_no_punct 第 k 字 → raw 索引（或 None）
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            out_to_raw.extend(i1 + (k - j1) for k in range(j1, j2))
        elif op == "replace":
            for k in range(j1, j2):
                raw_idx = i1 + int((k - j1) * (i2 - i1) / max(1, j2 - j1)) if i2 > i1 else None
                out_to_raw.append(raw_idx)
        elif op == "insert":
            out_to_raw.extend([None] * (j2 - j1))
        # delete 跳过（原文有、输出没有）
    result = []
    oi = 0
    last_end = 0.0
    for ch in out:
        if ch in "，。！？；：、,.!?;":
            result.append((ch, last_end, last_end))
        else:
            raw_idx = out_to_raw[oi] if oi < len(out_to_raw) else None
            oi += 1
            if raw_idx is not None and raw_idx < len(chars):
                last_end = chars[raw_idx][2]
                result.append((ch, chars[raw_idx][1], chars[raw_idx][2]))
            else:
                result.append((ch, last_end, last_end))
    return result


def _group_tokens_to_cues(words):
    """把 whisper 词级 token 组成字幕条,保证不在词中间断开,并补全标点。

    两个历史问题:
    1. whisper 会把双字词切成两个单字 token(医|生),强制断句落在词中间时
       前后两条字幕各显示半个词 → jieba 重新分词,断点必在词边界。
    2. whisper 中文输出经常不带标点,断句逻辑依赖标点 → 没标点时只能靠
       MAX_CHARS 硬切,句子全部连在一起(用户 2026-08-20 反馈)。
       解法:用语音停顿反推标点 -- 词间隙 >0.6s 插句号,>0.3s 插逗号。
    """
    # 过滤语气词后的 token 流 (tok, start, end)
    toks = []
    for w in words:
        tok = w.word.strip()
        if not tok:
            continue
        if len(tok) == 1 and tok in FILLER_WORDS:
            continue
        toks.append((tok, w.start, w.end))
    if not toks:
        return []

    # 停顿反推标点：返回带标点的 token 流。
    # 注意：标点 token 不占时间（start=end=prev_end）——
    # 否则字幕结束时间会被推到下一词的开始，间隙归零，
    # 全部帧被「合并间距太小」逻辑吞掉（2026-08-20 实际事故：1139 词→1 条字幕）。
    # 阈值：句号>0.9s、逗号>0.55s —— 只补真停顿，不把词间短停顿误当标点。
    def with_punct(tokens):
        out, prev_end = [], None
        for tok, ws, we in tokens:
            if prev_end is not None:
                gap = ws - prev_end
                if gap > 0.9:
                    out.append(("。", prev_end, prev_end))
                elif gap > 0.55:
                    out.append(("，", prev_end, prev_end))
            out.append((tok, ws, we))
            prev_end = we
        return out

    toks = with_punct(toks)

    def _clean(t):
        # 只去首部标点/空格,保留尾部标点(可读性)
        return fix_terms(t.lstrip(" " + BREAK))

    try:
        import jieba
        import logging as _lg
        jieba.setLogLevel(_lg.ERROR)
        # 全文 + 每个字符所属 token 的时间映射(含合成的标点字符)
        chars = []
        for tok, ws, we in toks:
            for ch in tok:
                chars.append((ch, ws, we))
        full = "".join(c[0] for c in chars)
        cues, buf, start, pos = [], [], None, 0
        for word in jieba.cut(full):
            if not word:
                continue
            wlen = len(word)
            if start is None:
                start = chars[pos][1]
            buf.append(word)
            t = "".join(buf)
            if (word[-1] in BREAK and len(t) >= MIN_CHARS) or len(t) >= MAX_CHARS:
                end_time = chars[pos + wlen - 1][2]
                text = _clean(t)
                if text and not all(c in FILLER_WORDS for c in text):
                    cues.append({"start": start, "end": end_time, "text": text})
                buf, start = [], None
            pos += wlen
        if buf:
            text = _clean("".join(buf))
            if text and not all(c in FILLER_WORDS for c in text):
                cues.append({"start": start, "end": chars[-1][2], "text": text})
        return cues
    except ImportError:
        pass

    # 降级方案:原始 token 流 + 单字 lookahead 词边界保护
    cues, buf, start = [], [], None
    for i, (tok, ws, we) in enumerate(toks):
        if start is None:
            start = ws
        buf.append(tok)
        t = "".join(buf)
        # 标点 token:直接作为断句点
        if len(tok) == 1 and tok in BREAK and len(t) >= MIN_CHARS:
            text = _clean(t)
            if text and not all(c in FILLER_WORDS for c in text):
                cues.append({"start": start, "end": we, "text": text})
            buf, start = [], None
            continue
        if (tok[-1] in BREAK and len(t) >= MIN_CHARS) or len(t) >= MAX_CHARS:
            # 词边界保护:强制断句时若末 token 和下一 token 都是单字,
            # 多带一个 token(上限溢出 2 字),避免把双字词切成两半
            if len(t) >= MAX_CHARS and len(tok) == 1 and tok[-1] not in BREAK:
                nxt = next((x for x in toks[i + 1:i + 3]
                            if not (len(x[0]) == 1 and x[0] in FILLER_WORDS)), None)
                if nxt and len(nxt[0]) == 1 and len(t) < MAX_CHARS + 2:
                    continue
            text = _clean(t)
            if text and not all(c in FILLER_WORDS for c in text):
                cues.append({"start": start, "end": we, "text": text})
            buf, start = [], None
    if buf:
        text = _clean("".join(buf))
        if text and not all(c in FILLER_WORDS for c in text):
            cues.append({"start": start, "end": toks[-1][2], "text": text})
    return cues


def parse_llm_json_array(out):
    """解析 LLM 返回的 JSON 数组,多策略容错。

    CI 实证踩过的坑:裸键、单引号、尾随逗号、引号错位("end:29")，
    以及 LLM 偶发把 JSON 包进 ```json 代码块、甚至再套一层 [ ]。
    逐级降级:去代码块围栏 → JSON → 字面量 → 嵌套数组展开 → 正则修复 → 宽松抽取。
    """
    import ast

    def _loads(s):
        try:
            return json.loads(s)
        except (json.JSONDecodeError, ValueError):
            return None

    def _unwrap(v):
        while isinstance(v, list) and len(v) == 1 and isinstance(v[0], list):
            v = v[0]
        return v

    out = re.sub(r"```[a-zA-Z]*", "", out)
    out = out.replace("```", "")
    single = _loads(out.strip())
    if isinstance(single, dict):
        if isinstance(single.get('picks'), list):
            return single['picks']
        if 'candidate_id' in single or {'start', 'end'} <= single.keys():
            # Normalize only the container; callers verify real ranges.
            return [single]
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        raise RuntimeError(f"金句返回无法解析(无数组):{out[:300]}")
    raw = m.group(0)

    v = _loads(raw)
    if v is None:
        try:
            v = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            v = None
    if isinstance(v, list):
        v = _unwrap(v)
        # 2026-09-02：空数组是**合法结果**（选段改造后，LLM 判定「本段没有够格
        # 金句」就返回 []）。原来 `if v:` 把 [] 当解析失败继续降级，日志会误报
        # 「解析失败」，也可能被后面的宽松抽取捞出垃圾结果。
        if isinstance(v, list) and (v or raw.strip() in ("[]", "[ ]")):
            return v

    fixed = re.sub(r'"(\w+):(\d+)"', r'"\1":\2', raw)
    fixed = re.sub(r"([{,]\s*)(\w+)(\s*:)", r'\1"\2"\3', fixed)
    fixed = fixed.replace("'", '"')
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
    v = _loads(fixed)
    if isinstance(v, list):
        v = _unwrap(v)
        if v:
            return v

    objs = []
    for block in re.findall(r"\{[^{}]*\}", raw):
        pairs = re.findall(r'"?(\w+)"?\s*:\s*"?([^",}]+)"?', block)
        obj = {k: (int(v) if v.strip().isdigit() else v.strip()) for k, v in pairs}
        if "start" in obj and "end" in obj:
            objs.append(obj)
    if objs:
        return objs
    raise RuntimeError(f"金句 JSON 所有修复策略均失败:{raw[:300]}")
def argument_context_candidates(cues,seeds):
    """Offer complete sentences, including nearby natural answer endings.

    ASR display rows are not sentence boundaries. Snapping a 120-second window
    to one such row both cut sentences and missed slightly longer answers.
    Candidates still need an editorial decision; duration is not approval.
    """
    from source_selection import sentence_units, boundary_error
    units=sentence_units(cues)
    ranges={}
    for seed in seeds:
        lo,hi=seed.get('start'),seed.get('end')
        if type(lo) is not int or type(hi) is not int or not 0<=lo<=hi<len(cues):
            continue
        openings=[u['start'] for u in units if u['start']<=lo]
        if not openings:continue
        start_options=set()
        for lookback in (0,15,30,60,90):
            stamp=cues[lo]['start']-lookback
            a=min(openings,key=lambda i:abs(cues[i]['start']-stamp))
            start_options.add(a)
        for a in sorted(start_options):
            ends=[u['end'] for u in units if u['end']>=hi
                  and editorial.MIN_SECONDS<=cues[u['end']]['end']-cues[a]['start']<=330]
            if not ends:continue
            # Keep every sentence ending in the preferred 2–3 minute range,
            # rather than offering only the first row crossing 120 seconds.
            chosen={b for b in ends if cues[b]['end']-cues[a]['start']<=180}
            for length in (210,240,300):
                chosen.add(min(ends,key=lambda j:abs(cues[j]['end']-cues[a]['start']-length)))
            for b in sorted(chosen):
                seconds=cues[b]['end']-cues[a]['start']
                if not boundary_error(cues,dict(start=a,end=b)):
                    ranges[(a,b)]={'start':a,'end':b,'duration_sec':round(seconds,2)}
    return [dict(row,candidate_id=i) for i,row in enumerate(ranges.values())]


def selection_schema(cue_count=None, candidate_count=None):
    """Constrain CPU output shape and IDs; timestamps are verified in Python."""
    properties = {'score': {'type': 'number', 'minimum': 0, 'maximum': 10},
                  'reason': {'type': 'string', 'maxLength': 60}}
    if candidate_count is not None:
        properties.update(candidate_id={'type': 'integer', 'minimum': 0,
                                        'maximum': candidate_count - 1},
                          accepted={'type': 'boolean'})
    else:
        properties.update({k: {'type': 'integer', 'minimum': 0,
                               'maximum': cue_count - 1} for k in ('start', 'end')})
    return {'type': 'object', 'properties': {'picks': {'type': 'array',
        'maxItems': 2, 'items': {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}}},
        'required': ['picks'], 'additionalProperties': False}


def pick_argument_context(cues,seeds,speaker,api_key,work,suffix):
    if not any(type(p.get('start')) is int and type(p.get('end')) is int
               and 0 <= p['start'] <= p['end'] < len(cues) for p in seeds):
        raise ValueError('原选段没有有效字幕编号，不能判定素材不合格')
    choices=argument_context_candidates(cues,seeds)
    if not choices:return []
    (work/f'context_candidates{suffix}.json').write_text(json.dumps(choices,ensure_ascii=False,indent=2))
    transcript='\n'.join(f"字幕{u['start']}-{u['end']}|{u['text']}"
                         for u in editorial_sentence_units(cues))
    # The complete numbered transcript already contains every opening/ending.
    # Repeating those strings for 30-60 overlapping ranges inflated real CPU
    # prompts past 12k chars (#632), without adding any source evidence.
    table=choices
    prompt=(f'你是{speaker}访谈编辑。此前选出了有意义的短句，但用户要完整观点长片。'
        '以下候选是这些观点附近的真实连续上下文，每条已由程序确保至少120秒。'
        '从候选ID中选至多2条：一个完整主题、开头独立可懂、解释充分、自然结束。'
        '必须连同中间原文阅读判断，不能仅看首尾或因为时长足够就接受。'
        '无关问题拼在一起、寒暄开场、缺必要解释、残句结束必须拒绝。'
        '可以选较长候选保留同主题追问，不得改写文字。没有合格候选就返回[]。'
        '只输出JSON对象，picks字段为数组，每项candidate_id,accepted,score,reason。'
        '只返回接受的候选并设accepted=true；检查全部候选后仍无合格项才返回{"picks":[]}。'
        '不要用一两条拒绝结果代替对其余候选的判断。'
        '不得自行填写起止序号或时长，程序按candidate_id取真实区间。\n完整原文：\n'+transcript+
        '\n候选：\n'+json.dumps(table,ensure_ascii=False))
    answer=llm([{'role':'user','content':prompt}],api_key,temperature=0,max_tokens=512,
               budget_sec=text_budget(90),response_schema=selection_schema(candidate_count=len(choices)))
    (work/f'context_response{suffix}.txt').write_text(answer)
    selected=[]
    rejected_ids=set()
    for row in parse_llm_json_array(answer):
        if not isinstance(row,dict) or type(row.get('accepted')) is not bool:
            raise ValueError('连续上下文缺少明确接受或拒绝结论')
        i=row.get('candidate_id')
        if type(i) is not int or not 0<=i<len(choices):
            raise ValueError('连续上下文候选ID无效，未形成内容判定')
        if row.get('accepted') is False or float(row.get('score',0))<MIN_HIGHLIGHT_SCORE:
            rejected_ids.add(i)
            continue
        choice=choices[i]
        from source_selection import boundary_error
        if boundary_error(cues,choice):
            continue
        if any(not(choice['end']<p['start'] or choice['start']>p['end']) for p in selected):
            continue
        selected.append(dict(start=choice['start'],end=choice['end'],score=row['score'],reason=row.get('reason','')))
    # A response rejecting only two IDs does not reject the other candidates.
    # Keep the ASR available for bounded recovery instead of banning the source.
    if not selected and rejected_ids and len(rejected_ids)<len(choices):
        raise SelectionIncomplete(
            f'连续上下文仅明确拒绝{len(rejected_ids)}/{len(choices)}个候选，选段未完成；保留ASR，不判整源无合格片')
    return selected[:2]


def editorial_sentence_units(cues):
    """Read ASR display rows as continuous speech, without rewriting a byte."""
    units=[]
    start=0
    text=''
    for i,cue in enumerate(cues):
        text+=cue['text']
        if re.search(r'[。！？!?][”’」』\"]?\s*$',text) or i==len(cues)-1:
            units.append(dict(start=start,end=i,text=text))
            start=i+1
            text=''
    return units


def pick_highlights(cues, speaker, api_key, work, suffix="", target_sec=None, allow_empty=False):
    """Select complete continuous arguments; short quotations never enter daily work."""
    target = target_sec or TARGET_SEC
    from source_selection import boundary_error
    identity = {'editorial': editorial.plan_identity(cues, target), 'selector_version': 11}
    cache = work / f"highlights{suffix}.json"
    if cache.exists():
        try:
            saved = json.loads(cache.read_text())
            if isinstance(saved, dict) and saved.get('identity') == identity:
                for pick in saved['picks']:
                    editorial.range_seconds(cues, pick)
                    if boundary_error(cues,pick):raise ValueError('缓存选段边界已失效')
                return saved['picks']
        except (ValueError, TypeError, KeyError):
            pass
    if not cues or cues[-1]['end']-cues[0]['start'] < editorial.MIN_SECONDS:
        return []
    if os.environ.get('SOURCE_EDITORIAL_FIRST') == 'true':
        from source_selection import select
        selected=select(cues)
        if selected:
            print(f'[原文选段] {len(selected)}条连续候选；无需等待文本模型，逐条进入实片质检',flush=True)
            cache.write_text(json.dumps({'identity':identity,'picks':selected},ensure_ascii=False,indent=2))
            return selected
    numbered_rows=[]
    for unit in editorial_sentence_units(cues):
        a,b=unit['start'],unit['end']
        numbered_rows.append(f"字幕{a}-{b}|{cues[a]['start']:.2f}-{cues[b]['end']:.2f}秒|{unit['text']}")
    numbered="\n".join(numbered_rows)
    prompt = (
        f"你是{speaker}访谈编辑。以下是带原始时间戳的CPU离线ASR。"
        "字幕序号之间不一定是词句边界，请连起来读。用户明确拒绝十几秒、几十秒摘句。"
        "选1到2个不同的、连续完整观点，每条以120到180秒为主，"
        "需要解释时可更长。每个区间实际结束时间减起始时间必须至少120秒。"
        "一条必须讲清一个主题，有观点、有理由或案例、自然结论；保留必要限定与否定。"
        "不要只取结论、不要拼不相关问题、不要为了数量硬凑。无法满足就返回[]。"
        "开场第一句话须明确主题并独立可懂，不要求三秒内说完；不能从半句话、无指代对象的回应、主持人称呼或寒暄开始；"
        "也不能删掉理解这句话所必需的上下文。可以保留同一主题内有用的追问。"
        "输入已将显示换行接回完整句，字幕a-b表示这一整句占用的原始字幕编号。"
        "start/end仍是原始字幕编号，不是句子序号或秒数。先找同一主题问答的自然起止，"
        "再核算时长，不能直接从开头截到恰好120秒；不得把片头预告、寒暄和正式采访混成一段。"
        "片尾必须包含回答及结论，不能用下一个未回答的问题凑够120秒。"
        "保留原话，不修正或补造ASR内容，不把口语重复当成内容不完整。"
        '只返回JSON对象，picks字段为数组，每项包含start,end,score(至少7),reason(完整主题)。'
        '没有合格选段返回{"picks":[]}。\n'+numbered)
    valid=[];seeds=[];parsed_response=False
    for attempt in range(2):
        try:
            response=llm([{'role':'user','content':prompt}],api_key,
                         temperature=0,max_tokens=512,budget_sec=text_budget(90),
                         response_schema=selection_schema(cue_count=len(cues)))
            (work/f'highlight_response{suffix}-{attempt}.txt').write_text(response)
            picks=parse_llm_json_array(response)
            if any(not isinstance(p,dict) or not {'start','end','score'} <= p.keys()
                   for p in picks):
                raise ValueError('选段字段无效')
            parsed_response=True
            seeds.extend(p for p in picks if float(p.get('score',0))>=MIN_HIGHLIGHT_SCORE)
            invalid=[]
            for pick in picks:
                if float(pick.get('score',0)) < MIN_HIGHLIGHT_SCORE:
                    continue
                try:
                    editorial.range_seconds(cues,pick)
                    error=boundary_error(cues,pick)
                    if error:raise ValueError(error)
                except (ValueError,TypeError,KeyError) as exc:
                    invalid.append(str(exc))
                    continue
                if any(not(pick['end']<v['start'] or pick['start']>v['end']) for v in valid):
                    continue
                valid.append(pick)
            if valid or not picks:
                break
            if invalid:
                raise ValueError('; '.join(invalid))
        except LocalTextUnavailable:
            raise
        except (ValueError,TypeError,KeyError,RuntimeError) as exc:
            print(f'[完整观点] 第{attempt+1}次未通过: {exc}')
            prompt += '\n上次区间未通过：'+str(exc)+'。重新在同一主题完整上下文内选择，禁止短句。'
    if not parsed_response:
        raise LocalTextUnavailable('本地选段回答格式无效，未形成内容判定；保留ASR并重试')
    if not valid and seeds:
        try:
            valid=pick_argument_context(cues,seeds,speaker,api_key,work,suffix)
            for pick in valid:editorial.range_seconds(cues,pick)
        except LocalTextUnavailable:
            raise
        except (ValueError,TypeError,KeyError,RuntimeError) as exc:
            print('[完整观点] 连续上下文候选未通过：'+str(exc))
            raise LocalTextUnavailable('连续上下文选段格式或编号无效；保留ASR并重试，不判素材不合格') from exc
    valid.sort(key=lambda p:p['start'])
    cache.write_text(json.dumps({'identity':identity,'picks':valid},ensure_ascii=False,indent=2))
    return valid


def review_complete_argument(cues, picks, speaker, api_key, work, suffix):
    omitted_text=None
    if len(picks)==1:
        editorial.range_seconds(cues,picks[0])
    else:
        try:
            spans=[dict(start=cues[p['start']]['start'],end=cues[p['end']]['end']) for p in picks]
            source_sha=picks[0].get('editorial_source_sha256')
            if (not editorial.reviewed_omission_matches(source_sha,spans)
                    or any(p.get('editorial_source_sha256')!=source_sha for p in picks)):
                raise ValueError('未核对的拼接范围')
            omitted_text=''.join(c['text'] for c in cues[picks[0]['end']+1:picks[1]['start']])
            if not omitted_text:raise ValueError('缺少删去部分的原文')
        except (IndexError,KeyError,TypeError,ValueError) as exc:
            raise VisualQualityError('只允许同一论述中已核对的短插语剪除，禁止拼凑：'+str(exc)) from exc
    text=''.join(c['text'] for p in picks for c in cues[p['start']:p['end']+1])
    integrity_error=editorial.transcript_integrity_error(text)
    if integrity_error:
        raise VisualQualityError(integrity_error)
    digest=editorial.text_digest(text)
    cache=work/f'editorial_review{suffix}.json'
    if cache.exists():
        try:
            saved=json.loads(cache.read_text())
            if (saved.get('transcript_sha256')==digest and saved.get('review_prompt_version')==6
                    and saved.get('review_model')==LOCAL_LLM_MODEL
                    and saved.get('review_protocol')==(3 if omitted_text else 2)
                    and (not omitted_text or (saved.get('omitted_text_sha256')==editorial.text_digest(omitted_text)
                         and saved.get('omission_preserves_meaning') is True and saved.get('omitted_is_parenthetical') is True))
                    and not editorial.review_error(saved)):
                return saved
        except (ValueError,TypeError):
            pass
    # A user-reviewed source profile is stronger evidence than a small CPU
    # model re-judging the same clip on every run. Trust it only when it is
    # bound to this mother SHA and to the exact post-correction transcript;
    # any changed byte fails closed and must be reviewed again.
    reviewed=picks[0].get('editorial_review') if len(picks)==1 else None
    if reviewed is not None:
        proof=dict(reviewed)
        if (not picks[0].get('editorial_source_sha256')
                or proof.get('transcript_sha256')!=digest
                or proof.get('review_protocol')!=2
                or proof.get('review_prompt_version')!=2):
            raise VisualQualityError('已核对观点与当前母片修正文本不匹配，禁止沿用旧审核')
        for name in ('opening_quote','ending_quote'):
            quote=proof.get(name)
            if not isinstance(quote,str) or not quote or quote not in text:
                raise VisualQualityError('已核对观点的开场/结尾证据不在当前原话中')
        issues=proof.get('issues')
        if not isinstance(issues,list) or any(
                not isinstance(item,str) or not item or item not in text for item in issues):
            raise VisualQualityError('已核对观点的问题证据不在当前原话中')
        error=editorial.review_error(proof)
        if error:
            raise VisualQualityError(error+'：'+str(proof.get('issues') or ''))
        cache.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
        print('[完整观点] 命中母片SHA及修正后全文哈希绑定的人工审核')
        return proof
    prompt=(f'审核{speaker}的一段访谈能否独立成片。任务是判断剪辑是否保留完整表达，'
        '不是审查投资判断的正确性，也不是要求研究报告式的严密论证。\n'
        '先阅读全部原话，在analysis中逐字摘录观点、至少一个理由或例子、收束语；不存在则填空字符串。'
        '然后填写verdict。观点加上片内理由、自然完成回答即可构成完整表达；'
        '结尾可以是最后一条解释，不必再次重述观点。不要求定义常见行业名词或提供数据证明。'
        '主持人已说出话题再提问可以独立开场，提及过去直播日期不等于依赖片外上下文。\n'
        '保留以下剪辑门禁：真正不明的指代、必要论述被切断、未回答的新问题结尾、'
        '无关话题或片头预告寒暄拼凑，均不得通过。中间同主题追问可以保留。'
        '按完整原话理解，字幕显示换行、口吃、重复、语气词、常见口语比喻本身不算错误。'
        '不得自行改写错字或猜测关键数字、否定、实体；仅在这些识别歧义实际妨碍理解时，'
        '列入audio_issues并标记requires_audio_review。结构不完整的理由写入completeness_reason，'
        '不能冒充听音问题。\n'
        '所有quote必须逐字取自原话。opening_quote从第一个字开始，ending_quote覆盖最后一个字，'
        '各不超过100字。audio_issues每项将quote和影响原意的reason配对；没有则为空数组。'
        '每个判定须与摘录的证据一致；缺什么写具体，不要凭空提出片内未问的新问题。\n原话：'+text)
    # Whole-sentence evidence prevents a display row ending mid-sentence from
    # becoming a spurious "missing object". Evidence and verdict are separate
    # objects because Ollama's grammar orders property names alphabetically.
    evidence=list(dict.fromkeys(fragment
        for sentence in re.findall(r'[^。！？!?]+[。！？!?]?',text)
        for fragment in (sentence,sentence.rstrip('。！？!?')) if fragment))
    quote_field={'type':'string','maxLength':100}
    analysis_fields={
        'claim_quote':quote_field,
        'reasoning_quote':quote_field,
        'conclusion_quote':quote_field,
        'opening_quote':quote_field,
        'ending_quote':quote_field,
        'summary':{'type':'string','maxLength':50},
        'completeness_reason':{'type':'string','maxLength':150},
        'audio_issues':{'type':'array','maxItems':8,'items':{
            'type':'object','properties':{
                'quote':{'type':'string','enum':evidence},
                'reason':{'type':'string','maxLength':60}},
            'required':['quote','reason'],'additionalProperties':False}},
    }
    fields={name:{'type':'boolean'} for name in ('standalone_opening',
        'complete_argument','reasoning_present','natural_ending','requires_audio_review')}
    if omitted_text:
        first=''.join(c['text'] for c in cues[picks[0]['start']:picks[0]['end']+1])
        second=''.join(c['text'] for c in cues[picks[1]['start']:picks[1]['end']+1])
        prompt+=('\n另须独立审核这次插语剪除。音画与字幕会一起剪掉中间插语，保留片段顺序。'
            '不能因某句话不方便、包含反例/否定/必要限定就删掉它。若删除改变原意、论证或立场，必须拒绝。'
            '不猜测插语中的识别疑点；仅在无需依赖该插语也能明确原论点和限定时判断。'
            '输出omission_preserves_meaning及omitted_is_parenthetical两个布尔值和omission_reason。'
            '\n[保留前段]'+first+'\n[拟删插语]'+omitted_text+'\n[保留后段]'+second)
        fields.update({
            'omission_preserves_meaning':{'type':'boolean'},
            'omitted_is_parenthetical':{'type':'boolean'},
            'omission_reason':{'type':'string'},
        })
    response_schema={'type':'object','properties':{
        'analysis':{'type':'object','properties':analysis_fields,
            'required':list(analysis_fields),'additionalProperties':False},
        'verdict':{'type':'object','properties':fields,
            'required':list(fields),'additionalProperties':False}},
        'required':['analysis','verdict'],'additionalProperties':False}
    for attempt in range(2):
        try:
            response=llm([{'role':'user','content':prompt}],api_key,temperature=0,
                         max_tokens=2200,budget_sec=text_budget(240),response_schema=response_schema)
            (work/f'editorial_response{suffix}-{attempt}.txt').write_text(response,encoding='utf-8')
            raw=re.sub(r'```(?:json)?|```','',response).strip()
            match=re.search(r'\{.*\}',raw,re.S)
            proof=json.loads(match.group(0) if match else raw)
            if 'analysis' in proof or 'verdict' in proof:
                analysis,verdict=proof.get('analysis'),proof.get('verdict')
                if not isinstance(analysis,dict) or not isinstance(verdict,dict):
                    raise ValueError('缺少证据或判定对象')
                for name in ('claim_quote','reasoning_quote','conclusion_quote'):
                    quote=analysis.get(name)
                    if not isinstance(quote,str) or (quote and quote not in text):
                        raise ValueError('观点/理由/收束引用不在实际原话中')
                audio_issues=analysis.get('audio_issues')
                if not isinstance(audio_issues,list) or any(not isinstance(x,dict)
                        or not isinstance(x.get('reason'),str) or not x['reason']
                        for x in audio_issues):
                    raise ValueError('听音问题缺少配对说明')
                proof={**analysis,**verdict,'issues':[x.get('quote') for x in audio_issues],
                       'issue_details':[x['reason'] for x in audio_issues]}
                # A positive decision needs actual source support, not just flags.
                if proof.get('complete_argument') is True and not analysis['claim_quote']:
                    raise ValueError('完整观点通过却没有原文观点证据')
                if proof.get('reasoning_present') is True and not analysis['reasoning_quote']:
                    raise ValueError('理由通过却没有原文理由证据')
            for name in ('opening_quote','ending_quote'):
                quote=proof.get(name)
                if not isinstance(quote,str) or not quote or quote not in text:
                    raise ValueError('开场/结尾引用证据不在实际原话中')
                if name=='opening_quote' and not text.startswith(quote):
                    raise ValueError('开场引用不是实际选段的开头；必须从第一个字摘录')
                if name=='ending_quote' and not text.endswith(quote):
                    raise ValueError('结尾引用不是实际选段的结尾；必须覆盖最后一个字')
            issues=proof.get('issues')
            if not isinstance(issues,list) or any(not isinstance(x,str) or not x or x not in text for x in issues):
                raise ValueError('问题引用证据不在实际原话中')
            break
        except (ValueError,TypeError,RuntimeError) as exc:
            (work/f'editorial_service_error{suffix}-{attempt}.txt').write_text(type(exc).__name__+': '+str(exc))
            if attempt:
                raise EditorialReviewUnavailable('观点审核服务未提供可对照的实际原文证据：'+str(exc)) from exc
            prompt+='\n上次响应未提供可逐字核对的证据。请重新独立审核，只从实际保留原话摘录引用，不要复制审核规则。'
    if proof.get('issues'):
        proof['requires_audio_review']=True
    proof.update(version=editorial.VERSION,transcript_sha256=digest,review_protocol=3 if omitted_text else 2,
                 review_prompt_version=6,review_model=LOCAL_LLM_MODEL)
    if omitted_text:
        proof['omitted_text_sha256']=editorial.text_digest(omitted_text)
        cache.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
        if proof.get('omission_preserves_meaning') is not True or proof.get('omitted_is_parenthetical') is not True:
            raise VisualQualityError('剪除插语未通过独立语义复核：'+str(proof.get('omission_reason','')))
    cache.write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    error=editorial.review_error(proof)
    if error:
        raise VisualQualityError(error+'；完整性理由：'+str(proof.get('completeness_reason') or '未提供')+
            '；原文问题：'+json.dumps(list(zip(proof.get('issues') or [],proof.get('issue_details') or [])),ensure_ascii=False))
    return proof


def translate(texts, api_key, work):
    cache = work / "translation.json"
    if cache.exists():
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if len(cached) == len(texts):
            print("[翻译] 命中缓存")
            return cached

    out, BATCH = [], 20
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        numbered = "\n".join(f"{j+1}. {t}" for j, t in enumerate(batch))
        prompt = (
            "把下面每条中文字幕翻成自然口语化的英文。\n"
            "严格保持编号,每条一行,格式「N. translation」,只输出译文行。\n"
            "注意:原文来自语音识别,可能有残缺或错字;只译实际出现的内容,"
            "不要自行补全或添加原文没有的信息。\n"
            "专有名词按通用译法:林园=Lin Yuan,达仁堂=Darentang,"
            "片仔癀=Pien Tze Huang,同仁堂=Tong Ren Tang,"
            "安宫牛黄丸=Angong Niuhuang Wan,速效救心丸=Suxiao Jiuxin Wan。\n\n"
            + numbered)
        res = llm([{"role": "user", "content": prompt}], api_key, temperature=0.3)
        got = {}
        for line in res.splitlines():
            mm = re.match(r"\s*(\d+)[.、)]\s*(.+)", line)
            if mm:
                got[int(mm.group(1))] = mm.group(2).strip()
        out.extend(got.get(j + 1, "") for j in range(len(batch)))
        print(f"[翻译] {min(i+BATCH, len(texts))}/{len(texts)}")
    cache.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def unfinished_caption_tail(text):
    """Function words are incomplete tails; 机会/社会/相对 are whole words."""
    from presentation import word_spans
    spans=word_spans(text)
    if not spans:
        return False
    if re.search(r'(?:(?:我|我们)(?:主要)?|主要)(?:是)?从(?:这个)?行业$',text):
        return True
    a,b=spans[-1]
    return bool(re.search(r'(?:还更|一个|这个|这种|一些|那些|这些|虽然|即使|尽管|无论|(?:我|你|他|她|们|人|企业|公司)会|(?:他|它|你|我)跟银行)$',text)) or text[a:b] in {
        '更加','因为','所以','如果','那么','但是','而且','以及','把','被',
        '来自','甚至','跟','向','主要',
        '与','比','是','要','会','能','将','对','向','愿意','暂时','就是'}


def dependent_caption_start(text):
    from presentation import word_spans
    spans=word_spans(text)
    if not spans:
        return False
    a,b=spans[0]
    return text[a:b] in {'的','地','得'}


def caption_timeline(entries):
    """One shared character/time map for planning and final validation."""
    strip = lambda t: re.sub(r'[\s，。！？；：、]', '', t)
    chars=[]; entry_bounds=set(); punctuation_bounds=set()
    for i,e in enumerate(entries):
        a,b=float(e['start_sec']),float(e['end_sec'])
        if i+1<len(entries): b=min(b,float(entries[i+1]['start_sec']))
        original=e.get('zh','')
        mapped=e.get('caption_chars')
        for j,c in enumerate(original):
            if strip(c):
                chars.append(tuple(mapped[j]) if mapped is not None else
                             (c,a+(b-a)*j/len(original),a+(b-a)*(j+1)/len(original)))
            elif c in '，。！？；：、':
                punctuation_bounds.add(len(chars))
        if chars:
            entry_bounds.add(len(chars))
    return chars,entry_bounds,punctuation_bounds


def apply_semantic_groups(entries, texts, capacity, font_px=None, min_font_px=38):
    """Validate source- or model-selected boundaries against text and timing."""
    from presentation import word_spans, wrap_words
    strip = lambda t: re.sub(r'[\s，。！？；：、]', '', t)
    chars,entry_bounds,punctuation_bounds=caption_timeline(entries)
    source=''.join(c[0] for c in chars)
    if not isinstance(texts,list) or not texts or not all(isinstance(t,str) and strip(t) for t in texts):
        raise ValueError('完整意群分组为空或格式错误')
    if ''.join(strip(t) for t in texts)!=source:
        raise ValueError('意群分组改写或丢失原话，拒绝烧录')
    # A spoken filler/call-out can flash even when its text is a whole
    # word. Remove that screen boundary while preserving the original times
    # and every character. The merged group still faces all layout/8s gates.
    texts=list(texts)
    while len(texts)>1:
        offset=0; short=None
        for i,text in enumerate(texts):
            end=offset+len(strip(text))
            if chars[end-1][2]-chars[offset][1]<.8:
                short=i; break
            offset=end
        if short is None:
            break
        if short:
            texts[short-1:short+1]=[texts[short-1]+texts[short]]
        else:
            texts[:2]=[texts[0]+texts[1]]
    # `source` has punctuation removed, so dictionary tokenization can fuse
    # neighbours that were separate in the real transcript (e.g. “不会错。钱”
    # becomes the bogus token “错钱”). Original punctuation remains an exact,
    # source-backed semantic boundary and must survive that normalization.
    bounds={0,len(source)}|{b for a,b in word_spans(source)}|punctuation_bounds

    def fit_lines(text):
        """Return a legal per-cue capacity/font without crossing 38 px."""
        cue_capacity = capacity
        cue_font = font_px
        max_capacity = capacity
        if font_px:
            max_capacity = max(capacity, int(font_px * capacity / min_font_px))
        while True:
            try:
                wrap_words(text, cue_capacity)
                return cue_capacity, cue_font
            except ValueError as exc:
                if ("无法放入两行" not in str(exc)
                        or cue_capacity >= max_capacity):
                    raise
                cue_capacity += 1
                cue_font = max(min_font_px,
                               int(font_px * capacity / cue_capacity))

    def split_long_group(start, end):
        """Split at original cue/punctuation word boundaries, never by character count.

        This serves both timing (>8 s) and layout overflow.  The model still
        selects the complete parent intent group; local code only chooses safe
        screen boundaries inside it and rejects when none exists.
        """
        candidates = sorted(
            ({start, end} | entry_bounds | punctuation_bounds) & bounds)
        best = {start: (0, [])}
        for a in candidates:
            if a not in best or a >= end:
                continue
            for b in candidates:
                if b <= a or b > end:
                    continue
                part = source[a:b]
                if unfinished_caption_tail(part) or dependent_caption_start(part):
                    continue
                duration = chars[b-1][2] - chars[a][1]
                if not .25 <= duration <= 8:
                    continue
                try:
                    cue_capacity, cue_font = fit_lines(part)
                except ValueError:
                    continue
                # Prefer readable complete ASR phrases around 8–18 characters;
                # a short final phrase is allowed but never flashed below .25 s.
                boundary_cost = (0 if b in punctuation_bounds else
                                 (2 if b in entry_bounds else 20))
                cost = (best[a][0] + abs(len(part)-14)
                        + (8 if len(part)<4 else 0) + boundary_cost)
                if b not in best or cost < best[b][0]:
                    best[b] = (cost, best[a][1] + [
                        (a, b, part, cue_capacity, cue_font)])
        if end not in best:
            raise ValueError('单屏跨越超过8秒，原始意群边界也无法安全重分')
        return best[end][1]

    result=[]; offset=0
    for original_text in texts:
        text=re.sub(r'\s+','',original_text); end=offset+len(strip(text))
        if end not in bounds: raise ValueError('意群分组切断完整词')
        if unfinished_caption_tail(strip(text)):
            raise ValueError('意群以未完成的连接词结束：'+text)
        if dependent_caption_start(strip(text)):
            raise ValueError('意群不能以依附上一屏的成分开头：'+text)
        # A model-selected complete clause can be within the advertised two-line
        # character limit and still have no legal split at the exact midpoint
        # (for example because a protected company name or numeric unit crosses
        # it).  Do not ask the model to break that word, and do not reject the
        # whole otherwise-good clip.  Instead find the smallest two-line
        # capacity that preserves every word and shrink this cue only.  The
        # floor remains 38 px, so this is a bounded layout adjustment rather
        # than a quality-gate bypass.
        a,b=chars[offset][1],chars[end-1][2]
        if b-a>8:
            pieces=split_long_group(offset,end)
        else:
            try:
                pieces=[(offset,end,text,*fit_lines(text))]
            except ValueError as fit_error:
                # A complete spoken sentence can overflow two lines in less
                # than eight seconds. Reuse the same source-bound splitter.
                try:
                    pieces=split_long_group(offset,end)
                except ValueError as split_error:
                    raise ValueError(str(fit_error)+'；原文边界无法安全重分') from split_error
        for lo, hi, piece, cue_capacity, cue_font in pieces:
            a,b=chars[lo][1],chars[hi-1][2]
            if b-a<.25: raise ValueError('意群字幕过短闪屏')
            group = dict(start_sec=a,end_sec=b,zh=piece,en='',semantic_group=True)
            if cue_capacity > capacity:
                group['line_capacity'] = cue_capacity
                group['font_px'] = cue_font
            result.append(group)
        offset=end
    return result


def repair_semantic_boundaries(texts):
    """Remove invalid screen boundaries; never alter or drop spoken characters."""
    from presentation import word_spans
    source=''.join(texts)
    bounds={0,len(source)}|{b for a,b in word_spans(source)}
    out=[]; offset=0
    for text in texts:
        if out and (offset not in bounds or unfinished_caption_tail(out[-1]) or dependent_caption_start(text)):
            out[-1]+=text
        else:
            out.append(text)
        offset+=len(text)
    return out


def token_breaks_to_char_offsets(selected, tokens):
    """Map selected whole-word IDs; a model never has to count characters."""
    if (not isinstance(selected,list) or not selected
            or any(type(n) is not int or not 1<=n<=len(tokens) for n in selected)
            or selected[-1]!=len(tokens)
            or any(b<=a for a,b in zip([0]+selected,selected))):
        raise ValueError('换屏词编号须从1开始、严格递增并覆盖最后一个词')
    return [tokens[n-1]['end'] for n in selected]


def source_caption_groups(entries, layout):
    """Plan readable screens using words, grammar, source pauses and timestamps.

    Screen boundaries are layout decisions, not edits to the spoken argument.
    No characters, timestamps, negations, entity names or numeric units change.
    The existing independent validator checks the complete result afterwards.
    """
    from presentation import word_spans,wrap_words
    import jieba.posseg as posseg
    chars,entry_bounds,punctuation_bounds=caption_timeline(entries)
    source=''.join(c[0] for c in chars)
    if not source:raise ValueError('字幕原文为空')
    bounds=sorted({0,len(source)}|{b for a,b in word_spans(source)}|punctuation_bounds)
    sentence_ends=set(); terminal_marks={}; offset=0
    for c in ''.join(e.get('zh','') for e in entries):
        if c in '。！？':
            sentence_ends.add(offset)
            if c in '！？':terminal_marks[offset]=c
        elif not re.match(r'[\s，。！？；：、]',c):offset+=1
    tagged=list(posseg.cut(source));pos={};offset=0
    for word in tagged:
        pos[offset]=(word.word,word.flag);offset+=len(word.word)
    ends={a+len(word): (word,tag) for a,(word,tag) in pos.items()}
    capacity=layout['line_capacity'];font=layout.get('subtitle_font_px')
    max_capacity=max(capacity,int(font*capacity/38)) if font else capacity
    best={0:(0,[])}
    for i,a in enumerate(bounds):
        if a not in best:continue
        for b in bounds[i+1:]:
            if b-a>2*max_capacity:break
            part=source[a:b]
            duration=chars[b-1][2]-chars[a][1]
            if duration>layout.get('_phrase_limit',6):break
            if duration<.8:continue
            if unfinished_caption_tail(part) or dependent_caption_start(part):continue
            if part.startswith('的话'):continue
            if b<len(source) and (re.search(r'(?:就像|比如说|确实|不会|不能|应该|必须|需要|认为|觉得)$',part)
                    or part.endswith('会') and not part.endswith(('社会','机会','体会','学会','协会','大会'))):
                continue
            # Keep negation, prepositions, classifiers and verb/object pairs
            # together even if a noisy ASR cue boundary falls between them.
            tail,tail_tag=ends.get(b,('',''))
            following,next_tag=pos.get(b,('',''))
            hard_tail=tail in {'不','没','未','别','不会','不能','没有','不是','并非',
                    '应该','必须','需要','想','觉得','认为','知道','相信','就像','比如说',
                    '和','或','为','在','更','就','也','地','得'}
            dependent_pair=(tail_tag in {'p','c','q','u','uj','ul','d'} and tail not in {'了','着','过','的'}
                    or tail_tag.startswith('v')
                    or tail_tag.startswith('r') and next_tag.startswith(('v','d'))
                    or tail_tag.startswith(('a','s','f','b','n')) and next_tag.startswith('n'))
            if b<len(source) and (hard_tail or b not in punctuation_bounds and dependent_pair):
                continue
            if b<len(source) and part.endswith(('国内','国外','海外')) and next_tag.startswith(('n','r')):continue
            if following in {'的','地','得'}:continue
            if part.endswith('的') and b not in punctuation_bounds and next_tag.startswith(('n','r')):continue
            fits=False
            for cap in range(capacity,max_capacity+1):
                try:wrap_words(part,cap);fits=True;break
                except ValueError:pass
            if not fits:continue
            gap=(chars[b][1]-chars[b-1][2]) if b<len(chars) else 0
            boundary_cost=(0 if b in punctuation_bounds else
                           2 if gap>=.35 else 4 if b in entry_bounds else 10)
            cost=(best[a][0]+boundary_cost+abs(len(part)-14)/3
                  +abs(duration-3.5)*2+(8 if len(part)<5 else 0)
                  +40*sum(a<stop<b and not unfinished_caption_tail(source[a:stop])
                          for stop in sentence_ends))
            if b not in best or cost<best[b][0]:best[b]=(cost,best[a][1]+[part])
    if len(source) not in best:
        # Preserve a complete slow phrase rather than create a flashing tail.
        if not layout.get('_phrase_limit'):
            return source_caption_groups(entries,{**layout,'_phrase_limit':8})
        raise ValueError('没有满足词界、意群和显示时长的本地分屏路径')
    result=[];offset=0
    for part in best[len(source)][1]:
        offset+=len(part);result.append(part+terminal_marks.get(offset,''))
    return result


def caption_break_schema(token_count, char_count=1, max_group_chars=30):
    return {'type':'object','properties':{'break_after_tokens':{
        'type':'array','minItems':max(1,(char_count+max_group_chars-1)//max_group_chars),'maxItems':token_count,
        'items':{'type':'integer','minimum':1,'maximum':token_count}}},
        'required':['break_after_tokens'],'additionalProperties':False}


def compact_caption_tokens(tokens):
    # Offsets and timestamps belong to the validator, not the language model.
    # The old JSON repeated the transcript three times and sent >23k chars for
    # a two-minute clip. Only IDs and words are needed to choose boundaries.
    return ' '.join(f"{t['id']}:{t['text']}" for t in tokens)


def semantic_caption_entries(entries, api_key, layout, cache_path, reviewed_groups=None):
    """Prefer real sentence boundaries; validate every character locally."""
    from caption_readability import clean_entries, write_edit_proof, VERSION as READABILITY_VERSION
    entries, edit_proof = clean_entries(entries)
    capacity=layout['line_capacity']
    cache_path=Path(cache_path)
    write_edit_proof(cache_path.with_suffix('.editing.json'), edit_proof)
    # Old raw-text boundaries are invalid after display edits; replan the
    # cleaned transcript. Raw ASR and original reviewed picks remain untouched.
    if edit_proof['edits']:
        reviewed_groups=None
    cache_path=cache_path.with_name(cache_path.stem+f'.readable-{READABILITY_VERSION}.json')
    if reviewed_groups is not None:
        result=apply_semantic_groups(entries,reviewed_groups,capacity,layout.get('subtitle_font_px'))
        cache_path.write_text(json.dumps(reviewed_groups,ensure_ascii=False,indent=2))
        return result
    if cache_path.exists():
        try:
            return apply_semantic_groups(
                entries, json.loads(cache_path.read_text()), capacity,
                layout.get('subtitle_font_px'))
        except (ValueError,TypeError): pass
    try:
        groups=source_caption_groups(entries,layout)
        result=apply_semantic_groups(entries,groups,capacity,layout.get('subtitle_font_px'))
        cache_path.write_text(json.dumps(groups,ensure_ascii=False,indent=2))
        print(f'[字幕分屏] 本地词界、停顿与意群规划通过全部校验：{len(result)}屏',flush=True)
        return result
    except ValueError as exc:
        print('[字幕分屏] 本地规划需要补充：'+str(exc),flush=True)
    raw=''.join(e.get('zh','') for e in entries)
    if re.search(r'[。！？]',raw):
        # CPU ASR already supplies punctuation. Reconnect display rows into
        # actual sentences instead of sending a redundant 20k-character cue /
        # transcript / token table to the local model for a second segmentation.
        groups=[re.sub(r'[\s，。！？；：、]','',sentence)
                for sentence in re.findall(r'[^。！？]+[。！？]?',raw)]
        groups=[g for g in groups if g]
        groups=repair_semantic_boundaries(groups)
        try:
            result=apply_semantic_groups(entries,groups,capacity,layout.get('subtitle_font_px'))
            cache_path.write_text(json.dumps(groups,ensure_ascii=False,indent=2))
            print(f'[字幕分屏] 原文完整句边界通过字符、词界、时长及两行校验：{len(result)}屏',flush=True)
            return result
        except ValueError as exc:
            print('[字幕分屏] 原文边界需进一步分组：'+str(exc),flush=True)
    from presentation import word_spans
    # Ask for boundary indices, never a copied transcript: models tend to
    # silently repair spoken repetitions/ASR errors while copying strings.
    transcript=re.sub(r'[\s，。！？；：、]', '', ''.join(e.get('zh','') for e in entries))
    tokens=[{'id':i+1,'end':b,'text':transcript[a:b]} for i,(a,b) in enumerate(word_spans(transcript))]
    # 48 px captions may shrink per cue, but never below 38 px.  Tell the
    # model the actual bounded two-line limit and enforce it locally.  The old
    # code mentioned a nominal limit only in the prompt; an oversized group
    # could therefore reach wrap_words(), fail with a vague layout error, and
    # be repeated unchanged for all three attempts.
    font_px=layout.get('subtitle_font_px')
    max_capacity=(max(capacity, int(font_px*capacity/38))
                  if font_px else capacity)
    max_group_chars=max_capacity*2

    def repair_oversized_groups(breaks):
        """Ask for boundaries only inside an oversized model-selected intent."""
        repaired=[]; start=0
        for end in breaks:
            if end-start<=max_group_chars:
                repaired.append(end); start=end; continue
            parent=transcript[start:end]
            choices=[{'id':i+1,'end':b,'text':parent[a:b]}
                     for i,(a,b) in enumerate(word_spans(parent))]
            parent_bounds={0,len(parent)}|{t['end'] for t in choices}
            request=(
                '只修复下面这一个过长但语义完整的中文字幕意群。请在完整句/完整意群处'
                '增加换屏，不能按固定字数切，不能拆开专名、否定词、数字单位或谓宾结构。'
                f'只返回JSON {{"break_after_tokens":[每屏末词id]}}；每段最多{max_group_chars}字，'
                f'最后一个id必须是{len(choices)}。选词id，不是字符位置，不需要计算字数位置。原文：{parent}。'
                '词序列：'+compact_caption_tokens(choices))
            answer=llm([{'role':'user','content':request}],api_key,
                       temperature=0,max_tokens=1000,budget_sec=text_budget(45),
                       response_schema=caption_break_schema(len(choices),len(parent),max_group_chars))
            local=token_breaks_to_char_offsets(_parse_json_object(answer)['break_after_tokens'],choices)
            if (not isinstance(local,list) or not local
                    or any(type(n) is not int for n in local)
                    or local[-1]!=len(parent)
                    or any(n not in parent_bounds for n in local)
                    or any(b<=a or b-a>max_group_chars
                           for a,b in zip([0]+local,local))):
                raise ValueError(
                    f'超长意群局部复分失败（{len(parent)}字，硬上限{max_group_chars}字）')
            repaired.extend(start+n for n in local)
            start=end
        return repaired
    prompt=('请按中文完整句/完整意群给原文选字幕换屏位置。只返回JSON '
            '{"break_after_tokens":[每屏末词的id]}，最后一个id必须等于'
            +str(len(tokens))+'。只选下面明确给出的词id，不是字符位置，不需要计算累计字数。'
            '尽量每屏8到22字；在必须保留完整意群时可放宽，但绝不能超过'
            +str(max_group_chars)+'字。仅可从下面token的id字段选择位置，'
            '但绝不能仅按固定字数切割。不能把否定词和谓语拆开、不能以'
            '“还更、因为、如果、把、被、与”等未完成成分结束。'
            '可以选完整短语作为一个意群，如“守住现金流的企业”或“大家还更愿意买”。'
            '必须保留原文逗号体现的意群边界。错误：投资技巧一定要是 / 大行业；正确：投资技巧 / 一定要是大行业越来越大。'
            '错误：工资收入高 / 的一些发达国家；正确：凡是工资收入高的一些发达国家。'
            '原始ASR标点仅供参考，若句号落在“这个”等未完成成分后，需连接下句。'
            '原文：'+raw+'。完整词序列（id:词）：'+compact_caption_tokens(tokens))
    error=''
    for attempt in range(3):
        try:
            response=llm([{'role':'user','content':prompt+error}],api_key,temperature=0,max_tokens=1000,
                         budget_sec=text_budget(60),response_schema=caption_break_schema(len(tokens),len(transcript),max_group_chars))
            cache_path.with_suffix(f'.attempt{attempt+1}.txt').write_text(response,encoding='utf-8')
            breaks=token_breaks_to_char_offsets(_parse_json_object(response)['break_after_tokens'],tokens)
            if not isinstance(breaks,list) or not breaks or any(type(n) is not int for n in breaks) or breaks[-1]!=len(transcript) or any(b<=a for a,b in zip([0]+breaks,breaks)):
                raise ValueError('换屏位置必须严格递增并覆盖全部原文')
            merged=repair_semantic_boundaries([transcript[a:b] for a,b in zip([0]+breaks,breaks)])
            breaks=[]; boundary=0
            for piece in merged:
                boundary+=len(piece); breaks.append(boundary)
            if any(b-a>max_group_chars for a,b in zip([0]+breaks,breaks)):
                breaks=repair_oversized_groups(breaks)
            texts=repair_semantic_boundaries([transcript[a:b] for a,b in zip([0]+breaks,breaks)])
            result=apply_semantic_groups(
                entries, texts, capacity, layout.get('subtitle_font_px'))
            cache_path.write_text(json.dumps(texts,ensure_ascii=False,indent=2))
            return result
        except (ValueError,KeyError,TypeError,RuntimeError) as exc:
            print('[意群重试]',str(exc),flush=True)
            error=f'\n第{attempt+1}次输出未通过严格校验：'+str(exc)+'。请重新按原文输出全部字幕。'
            if 'texts' in locals():
                error+='上次分屏文本：'+json.dumps(texts,ensure_ascii=False)
    raise CaptionPlanningUnavailable('字幕分屏未完成，保留原始转写供重试；'+error)


def make_ass(entries, path, W, H, card_style=False):
    """竖版适配:字号按高度算、抬到安全区。burner 的 make_ass 是按 16:9 调的,
    720x1280 下算出来才 29px,且会被平台底部 UI 遮住。

    字体必须可换:本机有 Microsoft YaHei,CI 的 ubuntu 上只有 Noto Sans CJK。
    libass 按名字找字体,找不到就 fallback 到无 CJK 字形的字体 -- 中文字幕
    会烧成一排豆腐块。workflow 里通过 ZH_FONT/EN_FONT 传入。"""
    # Chinese production uses the same semantic segmenter in every layout.
    if not any(e.get("en") for e in entries):
        from presentation import layout_for, write_ass
        return write_ass(entries, path, layout_for(W, H, card_style),
                         os.environ.get("ZH_FONT", "Microsoft YaHei"))
    font_zh = os.environ.get("ZH_FONT", "Microsoft YaHei")
    font_en = os.environ.get("EN_FONT", "Arial")
    vertical = H > W
    if vertical:
        # v3 对标版：字幕固定在人物画面下方的 874~1040 安全区，字号
        # 38~42px，最多两行；不能再沿用旧版 58px/贴底参数。
        zh = (40 if card_style and W == 720 and H == 1280
              else max(38, int(H * 0.05)))
        en = max(26, int(H * 0.035))
        mv = (240 if card_style and W == 720 and H == 1280
              else int(H * 0.09))
        zw = max(11, int(W * 14 / 720))
        ew = max(24, int(W * 34 / 720))
    else:
        # 横屏：字号占高 7%（用户 2026-08-25 反馈 5% 偏小，调大 ~1.4 倍）、
        # 底边距占高 6.5%（已修好贴底）、行宽按字号自洽。
        zh = max(36, int(H * 0.08))
        en = max(24, int(H * 0.06))
        mv = int(H * 0.065)
        zw = max(9, int(W * 0.80 / zh))
        ew = max(20, int(W * 0.85 / en))

    def wrap(t, n, cjk):
        t = (t or "").strip()
        if not t:
            return ""
        if cjk:
            # jieba 分词断行：断点落在词边界，避免双字词被硬切（如「看|好」被
            # 切成「看」+「好」）；标点归前一个词，避免孤立在行首（如「节能灯\n。」）。
            try:
                import jieba as _jieba
                import logging as _lg
                _jieba.setLogLevel(_lg.ERROR)
                segs = list(_jieba.cut(t))
            except ImportError:
                segs = list(t)
            merged = []
            for w in segs:
                if w and merged and w[0] in "，。！？；：、,.!?;":
                    merged[-1] += w
                else:
                    merged.append(w)
            lines, cur = [], ""
            for w in merged:
                if len(cur) + len(w) > n and cur:
                    lines.append(cur)
                    cur = w
                else:
                    cur += w
            if cur or not lines:
                lines.append(cur)
            if len(lines) > 2:
                # 重新按总长度均分成两行。旧逻辑把第 2 行以后的全部塞进
                # 第二行，libass 会再次自动折行，最终出现用户看到的三行。
                joined = "".join(lines)
                cut = max(1, (len(joined) + 1) // 2)
                lines = [joined[:cut], joined[cut:]]
            return "\\N".join(lines)
        words, lines, cur = t.split(), [], ""
        for w in words:
            if len(cur) + len(w) + 1 > n and cur:
                lines.append(cur); cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            lines.append(cur)
        if len(lines) > 2:
            joined = " ".join(lines)
            cut = max(1, len(joined) // 2)
            split = joined.rfind(" ", 0, cut + 1)
            split = split if split > 0 else cut
            lines = [joined[:split].strip(), joined[split:].strip()]
        return "\\N".join(lines)

    def card_subtitle_override(text):
        """把一/两行字幕作为整体固定在白色字幕区中央。

        WrapStyle=2 禁止 libass 产生隐式第三行；超长单行按字符数缩小，
        保证显式的两行仍留在 644px 安全宽度内。
        """
        if not (card_style and vertical):
            return "", text
        lines = text.split("\\N") if text else []
        longest = max((len(line) for line in lines), default=1)
        safe_width = SUBTITLE_REGION["width"] - 36
        fitted = min(zh, max(28, int(safe_width / max(1, longest))))
        center_x = SUBTITLE_REGION["x"] + SUBTITLE_REGION["width"] // 2
        center_y = SUBTITLE_REGION["y"] + SUBTITLE_REGION["height"] // 2
        return f"{{\\an5\\pos({center_x},{center_y})\\fs{fitted}}}", text

    def ts(s):
        return f"{int(s//3600)}:{int(s%3600//60):02d}:{s%60:05.2f}"

    # 音频卡沿用对标账号最易读的「黄字黑边」字幕；真实原画仍保持白字，避免
    # 在浅色/暖色现场画面上产生不必要的品牌化偏色。
    zh_color = "&H0000D7FF" if card_style else "&H00FFFFFF"
    zh_outline = 5 if card_style else 3
    L = ["[Script Info]", "ScriptType: v4.00+", "WrapStyle: 2",
         f"PlayResX: {W}", f"PlayResY: {H}",
         "", "[V4+ Styles]",
         "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
         "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
         "MarginL, MarginR, MarginV, Encoding",
         f"Style: EN,{font_en},{en},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
         f"-1,0,0,0,100,100,0,0,1,2,1,2,20,20,{mv + zh*2 + 10},1",
         f"Style: ZH,{font_zh},{zh},{zh_color},&H000000FF,&H00000000,"
         f"&H80000000,-1,0,0,0,100,100,0,0,1,{zh_outline},1,"
         f"{5 if card_style and vertical else 2},20,20,"
         f"{0 if card_style and vertical else mv},1",
         "", "[Events]",
         "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
         "Effect, Text"]
    for e in entries:
        a, b = ts(e["start_sec"]), ts(e["end_sec"])
        et, zt = wrap(e.get("en"), ew, False), wrap(e.get("zh"), zw, True)
        if et:
            L.append(f"Dialogue: 0,{a},{b},EN,,0,0,0,,{et}")
        if zt:
            override, zt = card_subtitle_override(zt)
            L.append(f"Dialogue: 0,{a},{b},ZH,,0,0,0,,{override}{zt}")
    Path(path).write_text("\n".join(L), encoding="utf-8-sig")


def probe(src, entries):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", entries, "-of", "csv=s=x:p=0", str(src)],
                       capture_output=True, text=True)
    return r.stdout.strip()


def video_size(src):
    """返回视频宽高；探测失败时抛质量错误，不能把未知尺寸当合格。"""
    raw = probe(src, "stream=width,height")
    try:
        width, height = (int(x) for x in raw.split("x"))
    except (TypeError, ValueError):
        raise VisualQualityError(f"无法取得视频分辨率：{raw!r}")
    if width <= 0 or height <= 0:
        raise VisualQualityError(f"视频分辨率异常：{width}x{height}")
    return width, height


def ensure_min_short_edge(src, minimum=MIN_SHORT_EDGE, label="成片"):
    """画质硬闸门。必须检查清理/裁切后的文件，而不只是原始下载。"""
    width, height = video_size(src)
    short = min(width, height)
    if short < minimum:
        raise VisualQualityError(
            f"{label}短边 {short} < {minimum}（{width}x{height}），画质不达标")
    return width, height


def brand_watermark_path():
    """返回生产水印；缺失时失败关闭，避免无品牌成片进入待投队列。"""
    path = BRAND_WATERMARK
    if not path.is_file() or path.stat().st_size < 1000:
        raise VisualQualityError(f"品牌水印文件缺失或异常：{path}")
    return path


def brand_overlay_filter(base_vf, width, height):
    """生成右上角品牌水印滤镜；宽度、透明度和边距均按画面自适应。"""
    ratio = min(0.25, max(0.08, BRAND_WATERMARK_WIDTH_RATIO))
    opacity = min(0.90, max(0.30, BRAND_WATERMARK_OPACITY))
    margin_ratio = min(0.08, max(0.01, BRAND_WATERMARK_MARGIN_RATIO))
    wm_width = max(64, int(width * ratio)) // 2 * 2
    margin_x = max(8, int(width * margin_ratio))
    margin_y = max(8, int(height * margin_ratio))
    return (
        f"[0:v]{base_vf}[base];"
        f"[1:v]format=rgba,colorchannelmixer=aa={opacity:.2f},"
        f"scale={wm_width}:-1[brand];"
        f"[base][brand]overlay=x=main_w-overlay_w-{margin_x}:"
        f"y={margin_y}:shortest=1[outv]"
    )


def _render_clean_preview(src, work, video_filter, duration, source_start=0.0):
    """渲染一小段清理后预览，供硬字幕二次复检。"""
    preview = Path(work) / "clean_preview.mp4"
    start = max(0.0, min(duration * 0.35, max(0.0, duration - 24.0)))
    clip_duration = max(4.0, min(24.0, duration - start))
    start += source_start
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}",
           "-t", f"{clip_duration:.2f}", "-i", str(src)]
    if video_filter:
        cmd += ["-vf", video_filter]
    cmd += ["-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            str(preview)]
    subprocess.run(cmd, check=True, capture_output=True)
    preview.with_suffix('.json').write_text(json.dumps(dict(
        source_start=start,duration=clip_duration,video_filter=video_filter),ensure_ascii=False))
    return preview


def build_clean_source_plan(src, work, width, height, duration,
                            raw_has_existing_subtitles):
    """决定如何得到只含我们一套字幕/水印的画面。

    顺序与「园园滚雪球」抽样成片一致：优先使用干净原画；可安全裁掉的
    先裁并实渲染复检；旧字幕/大标题无法安全移除时，仅保留已核验音频，
    重建品牌音频卡。不会把 delogo 当成大面积抹字工具。
    """
    logos = detect_corner_logos(src, strict=True)
    delogo = delogo_filter(logos, width, height) if logos else ""
    crop = safe_crop_plan(src, width, height)
    if crop:
        crop_w, crop_h, crop_x, crop_y = crop
    else:
        crop_w, crop_h, crop_x, crop_y = (
            width // 2 * 2, height // 2 * 2, 0, 0)
    filters = [x for x in (
        delogo, f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y}") if x]
    video_filter = ",".join(filters)

    if raw_has_existing_subtitles:
        crop_verified = False
        if crop:
            preview = _render_clean_preview(
                src, work, video_filter, duration)
            crop_verified = not has_existing_subtitles(preview)
        if crop_verified:
            strategy = "crop_delogo" if logos else "crop"
        elif ALLOW_AUDIO_CARD:
            strategy = "audio_card"
            crop_w, crop_h = AUDIO_CARD_WIDTH, AUDIO_CARD_HEIGHT
            video_filter = ""
        else:
            raise VisualQualityError(
                "源视频含持续内嵌字幕，且无法安全裁净；音频卡模式已关闭")
    elif crop:
        strategy = "crop_delogo" if logos else "crop"
    elif logos:
        strategy = "delogo"
    else:
        strategy = "direct"

    if min(crop_w, crop_h) < MIN_SHORT_EDGE:
        raise VisualQualityError(
            f"清理后预计短边 {min(crop_w, crop_h)} < {MIN_SHORT_EDGE}")
    return {
        "clean_strategy": strategy,
        "clean_video_filter": video_filter,
        "clean_output_resolution": {
            "width": crop_w, "height": crop_h,
            "short_edge": min(crop_w, crop_h),
        },
        "clean_filter_verified": True,
        "detected_corner_logos": logos,
    }


def selected_native_clean_plan(src, work, width, height, start, end):
    """Reassess the selected interview before forcing a face-only window.

    A mother-level card decision can come from an unrelated introduction or a
    missed narrow subtitle band. Keep camera cuts and original illustrations
    when the actual interval can be cropped cleanly at native resolution.
    """
    if width<=height or end<=start:return None
    work=Path(work);work.mkdir(parents=True,exist_ok=True)
    sample=work/'source-sample.mp4';cleaned=work/'clean-sample.mp4'
    proof=dict(version=1,source_start=start,source_end=end,
               source_resolution=[width,height],passed=False)
    try:
        subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(start),
            '-i',str(src),'-t',str(end-start),'-vf','fps=2','-an',
            '-c:v','libx264','-preset','ultrafast','-crf','23','-threads','2',str(sample)],
            check=True,capture_output=True,timeout=180)
        # This fallback must not interpret an unavailable OCR service as a
        # clean frame. It is useful even when the old mother cache says card.
        before=ocr_row_coverage(sample,frames=12,strict=True)
        crop=safe_crop_plan(sample,width,height,coverage=before)
        if crop is None:
            raise VisualQualityError('选段没有可验证的原画裁切方案')
        cw,ch,cx,cy=crop
        if min(cw,ch)<MIN_SHORT_EDGE:
            raise VisualQualityError('原画裁切后短边不足，不能放大冒充清晰源')
        logos=detect_corner_logos(sample,frames=12,strict=True)
        filters=[delogo_filter(logos,width,height) if logos else '',
                 f'crop={cw}:{ch}:{cx}:{cy}']
        vf=','.join(x for x in filters if x)
        subprocess.run(['ffmpeg','-y','-loglevel','error','-i',str(sample),
            '-vf',vf,'-an','-c:v','libx264','-preset','ultrafast','-crf','23',
            '-threads','2',str(cleaned)],check=True,capture_output=True,timeout=120)
        if has_existing_subtitles(cleaned,strict=True,frames=12):
            raise VisualQualityError('裁切后仍有持续原字幕或大标题')
        if detect_corner_logos(cleaned,frames=12,strict=True):
            raise VisualQualityError('裁切后仍有来源角标')
        proof.update(passed=True,video_filter=vf,crop_xywh=[cx,cy,cw,ch],
                     sampled_frames=12,raw_row_coverage=before,
                     clean_row_coverage=ocr_row_coverage(cleaned,frames=12,strict=True),
                     old_subtitles_removed=True,external_logos_removed=True)
        print(f'[原画适配] {start:.2f}–{end:.2f}秒可裁净旧字幕，保留横屏切镜和原始插图：{cw}x{ch}',flush=True)
        return dict(clean_strategy='crop_delogo' if logos else 'crop',
            clean_video_filter=vf,clean_output_resolution=dict(width=cw,height=ch),
            native_context_proof=proof,geometry_source=str(sample))
    except (VisualQualityError,subprocess.SubprocessError,OSError,ValueError) as exc:
        proof['reason']=str(exc)
        print('[原画适配] 未通过，继续按既有版式检查：'+str(exc),flush=True)
        return None
    finally:
        (work/'native-plan.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))


def _inside_brand_watermark_region(box, width, height):
    """判断 OCR 框是否属于我们刚叠加的右上角水印，供外部角标复检排除。"""
    x0, y0, x1, y1 = box
    ratio = min(0.25, max(0.08, BRAND_WATERMARK_WIDTH_RATIO))
    margin_ratio = min(0.08, max(0.01, BRAND_WATERMARK_MARGIN_RATIO))
    # 当前透明 PNG 的宽高比约 2.69；预留少量容差覆盖描边与 OCR 分框。
    aspect = 2057 / 765
    wm_height_ratio = (width * ratio / aspect) / max(1, height)
    region_x0 = 1.0 - margin_ratio - ratio - 0.03
    region_y1 = min(0.35, margin_ratio + wm_height_ratio + 0.04)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return region_x0 <= cx <= 1.0 and 0.0 <= cy <= region_y1


def detect_external_logos_after_render(final, strategy, width, height):
    """复检真实原画；音频卡是自有模板，不把模板文字当第三方角标。"""
    if strategy == "audio_card":
        return []
    remaining = detect_corner_logos(final, frames=6, strict=True)
    return [box for box in remaining
            if not _inside_brand_watermark_region(box, width, height)]


def reviewed_source_live_crop(source_report, width, height):
    """A measured crop is a render hint, bound to exact source bytes, never a gate exemption."""
    path=Path(__file__).with_name('source_crop_profiles.json')
    if not path.exists():
        return None
    row=json.loads(path.read_text()).get(source_report.get('source_sha256'))
    if not row or row.get('resolution') != [width,height]:
        return None
    x,y,w,h=row['crop_xywh']
    if (any(type(v) is not int or v%2 for v in (x,y,w,h))
            or min(x,y)<0 or min(w,h)<=0 or x+w>width or y+h>height
            or abs(w/h-LIVE_REGION['width']/LIVE_REGION['height'])>.005):
        raise VisualQualityError('已测量取景区域无效')
    return f"crop={w}:{h}:{x}:{y},scale={LIVE_REGION['width']}:{LIVE_REGION['height']}:flags=lanczos,setsar=1"


def select_interview_face(faces, width, height):
    """Ignore small background/cloth patterns before choosing the interview guest."""
    candidates=[tuple(map(int,b)) for b in faces
                if b[2] >= max(48, width*.045)
                and height*.12 <= b[1]+b[3]/2 <= height*.65]
    if not candidates:
        return None
    largest=max(b[2]*b[3] for b in candidates)
    # Comparable faces can be the interviewer and right-hand guest; a tiny
    # rightmost false positive must never win over the actual speaker's face.
    candidates=[b for b in candidates if b[2]*b[3] >= largest*.70]
    return max(candidates,key=lambda b:b[0]+b[2]/2)


def audio_card_live_crop(width, height, src=None, at=None):
    """为横屏原片生成与卡片窗口同宽高比的裁切；竖屏源禁止硬嵌。"""
    if width <= height:
        return None
    target_ratio = LIVE_REGION["width"] / LIVE_REGION["height"]
    if src is not None:
        import cv2
        import statistics
        cap=cv2.VideoCapture(str(src)); boxes=[]
        detector=_cascade(cv2.data.haarcascades+'haarcascade_frontalface_default.xml')
        for seconds in ((float(at or 0)+.5),(float(at or 0)+2),(float(at or 0)+4)):
            cap.set(cv2.CAP_PROP_POS_MSEC,seconds*1000)
            ok,frame=cap.read()
            if not ok: continue
            faces=detector.detectMultiScale(cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY),1.1,4,minSize=(48,48))
            face=select_interview_face(faces,width,height)
            if face is not None:
                boxes.append(face)
        cap.release()
        if len(boxes)>=2:
            fx,fy,fw,fh=[statistics.median([b[k] for b in boxes]) for k in range(4)]
            ch=min(height,int(fh*1.8))//2*2; cw=min(width,int(ch*target_ratio))//2*2
            if cw>=160 and ch>=120:
                cx=max(0,min(width-cw,int(fx+fw/2-cw/2)))//2*2
                cy=max(0,min(height-ch,int(fy-fh*.38)))//2*2
                return f"crop={cw}:{ch}:{cx}:{cy},scale={LIVE_REGION['width']}:{LIVE_REGION['height']}:flags=lanczos,setsar=1"
    # 横屏访谈优先取人物上半身，主动避开底部常驻字幕/栏目条。
    # 旧版取 78% 高度会把 0.73~0.95H 的来源条带一起带进真人窗口，
    # 导致本来可用的 1080P 双人访谈全部退回 audio_card。
    crop_h = min(height, int(height * 0.60)) // 2 * 2
    crop_w = min(width, int(crop_h * target_ratio)) // 2 * 2
    if crop_w > width:
        crop_w = width // 2 * 2
        crop_h = int(crop_w / target_ratio) // 2 * 2
    crop_x = int((width - crop_w) * 0.72) // 2 * 2
    crop_y = int(height * 0.05) // 2 * 2
    crop_x = max(0, min(crop_x, width - crop_w))
    crop_y = max(0, min(crop_y, height - crop_h))
    return (f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            f"scale={LIVE_REGION['width']}:{LIVE_REGION['height']}:"
            "flags=lanczos,setsar=1")


def partial_qr_finder_score(gray):
    """Recognize a remaining QR finder in cropped edge codes that cannot decode.

    Match the standard 7x7 dark/light/dark finder at several pixel scales;
    callers require it in multiple frames, rather than trusting one texture.
    """
    import cv2
    import numpy as np
    template = np.zeros((7, 7), dtype=np.uint8)
    template[1:6, 1:6] = 255
    template[2:5, 2:5] = 0
    edge = min(94, gray.shape[0], gray.shape[1])
    corners = (gray[:edge,:edge], gray[:edge,-edge:],
               gray[-edge:,:edge], gray[-edge:,-edge:])
    best = 0.0
    for corner in corners:
        for size in range(10, min(37,edge+1), 2):
            pattern = cv2.resize(template, (size,size), interpolation=cv2.INTER_NEAREST)
            pattern = cv2.GaussianBlur(pattern,(3,3),0.8)
            _, score, _, point = cv2.minMaxLoc(cv2.matchTemplate(corner,pattern,cv2.TM_CCOEFF_NORMED))
            x,y = point
            # Flat fields and weak incidental texture are not QR candidates.
            if float(corner[y:y+size,x:x+size].std()) >= 35:
                best = max(best,float(score))
    return best


def verify_live_region_after_render(final, frames=6, api_key=None,
                                    speaker='林园', reference=None, extra_times=(), actor_times=()):
    """复检嵌入的真人动态区：拒绝黑边、二维码和稳定外部角标。"""
    import tempfile
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VisualQualityError(f"真人动态区复检依赖不可用：{exc}") from exc

    cap = cv2.VideoCapture(str(final))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS)) or 30
    junction_indices={min(total-1,max(0,int(t*fps))) for t in extra_times}
    actor_indices={min(total-1,max(0,int(t*fps))) for t in actor_times}
    sample_indices=sorted({int(total*(i+.5)/max(1,frames)) for i in range(frames)}|junction_indices|actor_indices)
    frame_paths = []
    black_edge_hits = 0
    qr_hits = 0
    partial_qr_hits = 0
    full_face_frames = 0
    tmp = Path(final).parent / "_tmp" / ("live-region-"+Path(final).stem)
    tmp.mkdir(parents=True,exist_ok=True)
    got = 0
    try:
        qr = cv2.QRCodeDetector()
        for i,frame_index in enumerate(sample_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES,frame_index)
            ok, frame = cap.read()
            if not ok:
                continue
            x, y = LIVE_REGION["x"], LIVE_REGION["y"]
            w, h = LIVE_REGION["width"], LIVE_REGION["height"]
            region = frame[y:y + h, x:x + w]
            if region.shape[:2] != (h, w):
                raise VisualQualityError("真人动态区尺寸不完整")
            got += 1
            fp = tmp / f"frame-{i}.jpg"
            if not cv2.imwrite(str(fp), region):
                raise VisualQualityError("真人动态区抽帧失败")
            frame_paths.append(fp)

            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            partial_qr_hits += int(partial_qr_finder_score(gray) >= 0.70)
            dark_columns = np.mean(gray < 18, axis=0) > 0.92
            edge = max(8, int(w * 0.08))
            if (dark_columns[:edge].mean() > 0.45
                    or dark_columns[-edge:].mean() > 0.45):
                black_edge_hits += 1
            try:
                _decoded, points, _straight = qr.detectAndDecode(region)
                from presentation import qr_is_plausible, qr_candidate_has_finders
                # OpenCV may return repeated corners or vertices far outside
                # the image. Those cannot describe a QR; keep decoded codes,
                # plausible complete candidates and the separate finder gate.
                if _decoded or (points is not None and qr_is_plausible(points,w,h)
                                and qr_candidate_has_finders(region,points)):
                    qr_hits += 1
            except cv2.error:
                pass
    finally:
        cap.release()

    if got < max(3, frames // 2):
        raise VisualQualityError("真人动态区复检抽帧不足")
    if black_edge_hits >= max(2, got // 2):
        raise VisualQualityError("真人动态区检出持续黑边/错误取景")
    if qr_hits or partial_qr_hits >= 2:
        raise VisualQualityError("真人动态区检出来源二维码（含裁切残留定位图案）")
    from live_tracking import complete_face
    detector_path,_=_local_face_models()
    detector=cv2.FaceDetectorYN.create(str(detector_path),'',(632,470),
        score_threshold=.8,nms_threshold=.3,top_k=5000)
    geometry=[]
    for fp in frame_paths:
        region=cv2.imread(str(fp));h,w=region.shape[:2]
        detector.setInputSize((w,h));_,found=detector.detect(region)
        full_face=found is not None and any(complete_face(face,w,h) for face in found)
        full_face_frames+=int(full_face)
        index=int(fp.stem.split('-')[-1])
        geometry.append(dict(frame=index,time_sec=round(sample_indices[index]/fps,3),
            full_face=bool(full_face),boxes=[] if found is None else
            [[round(float(v),2) for v in face[:4]] for face in found]))
    (tmp/'geometry.json').write_text(json.dumps(dict(engine='opencv_yunet_cpu',
        full_face_frames=full_face_frames,sampled_frames=got,frames=geometry),indent=2))
    for row in geometry:
        if sample_indices[row['frame']] in junction_indices and not row['full_face']:
            raise VisualQualityError('剪接附近真人取景缺少完整人脸或头顶余量')
    actor_faces=sum(row['full_face'] for row in geometry if sample_indices[row['frame']] in actor_indices)
    if actor_indices and (len(actor_indices)!=6 or actor_faces<5):
        raise VisualQualityError('访谈人物镜头完整人脸复检不足5/6')
    if not actor_indices and full_face_frames < max(3, int(got*.8+.999)):
        raise VisualQualityError(f"真人窗口完整人脸抽帧不足：{full_face_frames}/{got}，拒绝裁头/裁下巴或空镜")
    logos = detect_corner_logos_in_images(frame_paths, stable_ratio=0.5,
                                          max_area=0.04)
    if logos:
        raise VisualQualityError(f"真人动态区仍有稳定来源角标：{logos}")
    # Rotating uploader watermarks can move between corners and evade a stable
    # position cluster. Reuse the multi-frame visual review on the cropped final
    # live region. Our own title/disclaimer/brand are all outside this rectangle.
    if reference:
        verdict=_local_identity_verdict(Path(reference),frame_paths,speaker)
        if not identity_verdict_passes(verdict,len(frame_paths)):
            raise VisualQualityError('真人动态区本地人物复检未确认林园本人：'+verdict['reason'])
    return {"live_region_verified": True, "no_qr_verified": True,
            "partial_qr_verified": True, "full_face_frames": full_face_frames,
            "no_black_bars_verified": True}


def run_source_quality_gate(src, work, speaker, api_key, report_path=None):
    """下载后的素材闸门；任何 ASR、切片和编码开始前必须通过。"""
    report_path = Path(report_path or (work / "source_quality.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # #630/#651: the visible subject is Lin Yuan, but the audio explicitly
    # belongs to his friend Wang Hong. Face matching cannot establish authorship.
    source_hash=_file_sha256(src)
    if speaker=='林园' and source_hash=='87e4dcea6b1292f184edb15188c38c4075a4fa94fc1b272ac2fc385f862faff1':
        report=dict(quality_gate_version=QUALITY_GATE_VERSION,source_sha256=source_hash,
            speaker=speaker,passed=False,retryable=False,failure_stage='source-quality',
            reason='已核对原始转写：王红讲述自己与林园的经历，非林园本人发言，禁止错误署名')
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return report
    # An obsolete cached verdict is a cache miss, not a verdict about this
    # source. Re-run today's full gate; never upgrade an old pass flag.
    try:
        cached=verified_source_evidence(src,speaker)
    except (VisualQualityError, OSError, ValueError, KeyError) as exc:
        print(f'[素材复用] 旧证据不可复用，重新质检：{exc}')
        cached=None
    if cached is not None:
        _download_speaker_reference(speaker,work)
        report_path.write_text(json.dumps(cached,ensure_ascii=False,indent=2))
        print('[素材复用] 当前文件与已实际核验的母片逐字节一致；成片仍逐条检查')
        return cached
    report = {
        "quality_gate_version": QUALITY_GATE_VERSION,
        "source_sha256": _file_sha256(src),
        "speaker": speaker,
        "passed": False,
    }
    try:
        try:
            duration = float(probe(src, "format=duration") or 0)
        except (TypeError, ValueError):
            duration = 0
        report["duration_sec"] = round(duration, 2)
        if not (SOURCE_MIN_DURATION <= duration <= SOURCE_MAX_DURATION):
            raise VisualQualityError(
                f"素材时长 {duration:.0f}s 不在 "
                f"[{SOURCE_MIN_DURATION},{SOURCE_MAX_DURATION}]")
        width, height = ensure_min_short_edge(src, label="原始素材")
        report["resolution"] = {
            "width": width, "height": height, "short_edge": min(width, height),
        }
        report["raw_has_existing_subtitles"] = has_existing_subtitles(src)
        report["visual_identity"] = verify_source_identity(
            src, work, speaker, api_key)
        report.update(build_clean_source_plan(
            src, work, width, height, duration,
            report["raw_has_existing_subtitles"]))
        # 该字段描述进入成片画布后的状态，不再等同于原文件状态。
        report["has_existing_subtitles"] = False
        report["passed"] = True
    except VisualQualityError as e:
        report["reason"] = str(e)
        report['retryable']=isinstance(e,VisualResponseFormatError) or str(e).startswith('人物 VLM 校验不可用')
        report['failure_stage']='quality-service' if report['retryable'] else 'source-quality'
    except Exception as e:
        # 质检服务未知异常也必须失败关闭，不能把“没检成”当成“已合格”。
        report["reason"] = f"素材质检不可用：{e}"
        report['retryable']=True
        report['failure_stage']='quality-service'
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8")
    return report


def verified_source_evidence(src,speaker):
    """Reuse an immutable actual source report, never an inferred pass flag."""
    manifest=BASE/'source_quality_evidence'/'manifest.json'
    if not manifest.exists():
        return None
    source_sha=_file_sha256(src)
    evidence=json.loads(manifest.read_text()).get(source_sha)
    if not evidence:
        return None
    path=manifest.parent/(source_sha+'.json')
    if _file_sha256(path)!=evidence['report_sha256']:
        raise VisualQualityError('实际源片检查证据校验和不符')
    report=load_source_quality_report(src,path)
    identity=report.get('visual_identity') or {}
    if (report.get('speaker')!=speaker or identity.get('version')!=VISUAL_GATE_VERSION
            or not identity_verdict_passes(identity,VISUAL_SAMPLE_COUNT)
            or not SOURCE_MIN_DURATION<=float(report['duration_sec'])<=SOURCE_MAX_DURATION):
        raise VisualQualityError('实际源片检查证据与当前规则不符')
    report['reused_actual_evidence']=evidence
    return report


def load_source_quality_report(src, report_path):
    """复用工作流前置质检结果，并防止报告被用于另一份素材。"""
    try:
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except Exception as e:
        raise VisualQualityError(f"素材质检报告不可读：{e}") from e
    if report.get("quality_gate_version") != QUALITY_GATE_VERSION:
        raise VisualQualityError("素材质检报告版本过旧")
    if report.get("source_sha256") != _file_sha256(src):
        raise VisualQualityError("素材质检报告与当前视频不匹配")
    if report.get('speaker')=='林园' and report.get('source_sha256')=='87e4dcea6b1292f184edb15188c38c4075a4fa94fc1b272ac2fc385f862faff1':
        raise VisualQualityError('已核对：该源为王红讲述林园，历史人脸通过记录不能证明声音归属')
    if report.get("passed") is not True:
        raise VisualQualityError(report.get("reason") or "素材质检未通过")
    if report.get("has_existing_subtitles") is not False:
        raise VisualQualityError("素材缺少无内嵌字幕证明")
    if report.get("clean_strategy") not in {
            "direct", "delogo", "crop", "crop_delogo", "audio_card"}:
        raise VisualQualityError("素材缺少可复现的干净画面策略")
    if report.get("clean_filter_verified") is not True:
        raise VisualQualityError("素材清理方案未经复检")
    clean_resolution = report.get("clean_output_resolution") or {}
    if int(clean_resolution.get("short_edge") or 0) < MIN_SHORT_EDGE:
        raise VisualQualityError("素材清理后分辨率不达标")
    if not report.get("visual_identity"):
        raise VisualQualityError("素材缺少人物核验记录")
    return report


def _file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _simhash64(features):
    """对字符串特征做 64 位 SimHash；用于容忍少量 ASR 错字和标点差异。"""
    weights = [0] * 64
    for feature in features:
        value = int.from_bytes(hashlib.blake2b(
            feature.encode("utf-8"), digest_size=8).digest(), "big")
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    out = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            out |= 1 << bit
    return f"{out:016x}"


def transcript_fingerprints(text, window=96, step=48, limit=96):
    """生成重叠转写指纹；不同平台、不同画质但说的是同一段话仍能命中。"""
    clean = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text or "").lower()
    if not clean:
        return []
    starts = list(range(0, max(1, len(clean) - window + 1), step))
    tail = max(0, len(clean) - window)
    if tail not in starts:
        starts.append(tail)
    result = []
    for start in starts:
        chunk = clean[start:start + window]
        grams = [chunk[i:i + 3] for i in range(max(1, len(chunk) - 2))]
        sig = _simhash64(grams)
        if sig not in result:
            result.append(sig)
    return result[:limit]


def transcript_ngram_fingerprints(text, n=5, limit=96):
    """确定性采样字符 n-gram；适合判断短片是否是长内容中的一个片段。"""
    clean = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text or "").lower()
    if not clean:
        return []
    grams = {clean[i:i + n] for i in range(max(1, len(clean) - n + 1))}
    hashes = sorted({hashlib.blake2b(g.encode("utf-8"), digest_size=8).hexdigest()
                     for g in grams})
    sampled = [h for h in hashes if int(h[-2:], 16) < 32]  # 固定抽约 1/8
    # 很短的金句采样后可能不足，回退全量；仍受 limit 控制，避免 state 膨胀。
    return (sampled if len(sampled) >= 8 else hashes)[:limit]


def _video_frame_fingerprints(src, count=12):
    """均匀抽帧 dHash；对重新编码、轻微缩放较稳定。"""
    try:
        import cv2
    except ImportError as e:
        raise VisualQualityError(f"缺少 OpenCV，无法生成视频指纹：{e}") from e
    cap = cv2.VideoCapture(str(src))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        cap.release()
        raise VisualQualityError("无法读取成片帧数，不能生成视频指纹")
    hashes = []
    for i in range(count):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / count))
        ok, frame = cap.read()
        if not ok:
            continue
        h, w = frame.shape[:2]
        # 去掉最外侧 5%，降低跨平台轻微裁边对指纹的影响。
        x, y = max(1, int(w * 0.05)), max(1, int(h * 0.05))
        if w > x * 2 and h > y * 2:
            frame = frame[y:h - y, x:w - x]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        bits = small[:, 1:] > small[:, :-1]
        value = 0
        for bit in bits.flatten():
            value = (value << 1) | int(bit)
        hashes.append(f"{value:016x}")
    cap.release()
    if len(hashes) < min(4, count):
        raise VisualQualityError(f"视频指纹抽帧不足：{len(hashes)}/{count}")
    return hashes


def _audio_fingerprints(src, limit=96):
    """使用 Chromaprint 生成声纹；可识别重新编码、换容器后的同一段音频。"""
    import struct
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-map", "0:a:0",
         "-f", "chromaprint", "-fp_format", "raw", "pipe:1"],
        capture_output=True)
    raw = r.stdout[:len(r.stdout) // 4 * 4]
    if r.returncode != 0 or len(raw) < 16:
        detail = r.stderr.decode("utf-8", "ignore")[:120]
        raise VisualQualityError(f"Chromaprint 音频指纹失败：{detail}")
    values = struct.unpack(f"<{len(raw) // 4}I", raw)
    # 取排序后的唯一 token，既控制 meta/state 体积，又让同一片段的子集仍可命中。
    return [f"{value:08x}" for value in sorted(set(values))[:limit]]


def build_content_fingerprints(src, transcript_text):
    """成片三重指纹：精确文件、画面、声音、转写内容。"""
    return {
        "version": FINGERPRINT_VERSION,
        "sha256": _file_sha256(src),
        "video_dhash": _video_frame_fingerprints(src),
        "audio_chromaprint": _audio_fingerprints(src),
        "transcript_simhash": transcript_fingerprints(transcript_text),
        "transcript_ngrams": transcript_ngram_fingerprints(transcript_text),
        "transcript_chars": len(re.sub(
            r"[^0-9A-Za-z\u4e00-\u9fff]+", "", transcript_text or "")),
    }


def _title_text(value):
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", value or "")


def title_quality_error(title, speaker, transcript_text, existing_titles=None,
                        require_quote=True):
    """程序化标题闸门：拦 ASR 脏词、摘要腔、编造和批内撞题。"""
    title = re.sub(r"\s+", " ", title or "").strip()
    if not re.match(rf"^(?:股神)?{re.escape(speaker)}[：:]", title):
        return f"标题必须以「{speaker}：」或「股神{speaker}：」开头"
    if any(word in title for word in TITLE_ASR_BLACKLIST):
        return "标题命中 ASR 污染词"
    if editorial.title_attribution_error(title) or any(phrase in title for phrase in ('请问','想问林总','分享一下','您如何','您认为','林总能')):
        return '标题引用了主持人的提问，不能归为嘉宾原话'
    if re.search(r"https?://|www\.|t\.cn/|@[\w\u4e00-\u9fff]+", title, re.I):
        return "标题含链接或引流信息"
    compact = _title_text(title)
    if not 12 <= len(compact) <= 62:
        return f"标题长度 {len(compact)} 不在 12~62 字"
    normalized = re.sub(
        rf"^(?:股神)?{re.escape(speaker)}[：:]", "", title).strip()
    body = _title_text(normalized)
    transcript = _title_text(transcript_text)
    if require_quote and len(body) >= 6:
        # 允许删除口水词或合并相邻句，但至少要有一段 6 字原话可回溯。
        if not any(body[i:i + 6] in transcript
                   for i in range(max(1, len(body) - 5))):
            return "标题缺少可回溯到所选字幕的连续原话"
    for previous in existing_titles or []:
        a, b = _title_text(previous), compact
        if a and difflib.SequenceMatcher(None, a, b).ratio() >= 0.84:
            return "标题与本批已有标题过于相似"
    return None


def _fallback_quote_title(cues, sel, speaker):
    from headline_policy import title_candidates
    transcript=''.join(cues[i]['text'] for i in sel)
    for title in title_candidates(transcript,speaker):
        if not title_quality_error(title,speaker,transcript):
            return title
    raise VisualQualityError('没有可直接引用的完整标题句，不能按字符截断凑标题')


def copywrite(cues, sel, speaker, occasion, api_key, work, suffix="",
              existing_titles=None, require_quote=True, reviewed_title=None, reviewed_cover=None):
    """LLM 生成 B站标题/简介/标签(参考原库 scripts/copywrite.py)。

    钩子式标题:prompt 要求带反常识/数字/冲突钩子（对标竞品高播放标题），但严禁编造，结果落 meta.json,
    投稿脚本优先读这里,不再用 occasion 硬拼。
    suffix 区分长视频拆多条的各段缓存（否则每段复用同一条文案）。
    """
    cache = work / f"copywrite{suffix}.json"
    transcript_text = "".join(cues[i]["text"] for i in sel)
    from headline_policy import attach_copy
    copy_identity={'version':4,'transcript_sha256':editorial.text_digest(transcript_text),
                   'speaker':speaker,'occasion':occasion,'reviewed_title':reviewed_title,
                   **({'reviewed_cover':reviewed_cover} if reviewed_cover else {})}
    if suffix=='_full':
        minutes=max(1,round((cues[sel[-1]]['end']-cues[sel[0]]['start'])/60))
        result=attach_copy(dict(title=f'{speaker}：{minutes}分钟完整访谈原声',
            desc=f'{speaker}在{occasion}的完整访谈原声。',tags=[speaker,'完整访谈'],
            copy_identity=copy_identity,title_quality_verified=True),transcript_text,speaker)
        cache.write_text(json.dumps(result,ensure_ascii=False))
        return result
    if reviewed_title:
        error=title_quality_error(reviewed_title,speaker,transcript_text,existing_titles,
                                  require_quote=require_quote)
        if error:
            raise VisualQualityError('编辑标题未通过原话校验：'+error)
        result=dict(title=reviewed_title,desc=f'{speaker}在{occasion}的公开发言选段。',
                    tags=[speaker,'价值投资'],copy_identity=copy_identity,title_quality_verified=True)
        result=attach_copy(result,transcript_text,speaker,existing_titles,reviewed_cover)
        cache.write_text(json.dumps(result,ensure_ascii=False))
        return result
    if cache.exists():
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            error = title_quality_error(
                cached.get("title"), speaker, transcript_text,
                existing_titles, require_quote=require_quote)
            if require_quote and not error:
                from headline_policy import complete,body
                if not complete(body(cached.get('title'),speaker)):
                    error='缓存标题不是可独立引用的完整原话'
            if not error and cached.get('copy_identity')==copy_identity:
                cached=attach_copy(cached,transcript_text,speaker,existing_titles)
                cache.write_text(json.dumps(cached,ensure_ascii=False,indent=2))
                return cached
            print(f"[文案] 缓存标题未通过 v3 闸门，重新生成：{error}")
        except ValueError:
            pass
    sample = "\n".join(cues[i]["text"] for i in sel)
    if os.environ.get('SOURCE_EDITORIAL_FIRST') == 'true':
        from headline_policy import title_candidates
        for title in title_candidates(transcript_text,speaker,existing_titles):
            if title_quality_error(title,speaker,transcript_text,existing_titles,require_quote=require_quote):continue
            result=attach_copy(dict(title=title,desc=f'{speaker}在{occasion}的公开发言选段。',
                tags=[speaker,'价值投资'],copy_identity=copy_identity,title_quality_verified=True),
                transcript_text,speaker,existing_titles)
            cache.write_text(json.dumps(result,ensure_ascii=False,indent=2))
            print(f'[原文文案] {title}',flush=True)
            return result
    prompt = f"""这是{speaker}在「{occasion}」发言的字幕节选:

{sample}

为它生成 B站投稿文案。

【完整观点标题】
先读完上面整段内容，识别主要论点，再选能独立理解的原话作为标题。
不能只复制开头的主持人问题、称呼或半句回应；不得靠常识修正含糊识别内容。
标题与整条视频的论点、理由、限定条件必须一致，保留否定和数字。

标题要求（严格遵守）:
1. 必须以「{speaker}：」或「股神{speaker}：」开头
2. 冒号后面必须是**从字幕里摘出来的他本人的原话**（可精简去口水词、可合并相邻两句，但不能改变意思、不能替换成书面语）
3. 优先用一句完整原话交代具体对象与明确观点，通常18~36字即可；必要时保留更长的限定条件。
   不强制数字或冲突，不添加原文没有的回报、身家、价格或态度。数字不是流量保证。
   避免把同一科技风险观点换一种说法；不要写“机遇与挑战”等空泛总结。
4. 保留口语感和态度（「我」「你」「不可能」「肯定」这类词不要删）
5. 严禁编造：字幕里没说的话、没出现的数字，一律不许写
6. 不要加任何后缀（不要「｜{speaker}」这种尾巴）

简介:
1. 100字以内，第一人称视角陈述内容要点，末尾注明来源场合
2. 可以补一句「看点」提示
3. ⚠️ 严禁出现任何链接或引流信息：不要写 URL、http、https、t.cn 短链、
   www 开头的地址、@某某账号、"来源见链接"之类。只写文字内容本身。
   （2026-09-03 用户明确要求：简介里不要放原始链接）

只输出 JSON:
{{{{"title":"标题","desc":"简介","tags":["标签","最多5个","含主讲人姓名"]}}}}"""
    d, last_error = None, ""
    for attempt in range(3):
        retry = ("" if not last_error else
                 f"\n上一次标题未通过程序质检：{last_error}。请修正后重新输出 JSON。")
        try:
            out = llm([{"role": "user", "content": prompt + retry}],
                      api_key, temperature=0.35)
            m = re.search(r"\{.*\}", out, re.S)
            candidate = json.loads(m.group(0))
            last_error = title_quality_error(
                candidate.get("title"), speaker, transcript_text,
                existing_titles, require_quote=require_quote)
            if not last_error and require_quote:
                from headline_policy import compact, body, complete
                if not complete(body(candidate.get('title'),speaker)):
                    last_error='标题是未完成的回应或句子片段，需要完整原话观点'
                elif compact(body(candidate.get('title'),speaker)) not in compact(transcript_text):
                    last_error='标题必须完整回溯原文，不能靠六字相同混入新数字或断言'
            if not last_error:
                d = candidate
                break
        except Exception as exc:
            last_error = f"文案 JSON 解析失败：{exc}"
    if d is None:
        d = {"title": _fallback_quote_title(cues, sel, speaker),
             "desc": f"{speaker}在{occasion}的公开发言精选。",
             "tags": [speaker, "价值投资"]}
        last_error = title_quality_error(
            d["title"], speaker, transcript_text, existing_titles,
            require_quote=require_quote)
        if last_error:
            raise VisualQualityError(f"标题连续三次未通过质量闸门：{last_error}")
    # 兜底清洗：prompt 说了不许带链接，但 LLM 不一定听话，程序层再洗一遍
    if d.get("desc"):
        clean_desc = re.sub(r"https?://\S+|www\.\S+|t\.cn/\S+|@[\w\u4e00-\u9fa5]{2,20}", "", d["desc"])
        clean_desc = re.sub(r"[（(]\s*[）)]|\s{2,}", " ", clean_desc).strip(" ，,、;；")
        if clean_desc != d["desc"]:
            print(f"[文案] 简介已清除链接/引流信息")
            d["desc"] = clean_desc
    d.setdefault("tags", [speaker])
    d['copy_identity']=copy_identity
    d["title_quality_verified"] = True
    d=attach_copy(d,transcript_text,speaker,existing_titles)
    cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"[文案] 标题:{d['title']}")
    return d


def _cascade(path):
    """兼容 OpenCV 4 / 5 取 CascadeClassifier。OpenCV 5 把它挪出了主命名空间。"""
    import cv2
    for getter in (lambda: cv2.CascadeClassifier,
                   lambda: cv2.objdetect.CascadeClassifier,
                   lambda: cv2.legacy.CascadeClassifier):
        try:
            return getter()(path)
        except AttributeError:
            continue
    raise RuntimeError("当前 OpenCV 版本找不到 CascadeClassifier（4/5 命名空间均未命中）")


def _sc_face_index(ttc_path, want_name="Noto Sans CJK SC"):
    """TTC 合集里找指定子字体下标。原库踩过的坑:默认取第 0 个是 JP 字形,
    简体字会「细一号」。"""
    try:
        from fontTools.ttLib import TTCollection
        for i, f in enumerate(TTCollection(ttc_path).fonts):
            if want_name in f["name"].toUnicode() if isinstance(f["name"].toUnicode(), str) else False:
                return i
            names = f["name"].names
            if any(want_name in (n.toUnicode() if hasattr(n, "toUnicode") else "") for n in names):
                return i
    except Exception:
        pass
    return 0


def wrap_cover_title(title, chars_per_line, max_lines=3):
    """按词边界折行，并且绝不静默丢掉标题尾部。"""
    if len(title) <= chars_per_line:
        return [title]
    try:
        import jieba as _jieba
        import logging as _lg
        _jieba.setLogLevel(_lg.ERROR)
        words = list(_jieba.cut(title))
    except ImportError:
        words = list(title)
    lines, current = [], ""
    for word in words:
        while len(word) > chars_per_line:
            head, word = word[:chars_per_line], word[chars_per_line:]
            if current:
                lines.append(current)
                current = ""
            lines.append(head)
        if len(current) + len(word) > chars_per_line and current:
            lines.append(current)
            current = word
        else:
            current += word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        raise VisualQualityError(
            f"封面标题 {len(title)} 字无法在 {max_lines} 行内完整排版")
    return lines


def select_verified_cover_face(frames, reference_path):
    """Select the actual matching face, never the most frequent/largest other face."""
    import cv2
    if not reference_path or not Path(reference_path).is_file():
        raise VisualQualityError('现场封面缺少已核验人物参考图')
    detector_path,recognizer_path=_local_face_models()
    detector=cv2.FaceDetectorYN.create(str(detector_path),'',(320,320),score_threshold=.80,
                                      nms_threshold=.3,top_k=5000)
    recognizer=cv2.FaceRecognizerSF.create(str(recognizer_path),'')
    def faces(path):
        image=cv2.imread(str(path))
        if image is None:return None,[]
        h,w=image.shape[:2];detector.setInputSize((w,h))
        _,found=detector.detect(image)
        return image,([] if found is None else found)
    ref,refs=faces(reference_path)
    if not len(refs):raise VisualQualityError('封面参考照没有可识别人脸')
    rf=max(refs,key=lambda face:float(face[2]*face[3]))
    reference=recognizer.feature(recognizer.alignCrop(ref,rf))
    matched=[]
    for path in frames:
        frame,found=faces(path)
        if frame is None:continue
        h,w=frame.shape[:2]
        for face in found:
            try:
                feature=recognizer.feature(recognizer.alignCrop(frame,face))
                score=float(recognizer.match(reference,feature,cv2.FaceRecognizerSF_FR_COSINE))
            except cv2.error:continue
            if score<LOCAL_FACE_COSINE_THRESHOLD:continue
            x,y,fw,fh=[int(v) for v in face[:4]]
            crop=frame[max(0,y):min(h,y+fh),max(0,x):min(w,x+fw)]
            if crop.size==0:continue
            sharp=float(cv2.Laplacian(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),cv2.CV_64F).var())
            matched.append((score,sharp,fw*fh,path,(x,y,fw,fh)))
    if not matched:raise VisualQualityError('现场封面未找到与林园参考照匹配的人脸')
    score,sharp,area,path,box=max(matched,key=lambda r:(r[0],r[1],r[2]))
    return path,box,{'engine':'opencv_yunet_sface_cpu','cosine_score':round(score,4),
                     'threshold':LOCAL_FACE_COSINE_THRESHOLD,'face_box':list(box),
                     'matched_faces':len(matched),'sharpness':round(sharp,2)}


def make_cover(src, seg_start, seg_end, title, speaker, out_path,
               video_filter="", preferred_time=None, reference_path=None):
    """封面:抽帧 → 人脸检测裁切 → 16:9 → 底部渐变 → 标题大字。

    竖屏视频也输出 16:9 横屏封面(2026-08-23 修复):B站封面信息流是横屏显示,
    竖屏画面居中贴到 1280x720,两侧用放大模糊的原帧做背景。
    竖屏排版自适应(2026-08-21 修复):之前用横屏硬编码参数(64px字号×17字/行),
    720px 宽的竖屏画布装不下 1007px 文字 → 标题溢出、人脸被挤。
    小帧人脸检测:360x640 低清源 haar 检不出脸 → 提前放大再检测。
    抽帧位置:取段落偏前位置,避开字幕最密集的说话中段。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
    tmp = out_path.with_suffix(".frame.png")
    mid = seg_start + (seg_end - seg_start) / 2
    # 人物闸门给出的 preferred_time 已经与参考照核验为本人；围绕该时间取三帧。
    # 没有核验时间时才退回原来的段内多帧策略。
    frames = []
    lo=max(0,seg_start);hi=max(lo,seg_end-.1)
    center=min(hi,max(lo,preferred_time)) if preferred_time is not None else mid
    offsets=(-.6,0,.6) if preferred_time is not None else tuple(
        p*(seg_end-seg_start) for p in (-.3,-.2,-.1,0,.1,.2,.3))
    sample_times=sorted({min(hi,max(lo,center+d)) for d in offsets})
    for idx, t in enumerate(sample_times):
        fp = tmp.with_suffix(f".{idx}.png")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.1f}",
               "-i", str(src)]
        if video_filter:
            cmd += ["-vf", video_filter]
        cmd += ["-frames:v", "1", str(fp)]
        subprocess.run(cmd,
                       check=True, capture_output=True)
        if fp.exists():
            frames.append(fp)

    if not frames:
        raise VisualQualityError("封面无法抽帧")
    remaining = detect_corner_logos_in_images(frames)
    if remaining:
        raise VisualQualityError(f"封面清理后仍检出外部角标：{remaining}")

    best_frame,best_face,identity_proof=select_verified_cover_face(frames,reference_path)
    Path(str(out_path)+'.identity.json').write_text(
        json.dumps(identity_proof,ensure_ascii=False,indent=2))

    img = Image.open(best_frame).convert("RGB")
    w, h = img.size

    # 以人脸为中心裁切,保持目标比例
    vertical = h > w
    portrait_foreground = None
    if vertical:
        # Keep the complete vertical source frame in a separate right-hand
        # panel; a large headline must not cover the speaker's face.
        portrait_foreground = ImageOps.contain(img, (288, 560), Image.Resampling.LANCZOS)
        bg = ImageOps.fit(img, (1280, 720), Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(30))
        img = Image.blend(bg, Image.new("RGB", bg.size, (10, 15, 20)), .65)
        W, H = 1280, 720
    else:
        # 横屏:16:9,以人脸为中心
        tw = min(w, int(h * 16 / 9))
        if best_face is not None:
            fx, fy, fw, fh = best_face
            cx = fx + fw // 2
            x0 = max(0, min(cx - tw // 2, w - tw))
        else:
            x0 = (w - tw) // 2
        img = img.crop((x0, 0, x0 + tw, h)).resize((1280, 720), Image.LANCZOS)
        W, H = 1280, 720

    # 清理临时帧
    for fp in frames:
        fp.unlink(missing_ok=True)
    tmp.unlink(missing_ok=True)

    # 底部 45% 压暗(黑渐变),字才看得清
    overlay = Image.new("L", (W, H), 0)
    od = ImageDraw.Draw(overlay)
    for y in range(H):
        if y > H * 0.55:
            od.line([(0, y), (W, y)], fill=int(200 * (y - H * 0.55) / (H * 0.45)))
    img.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), overlay)
    if portrait_foreground is not None:
        img.paste(portrait_foreground, (952, (720-portrait_foreground.height)//2))

    font_path = None
    for cand in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
                 "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]:
        if Path(cand).exists():
            font_path = cand
            break
    idx = _sc_face_index(font_path) if font_path and font_path.endswith(".ttc") else 0
    # 字号按画布宽自适应（用户 2026-08-26 反馈封面文字再大、标签+0.5倍）
    from presentation import cover_headline, cover_proof
    if not font_path:
        raise VisualQualityError("横版封面缺少中文字体，拒绝输出不可读小字/方框字")
    headline_lines = cover_headline(title, speaker)
    title_size = 100 if vertical else 104
    tag_size = 30 if W < 1000 else 54
    f_title = ImageFont.truetype(font_path, title_size, index=idx) if font_path else ImageFont.load_default()
    f_tag = ImageFont.truetype(font_path, tag_size, index=idx) if font_path else ImageFont.load_default()

    d = ImageDraw.Draw(img)
    # 主讲人标签(左上角黄底黑字),尺寸自适应
    tag_w = int(len(speaker) * tag_size * 1.15) + 28
    d.rounded_rectangle([36, 32, 36 + tag_w, 32 + int(tag_size * 1.7)], 8, fill=(255, 196, 0))
    d.text((50, 40), speaker, font=f_tag, fill=(20, 20, 20))
    # 标题:行宽按画布自适应,行高按字号
    if W < 1000:
        chars_per_line = max(10, int(W * 0.92 / title_size))
        max_lines = 3
        line_h = int(title_size * 1.25)
        margin_bottom = 48
    else:
        chars_per_line = max(13, int(W * 0.90 / title_size))
        # 26~36 字标题在 1280 画布上需要三行；旧代码会静默丢掉末尾。
        max_lines = 3
        line_h = int(title_size * 1.2)
        margin_bottom = 56
    # 标题分行：用 jieba 分词按词边界断，避免「公司」被硬切成「公」+「司」
    # （2026-08-27 实拍封面断句问题）。jieba 失败则退回均匀字符切分。
    lines = headline_lines
    y = H - margin_bottom - line_h * len(lines)
    if vertical:
        y = 238
    boxes = []
    for ln in lines:
        # 白字黑边(描边厚度自适应)
        stroke = 3 if W < 1000 else 2
        d.text((40, y), ln, font=f_title, fill=(255, 255, 255),
               stroke_width=stroke, stroke_fill=(0, 0, 0))
        boxes.append(d.textbbox((40,y),ln,font=f_title,stroke_width=stroke))
        y += line_h
    img.save(out_path, quality=92)
    cover_proof(img, out_path, lines, title_size, boxes, style="photo")
    print(f"[封面] {out_path.name} {W}x{H} 「{title[:20]}」")


def _has_persistent_editorial_overlay(cov, stable=0.50, min_rows=8):
    """检测上半屏持续存在的大标题/信息卡。

    小角标通常只占几行，可交给 ``detect_corner_logos`` + delogo；连续覆盖
    8% 以上画高的文字则属于版式本身，强行涂抹会留下大块脏画面，应改走
    音频卡重建。只检查 0%~55%，避免与下三分之一字幕判定重复。
    """
    run = 0
    for ratio in cov[:55]:
        run = run + 1 if ratio >= stable else 0
        if run >= min_rows:
            return True
    return False


def has_existing_subtitles(src, strict=False, frames=8):
    """检测视频是否已有硬字幕或持续编辑包装。

    2026-08-21 修复:旧版亮度阈值法把「画面偏亮」误判成「有字幕」
    (白色衣服/亮背景即可触发),导致无字幕视频跳过烧录,成片裸奔。

    新版检测字幕的结构特征(同时满足才算字幕帧):
    1. 底部存在横向窄条带(高度 3%~15% 屏高)
    2. 条带内白色(高亮)像素 ≥ 20%(文字覆盖)
    3. 条带上下边界与背景有明显对比(不是整片亮背景)
    """
    # 优先使用已有的多帧 OCR 行覆盖结果。旧算法要求字幕带内亮像素达到 20%，
    # 对「蓝底白字 + 黑描边」这类常见二次加工字幕过于苛刻：文字实际只占
    # 条带约 5%~12%，因此会误判为无字幕并再次烧录。OCR 只关心文字框，且
    # 以多帧持续出现为条件，可排除偶发 PPT/图表文字。
    try:
        cov = ocr_row_coverage(src, frames=frames,strict=strict)
        # 上半屏持续的大标题/信息卡同样会与我们的包装叠加。它不能按小角标
        # delogo，否则会留下大片模糊区域；统一标脏，交给音频卡重建。
        if _has_persistent_editorial_overlay(cov):
            return True
        # 字幕通常位于画面 55%~93% 高度；连续至少 2% 屏高、在至少一半
        # 抽样帧出现，视为已有硬字幕/下三分之一包装。
        run = 0
        for ratio in cov[55:94]:
            run = run + 1 if ratio >= 0.50 else 0
            if run >= 2:
                return True
    except Exception as e:
        if strict:
            raise VisualQualityError('字幕检测未完成，不能放行原画') from e
        print(f"[字幕检测] OCR 检测失败，回退像素检测: {e}", file=sys.stderr)

    try:
        import cv2
        import numpy as np
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            return False
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return False
        subtitle_hits = 0
        checked = 0
        for pct in (0.15, 0.30, 0.50, 0.70, 0.85):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * pct))
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            checked += 1
            h, w = frame.shape[:2]
            # 只看底部 28%(字幕安全区)
            bottom = frame[int(h * 0.72):, :]
            gray = cv2.cvtColor(bottom, cv2.COLOR_BGR2GRAY)
            # 按行统计亮像素(>200)比例,找「文字条带」
            bright = (gray > 185).astype(np.uint8)
            row_ratio = bright.mean(axis=1)  # 每行的亮像素占比
            # 滑窗找连续条带:高度 3%~15% 屏高,且带内亮像素 ≥ 20%,
            # 但条带外(其上 10% 高度内)亮像素 < 8%(排除整片亮背景)
            bh = bottom.shape[0]
            found = False
            for band_h in range(max(2, int(h * 0.03)), int(h * 0.15)):
                if band_h > bh:
                    break
                for y0 in range(0, bh - band_h, max(1, band_h // 2)):
                    band = row_ratio[y0:y0 + band_h]
                    # 条带上方必须有足够行且明显更暗(与背景对比),
                    # 纯亮背景(如白墙)会被排除;条带贴底时用带内列分布区分:
                    # 真字幕是「中间亮两侧暗」,整行亮是背景
                    above = row_ratio[max(0, y0 - int(h*0.08)):max(1, y0)]
                    if band.mean() < 0.20 or len(above) < 2 or above.mean() >= 0.08:
                        continue
                    # 带内列分布:字幕文字不会横贯整行,两端留白
                    col_bright = bright[y0:y0 + band_h, :].mean(axis=0)
                    if col_bright[:int(w*0.12)].mean() < 0.10 and col_bright[-int(w*0.12):].mean() < 0.10:
                        found = True
                        break
                if found:
                    break
            if found:
                subtitle_hits += 1
        cap.release()
        # 至少 5 帧里 2 帧有明确文字条带（OCR 不可用时的保守回退）
        return checked >= 2 and subtitle_hits >= 2
    except Exception:
        return False


def _json_default(o):
    """meta.json 落盘兜底：numpy 标量 / Path 等非原生类型统一转可序列化形式。"""
    try:
        import numpy as np
        if isinstance(o, np.generic):
            return o.item()
    except ImportError:
        pass
    from pathlib import Path as _P
    if isinstance(o, _P):
        return str(o)
    return str(o)


_OVERLAY_CACHE = {}
_OCR_ENGINE = None
_OCR_COV_CACHE = {}


def _ocr():
    """RapidOCR（PaddleOCR 的 ONNX 版）。只做文字检测不做识别 —— 实测同一帧
    检测+识别 28.2s，只检测 3.0s，快 9 倍且检出框更多（44 vs 40）。"""
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR(intra_op_num_threads=2,inter_op_num_threads=1)
    return _OCR_ENGINE


def ocr_row_coverage(src, frames=6, max_w=640, strict=False):
    """抽帧 OCR，统计每 1% 行位置被文字框覆盖的「帧比例」(长度 100 的列表)。

    2026-09-01 换掉原来的 Canny 边缘密度方案：边缘密度会把人物轮廓、K线图、
    装饰线条都当成文字，还漏检半透明台标；实测 5 条有标准答案的素材，
    OCR 全部命中（含之前漏掉的「红星资本局」「金融界 JRJ.com」台标）。
    用「帧比例」而不是单帧结果，是为了区分常驻贴片和一闪而过的内容。
    """
    path=Path(src)
    stamp=(path.stat().st_mtime_ns,path.stat().st_size) if path.exists() else None
    key = (str(path),stamp,frames,max_w,strict)
    if key in _OCR_COV_CACHE:
        return _OCR_COV_CACHE[key]
    cov = [0.0] * 100
    try:
        import cv2
        import numpy as np
        engine = _ocr()
        cap = cv2.VideoCapture(str(src))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        hit = np.zeros(100)
        got = 0
        for i in range(frames):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / max(1, frames)))
            ok, f = cap.read()
            if not ok:
                continue
            H, W = f.shape[:2]
            if W > max_w:
                f = cv2.resize(f, (max_w, int(H * max_w / W)))
                H, W = f.shape[:2]
            res, _ = engine(f, use_det=True, use_rec=False, use_cls=False)
            got += 1
            rows = np.zeros(100, bool)
            for box in (res or []):
                ys = [pt[1] for pt in box]
                a = max(0, min(99, int(min(ys) / H * 100)))
                b = max(0, min(100, int(max(ys) / H * 100) + 1))
                rows[a:b] = True
            hit += rows
        cap.release()
        if strict and got!=frames:
            raise VisualQualityError(f'OCR实际抽帧不足：{got}/{frames}')
        if got:
            cov = (hit / got).tolist()
    except Exception as e:
        if strict:
            raise VisualQualityError('OCR未完成，不能证明原画字幕已清理') from e
        print(f"[OCR] 行覆盖统计失败: {e}", file=sys.stderr)
    _OCR_COV_CACHE[key] = cov
    return cov


def detect_overlay_bands(src, k=1.8, margin=0.05, frames=12):
    """检测视频上下边缘的「贴片区」（台标/标题条/硬字幕），返回 (顶部比例, 底部比例)。

    做法：抽帧算 Canny 边缘的行剖面，用「中位数 × k」作自适应阈值，
    在顶部 40% / 底部 25% 窗口内找最内侧的高边缘行。

    2026-09-01 用 5 条真实视频标定（含 1 条无水印的干净片做负样本）：
      k=1.8 → 漏检 0.17 / 过检 0.11 / 干净片误报 0；k≤1.6 会把干净片误判成有水印。
    检出后各外扩 margin，宁可多裁一点也别留残缺水印。
    """
    key = str(src)
    if key in _OVERLAY_CACHE:
        return _OVERLAY_CACHE[key]
    res = (0.0, 0.0)
    try:
        import cv2
        import numpy as np
        cap = cv2.VideoCapture(str(src))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        E = []
        for i in range(frames):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / max(1, frames)))
            ok, f = cap.read()
            if ok:
                E.append(cv2.Canny(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), 80, 200).astype(np.float32) / 255)
        cap.release()
        if E:
            H = E[0].shape[0]
            row = np.mean(E, axis=0).mean(axis=1)
            w = max(3, H // 80)
            row = np.convolve(row, np.ones(w) / w, mode="same")
            thr = float(np.median(row)) * k
            hi = np.where(row >= thr)[0]
            t_idx = [i for i in hi if i <= H * 0.40]
            b_idx = [i for i in hi if i >= H * 0.75]
            top = (max(t_idx) + 1) / H if t_idx else 0.0
            bot = (H - min(b_idx)) / H if b_idx else 0.0
            top = top + margin if top >= 0.03 else 0.0
            bot = bot + margin if bot >= 0.03 else 0.0
            # 必须转成 Python float：numpy 标量参与比较会产出 np.bool_，
            # 写进 meta.json 时 json.dumps 直接 TypeError
            #（2026-09-02 事故：7 次出片全挂在 "Object of type bool is not JSON serializable"）
            res = (float(min(top, 0.45)), float(min(bot, 0.30)))
    except Exception as e:
        print(f"[裁切] 贴片检测失败，退回不裁: {e}", file=sys.stderr)
    _OVERLAY_CACHE[key] = res
    return res


def detect_corner_logos_in_images(frame_paths, stable_ratio=0.5, max_area=0.02):
    """对已抽出的帧做 OCR 角标复检，供清理后的封面质量闸门使用。"""
    try:
        import cv2
        engine = _ocr()
        boxes = []
        got = 0
        for fp in frame_paths:
            f = cv2.imread(str(fp))
            if f is None:
                continue
            H, W = f.shape[:2]
            got += 1
            res, _ = engine(f, use_det=True, use_rec=False, use_cls=False)
            for b in (res or []):
                xs = [pt[0] for pt in b]
                ys = [pt[1] for pt in b]
                boxes.append((min(xs) / W, min(ys) / H,
                              max(xs) / W, max(ys) / H))
        if not got:
            raise VisualQualityError("OCR 没有读到任何封面帧")
        clusters = []
        for b in boxes:
            hit = next((c for c in clusters
                        if all(abs(b[k] - c["r"][k]) < 0.03 for k in range(4))), None)
            if hit:
                hit["n"] += 1
            else:
                clusters.append({"r": b, "n": 1})
        out = []
        for c in clusters:
            if c["n"] < max(2, int(got * stable_ratio)):
                continue
            x0, y0, x1, y1 = c["r"]
            if (x1 - x0) * (y1 - y0) > max_area:
                continue
            in_corner = (x1 < 0.35 or x0 > 0.65) and (y1 < 0.30 or y0 > 0.70)
            if in_corner:
                out.append((x0, y0, x1, y1))
        return out
    except VisualQualityError:
        raise
    except Exception as e:
        raise VisualQualityError(f"封面 OCR 角标复检失败：{e}") from e


def detect_corner_logos(src, frames=10, stable_ratio=0.5, max_area=0.02,
                        strict=False):
    """检测常驻角落台标/水印，返回归一化框列表 [(x0,y0,x1,y1), ...]。

    背景（2026-09-03 用户发现 BV1QRt96GETb 右上角残留「投资大佬说 bilibili」）：
    此前的裁切只处理上下横向条带，完全没覆盖角落 logo。而 B站会给**所有**上传
    视频自动打「UP名 + bilibili」右上角水印，只要素材来自 B站就一定带别人的名字，
    这不是个例而是通例。

    判定条件（三者同时满足才算角标）：
      1. 跨帧位置固定（±3%）—— 排除会动的画面内容
      2. 面积 < 2% —— 排除大标题条
      3. 落在四角区域 —— 排除画面中部的字幕
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        if strict:
            raise VisualQualityError("缺少 OpenCV/Numpy，无法执行角标检测")
        return []
    try:
        engine = _ocr()
        cap = cv2.VideoCapture(str(src))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if not (W and H and total):
            cap.release()
            return []
        boxes = []
        got = 0
        for i in range(frames):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / frames))
            ok, f = cap.read()
            if not ok:
                continue
            got += 1
            res, _ = engine(f, use_det=True, use_rec=False, use_cls=False)
            for b in (res or []):
                xs = [pt[0] for pt in b]
                ys = [pt[1] for pt in b]
                boxes.append((min(xs) / W, min(ys) / H, max(xs) / W, max(ys) / H))
        cap.release()
        if not got:
            return []
        # 聚类：位置几乎不变的框
        clusters = []
        for b in boxes:
            hit = None
            for c in clusters:
                if all(abs(b[k] - c["r"][k]) < 0.03 for k in range(4)):
                    hit = c
                    break
            if hit:
                hit["n"] += 1
            else:
                clusters.append({"r": b, "n": 1})
        out = []
        for c in clusters:
            if c["n"] < max(2, int(got * stable_ratio)):
                continue
            x0, y0, x1, y1 = c["r"]
            if (x1 - x0) * (y1 - y0) > max_area:
                continue
            in_corner = (x1 < 0.35 or x0 > 0.65) and (y1 < 0.30 or y0 > 0.70)
            if in_corner:
                out.append((x0, y0, x1, y1))
        return out
    except Exception as e:
        if strict:
            raise VisualQualityError(f"角标检测失败：{e}") from e
        print(f"[角标] 检测失败: {e}", file=sys.stderr)
        return []


def delogo_filter(boxes, W, H, pad=4):
    """把角标框转成 ffmpeg delogo 滤镜串。

    用 delogo 而不是高斯模糊：2026-09-01 试过整块模糊，盲评 3/10（「像连人一起
    打了码」），比不处理更差。delogo 用周围像素插值填补，对半透明水印（B站自动
    水印）效果好；对不透明实心台标会留下轻微痕迹，那类素材应在选片阶段减分淘汰。
    """
    parts = []
    for x0, y0, x1, y1 in boxes:
        x = max(1, int(x0 * W) - pad)
        y = max(1, int(y0 * H) - pad)
        w = min(W - x - 1, int((x1 - x0) * W) + pad * 2)
        h = min(H - y - 1, int((y1 - y0) * H) + pad * 2)
        if w >= 8 and h >= 8:
            parts.append(f"delogo=x={x}:y={y}:w={w}:h={h}")
    return ",".join(parts)


def safe_crop_plan(src, W, H, stable=0.4, clean=0.24, max_cut=0.30, coverage=None):
    """算安全裁切方案 (crop_w, crop_h, crop_x, crop_y)，只为「腾出干净的字幕位」。

    历史教训（2026-09-01 三次迭代）：
      ① 按检测带裁掉所有贴片 → 中部贴片去不掉、还切到人头，失败回滚；
      ② 高斯模糊遮挡 → 盲评 3/10，比不处理更差；
      ③ 固定裁检测到的底部带 → 多行字幕只切掉一行，16 组只过 11 组(69%)。
    现在的做法：用 OCR 逐行统计「文字覆盖帧比例」，**从底部往上裁到干净为止**。

    参数：
      stable  行覆盖率 ≥ 此值视为常驻文字（贴片/硬字幕）
      clean   裁完后底部区域允许的最大覆盖率
      max_cut 总裁切上限（当前生产30%）；裁后仍须保留人脸和通过成片复检。
              超过调用方设定上限不再硬切，转人物卡重建。
    """
    cov = ocr_row_coverage(src) if coverage is None else coverage
    if not any(cov):
        return None
    # 第一步永远是：底部本来就干净吗？干净就别裁。
    # 2026-09-02 实测（ly-0902-50cd97 格隆专访）：底部 15% 覆盖率 0.00 完全干净，
    # 字幕带其实在 70~75%。算法却从底部往上够那条带子，算出要裁 33%，
    # 裁完新底部落在人物区（0.29 零星文字）反而不干净 → 6 次全放弃。
    # 正解：底部干净就直接用，我们的字幕烧在原底部即可，一刀都不用裁。
    # 检查窗口取 70~90%：2026-09-02 实测 5 条真实素材，字幕带集中在 65~87%，
    # 而 90~100% 普遍是 0.00（视频底部有安全边距）。
    # 最初用 85~100% 做窗口，正好落在空白区，把 8 条本该裁的误判成「干净」。
    # A narrow persistent subtitle occupies only 2–4% of image height. Averaging
    # it over 20 rows incorrectly called it clean, especially below row 90.
    # Use the same consecutive-row evidence as the hard-subtitle detector.
    band_run=0; persistent_band=False
    for coverage in cov[55:98]:
        band_run=band_run+1 if coverage>=stable else 0
        if band_run>=2:persistent_band=True;break
    if not persistent_band:
        print('[裁切] 未检出持续底部字幕带，无需裁切')
        return None
    # 从底部往上找「最底下那一块连续文字」，只裁它。
    # 上一版是「35% 内出现任何文字就一路裁到那里」，结果 26 条全部触顶放弃（0 条裁切）。
    limit = int(max_cut * 100)
    i = 99
    while i >= 100 - limit and cov[i] < stable:     # 跳过底部干净区
        i -= 1
    bot = 0
    if i >= 100 - limit:
        gap = 0
        j = i
        while j >= 100 - limit:
            if cov[j] >= stable:
                gap = 0
                bot = 100 - j
            else:
                gap += 1
                if gap >= 3:                        # 连续 3% 干净 → 文字块到头
                    break
            j -= 1
        bot = min(limit, bot + 3)                   # 多裁 3% 余量
    # 顶部：只裁小块（大块说明是标题包装，这种素材本就该在选片淘汰）
    top = 0
    for i in range(0, 20):
        if cov[i] >= stable:
            top = i + 1
    if top > 15:
        print(f"[裁切] 顶部文字 {top}% > 15%（大字标题包装），不裁顶部")
        top = 0
    elif top:
        top = min(15, top + 2)
    if top + bot > max_cut * 100:
        print(f"[裁切] 总裁切量 {top + bot}% > {max_cut:.0%}，放弃裁切（保画面）")
        return None
    if top + bot < 2:
        return None
    # 裁完后底部是否干净（留给我们自己的字幕）
    keep_lo, keep_hi = top, 100 - bot
    tail = cov[max(keep_lo, keep_hi - 15):keep_hi]
    if tail and sum(tail) / len(tail) > clean:
        print(f"[裁切] 裁 {top}%/{bot}% 后底部仍有文字（{sum(tail)/len(tail):.0%}），放弃")
        return None
    top_px = int(H * top / 100) // 2 * 2
    bot_px = int(H * bot / 100) // 2 * 2
    crop_h = (H - top_px - bot_px) // 2 * 2
    crop_w = W // 2 * 2
    if not _face_survives(src, top_px, crop_h):
        print("[裁切] 裁切后检不出人脸，回退不裁")
        return None
    print(f"[裁切] 顶{top}% 底{bot}% → {crop_w}x{crop_h}（保留 {crop_w*crop_h/(W*H):.0%}）")
    return crop_w, crop_h, 0, top_px


def _face_survives(src, top_px, crop_h, samples=6):
    """裁切后还能否检出人脸。切到脸的裁法一律不要。"""
    try:
        import cv2
        cap = cv2.VideoCapture(str(src))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cascade = _cascade(cv2.data.haarcascades +
                           "haarcascade_frontalface_default.xml")
        before = after = 0
        for i in range(samples):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (i + 0.5) / samples))
            ok, f = cap.read()
            if not ok:
                continue
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            if len(cascade.detectMultiScale(g, 1.1, 4, minSize=(28, 28))):
                before += 1
            gc = g[top_px:top_px + crop_h, :]
            if gc.size and len(cascade.detectMultiScale(gc, 1.1, 4, minSize=(28, 28))):
                after += 1
        cap.release()
        if before == 0:
            return True                    # 原片本来就没脸（如图表/资料画面），不拦
        return after >= before * 0.7       # 裁后人脸帧数不能掉太多
    except Exception as e:
        print(f"[裁切] 人脸校验失败({e})，保守起见不裁", file=sys.stderr)
        return False


def has_hard_watermark(src):
    """检测视频是否有难以裁除的水印(画面中间的 logo)。
    检查画面四角和中间是否有固定位置的半透明 logo。
    """
    try:
        import cv2
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            return False
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return False
        cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
        ret, frame = cap.read()
        cap.release()
        if not ret or frame is None:
            return False
        h, w = frame.shape[:2]
        # 检查中间区域是否有固定 logo
        center = frame[int(h*0.4):int(h*0.6), int(w*0.4):int(w*0.6)]
        gray = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
        _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        logo_ratio = cv2.countNonZero(binary) / (center.size / 3)
        return logo_ratio > 0.1  # 10% 以上白色像素 → 可能有中间水印
    except Exception:
        return False


def _chunk_by_time(cues, chunk_sec=360):
    """长视频按时间均分成多段（每段约 chunk_sec 秒）。
    返回 [(start_idx, end_idx), ...] 每段的 cues 索引区间。
    不足 1.5 段就不拆，返回整段。"""
    if not cues:
        return []
    total = cues[-1]["end"] - cues[0]["start"]
    if total <= chunk_sec * 1.5:
        return [(0, len(cues) - 1)]
    chunks = []
    start_idx = 0
    seg_start = cues[0]["start"]
    for i in range(1, len(cues)):
        if cues[i]["end"] - seg_start >= chunk_sec:
            chunks.append((start_idx, i - 1))
            start_idx = i
            seg_start = cues[i]["start"]
    chunks.append((start_idx, len(cues) - 1))
    return chunks


# 园园对同场 57:53 访谈公开拆出的 13 条时长（秒）。专用对标模式按这个
# 长短结构分配 13 个连续内容窗口；窗口额外均摊未入选的谈话时间，让 LLM
# 仍能在完整上下文里挑到语义闭合的观点，而不是机械截固定分钟数。
COMPETITOR_13_DURATION_PROFILE = [541, 160, 116, 197, 68, 100, 46,
                                  244, 136, 69, 220, 83, 604]


def _chunk_by_duration_profile(cues, profile):
    """按目标时长比例把整场讲话切成固定数量的内容窗口。"""
    if not cues or not profile:
        return []
    total = cues[-1]["end"] - cues[0]["start"]
    selected = float(sum(profile))
    gap = max(0.0, total - selected) / len(profile)
    boundaries = []
    elapsed = cues[0]["start"]
    for duration in profile[:-1]:
        elapsed += float(duration) + gap
        boundaries.append(elapsed)
    chunks = []
    start = 0
    for boundary in boundaries:
        end = start
        while end + 1 < len(cues) and cues[end + 1]["end"] <= boundary:
            end += 1
        chunks.append((start, max(start, end)))
        start = min(len(cues) - 1, end + 1)
    chunks.append((start, len(cues) - 1))
    return chunks


def _dedup_chunks_char(chunks, cues, sim_threshold=0.90):
    """字符级去重：逐字/高度相同的段直接去重（保留最早一段）。

    2026-08-29 补盲区：LLM 观点去重对「完全相同的两段」可能漏删（c94dbf 实测
    段1段2 逐字相同却都保留），故先用 difflib 把这种极端重复兜底掉，
    剩下的语义重复再交给 LLM。阈值 0.90 只抓「几乎逐字相同」，不误杀语义相似。
    """
    if len(chunks) <= 1:
        return chunks
    kept = []
    for cand in chunks:
        a, b = cand
        ta = "".join(cues[i]["text"] for i in range(a, b + 1))
        dup = False
        for ka, kb in kept:
            tk = "".join(cues[i]["text"] for i in range(ka, kb + 1))
            if difflib.SequenceMatcher(None, ta, tk).ratio() >= sim_threshold:
                dup = True
                break
        if not dup:
            kept.append(cand)
    if len(kept) < len(chunks):
        print(f"[字符去重] {len(chunks)} 段 → {len(kept)} 段（逐字重复兜底）")
    return kept


def _dedup_chunks_by_llm(chunks, cues, api_key, work):
    """LLM 观点去重：长视频多段里，观点重复的段只保留信息量最丰富的一段。

    2026-08-29：a987c4 拆 7 段但「AI风险/红海/泡沫」反复讲，是语义级重复
    （字符相似度仅 0.04~0.20，difflib 抓不到），必须 LLM 判断观点重复。
    保守降级：LLM 不可用 / 解析异常 / 结果异常 → 全部保留，绝不多删。
    """
    if len(chunks) <= 1 or not api_key:
        return chunks
    cache = work / "chunks_dedup.json"
    if cache.exists():
        try:
            keep0 = json.loads(cache.read_text(encoding="utf-8"))
            kept = [chunks[i] for i in keep0 if 0 <= i < len(chunks)]
            if len(kept) >= 2:
                return kept
        except Exception:
            pass
    segs = []
    for i, (a, b) in enumerate(chunks):
        txt = "".join(cues[j]["text"] for j in range(a, b + 1))
        segs.append(f"[段{i+1}]{txt[:180]}")
    prompt = ("下面是同一个访谈视频按时间切出的 " + str(len(chunks)) + " 段字幕。\n"
              "请判断哪些段讲的「观点重复」（同一个意思/同一个观点反复讲）。\n"
              "规则：观点重复的几段，只保留信息量最丰富的一段，其余删除；观点不重复的段全部保留。\n"
              "只输出 JSON 数组，元素是要【保留】的段编号（从 1 开始），如 [1,2,4,6]。不要输出其他内容。\n\n"
              + "\n".join(segs))
    try:
        out = llm([{"role": "user", "content": prompt}], api_key, temperature=0.0, max_tokens=200)
        nums = parse_llm_json_array(out)
    except Exception as e:
        print(f"[去重] LLM/解析异常，跳过: {e}")
        return chunks
    keep0 = sorted({n - 1 for n in nums if isinstance(n, int) and 1 <= n <= len(chunks)})
    if len(keep0) < 2:
        return chunks  # 结果异常（只剩 0/1 段）→ 保守全保留
    cache.write_text(json.dumps(keep0, ensure_ascii=False), encoding="utf-8")
    print(f"[去重] {len(chunks)} 段 → 保留 {len(keep0)} 段（LLM 观点去重）")
    return [chunks[i] for i in keep0]


def _wrap_audio_card_title(title, chars_per_line, max_lines=3):
    """按词边界排高密度标题，并把 ``100倍``、``30年`` 当不可拆原子。"""
    try:
        import jieba as _jieba
        import logging as _logging
        _jieba.setLogLevel(_logging.ERROR)
        raw_atoms = list(_jieba.cut(title))
    except ImportError:
        raw_atoms = list(title)

    phrase_atoms = []
    i = 0
    while i < len(raw_atoms):
        atom = raw_atoms[i]
        if (atom in {"最", "更", "很"} and i + 1 < len(raw_atoms)
                and raw_atoms[i + 1]
                and raw_atoms[i + 1][0] not in "，。！？；：、,.!?;"):
            phrase_atoms.append(atom + raw_atoms[i + 1])
            i += 2
            continue
        phrase_atoms.append(atom)
        i += 1

    atoms = []
    for atom in phrase_atoms:
        if (atom in {"%", "倍", "万", "亿", "年", "元"} and atoms
                and re.fullmatch(r"\d+(?:\.\d+)?", atoms[-1])):
            atoms[-1] += atom
        elif len(atom) > chars_per_line:
            atoms.extend(atom[i:i + chars_per_line]
                         for i in range(0, len(atom), chars_per_line))
        else:
            atoms.append(atom)

    lines = []
    current = ""
    for atom in atoms:
        if len(current) + len(atom) > chars_per_line and current:
            if atom and atom[0] in "，。！？；：、,.!?;的了着过吧呢吗啊呀":
                current += atom
                continue
            lines.append(current)
            current = ""
        current += atom
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        if len(title) <= chars_per_line * max_lines:
            # 英文长词/空格可能浪费一整行；容量足够时退回定宽，不丢任何字符。
            return [title[i:i + chars_per_line]
                    for i in range(0, len(title), chars_per_line)]
        raise VisualQualityError(
            f"音频卡标题超过 {max_lines} 行容量，拒绝截断：{title}")
    return lines


def _audio_card_display_topic(topic, speaker=""):
    """把投稿标题变成卡片常驻标题，同时保留竞品式信息密度。"""
    text = re.sub(r"\s+", " ", topic or "投资观点精选").strip()
    if speaker:
        escaped = re.escape(speaker)
        text = re.sub(
            rf'^[“\"]?股神[”\"]?\s*{escaped}\s*[：:]\s*', "", text)
        text = re.sub(rf'^{escaped}\s*[：:]\s*', "", text)
    if len(text) > AUDIO_CARD_TOPIC_MAX_CHARS:
        text = text[:AUDIO_CARD_TOPIC_MAX_CHARS - 1] + "…"
    return text


def _audio_card_topic_tag(text, speaker):
    if '片仔癀' in (text or ''):
        return f"{speaker}｜片仔癀"
    if '药企' in (text or ''):
        return f"{speaker}｜医药"
    for keyword in ("医药", "中药", "消费", "白酒", "AI", "科技", "机器人",
                    "老龄化", "价值投资", "股市"):
        if keyword.lower() in (text or "").lower():
            return f"{speaker}｜{keyword}"
    return f"{speaker}｜投资观点"


def _audio_card_emphasis_colors(text):
    """为强数字、冲突词和赛道词生成园园式红黄蓝标题层级。"""
    yellow = (255, 210, 24)
    blue = (35, 86, 170)
    red = (226, 45, 39)
    colors = [yellow] * len(text)
    for pattern, color in (
        (r"AI|科技|医药|中药|片仔癀|药企|消费|白酒|老龄化|行业|国家", blue),
        (r"\d+(?:\.\d+)?%?|[零一二三四五六七八九十百千万亿]+倍|"
         r"万倍|百倍|亿|首富|发财|赚钱|机会|风险|上涨|下跌|涨|跌|牛市|三四席|不敢", red),
    ):
        for match in re.finditer(pattern, text, re.I):
            for i in range(match.start(), match.end()):
                colors[i] = color
    return colors


def _draw_emphasis_line(draw, xy, text, font, stroke_width, stroke_fill,
                        centered=True):
    """逐字绘制强调色，保持整行居中或从指定 x 起绘制。"""
    x, y = xy
    widths = [draw.textlength(ch, font=font) for ch in text]
    if centered:
        x -= sum(widths) / 2
    for ch, width, color in zip(
            text, widths, _audio_card_emphasis_colors(text)):
        draw.text((int(x), y), ch, font=font, fill=color,
                  stroke_width=stroke_width, stroke_fill=stroke_fill)
        x += width


def extract_audio_card_portrait(reference_image, out_path):
    """从身份门禁的权威参考照提取目标人物肖像。

    多人访谈里“最大脸”经常是主持人，不能再从原片盲取。参考照已经由
    ``verify_source_identity`` 下载并作为 VLM 的身份基准；若参考照不可用，
    宁可回退通用人物图标，也不把其他嘉宾放进主讲人卡片。
    """
    try:
        import cv2
        frame = cv2.imread(str(reference_image))
        if frame is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _cascade(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        ).detectMultiScale(gray, 1.1, 4, minSize=(48, 48))
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
        H, W = frame.shape[:2]
        # 参考图本身可能是采访海报；1.45 倍足以保留头肩，同时避免把两侧
        # 栏目文字裁进圆形肖像。过宽的 1.75 倍在真实样片中带入了“人说”残字。
        side = int(max(w, h) * 1.45)
        side = min(side, W, H)
        if side < max(w, h):
            return None
        cx = x + w // 2
        cy = y + h // 2 + int(h * 0.22)
        x0 = max(0, min(W - side, cx - side // 2))
        y0 = max(0, min(H - side, cy - side // 2))
        crop = frame[y0:y0 + side, x0:x0 + side]
        if crop.size == 0:
            return None
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(out_path), crop):
            return None
        return out_path
    except Exception as e:
        print(f"[音频卡] 肖像提取失败，使用通用人物图标: {e}", file=sys.stderr)
        return None


def make_audio_card(out_path, speaker, topic, width=None, height=None,
                    portrait_path=None, require_portrait=False, cover_style=None, live_video=False):
    """生成不携带第三方字幕/角标的品牌音频卡。

    只在原画无法安全清理时使用。背景、文案和品牌均由本流水线生成；原素材
    仅贡献已通过人物核验的讲话音频，避免把模糊/涂抹后的脏画面硬塞进成片。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    width = int(width or AUDIO_CARD_WIDTH)
    height = int(height or AUDIO_CARD_HEIGHT)
    vertical = height > width

    # 对标账号的高播放音频卡不是深色科技模板，而是「浅灰底 + 人物视觉 +
    # 红黄标题 + 黄字字幕」。这里复刻信息层级和观看习惯，不复制它的插画、
    # 照片、署名或其他受保护资产。浅暖灰比纯白更耐看，也能承托金色品牌色。
    image = Image.new("RGB", (width, height), (232, 231, 226))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        blend = y / max(1, height - 1)
        color = (int(238 - 15 * blend), int(237 - 14 * blend),
                 int(232 - 12 * blend))
        draw.line((0, y, width, y), fill=color)

    font_path = next((x for x in (
        os.environ.get("AUDIO_CARD_FONT_FILE"),
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ) if x and Path(x).exists()), None)
    if not font_path:
        raise VisualQualityError("音频卡缺少中文字体，拒绝生成方框字成片")
    index = _sc_face_index(font_path) if font_path and font_path.endswith(".ttc") else 0
    unit = (min(width / 720, height / 1280) if vertical
            else min(width / 1280, height / 720))
    headline_size = max(34, int((52 if vertical else 58) * unit))
    topic_size = max(25, int((43 if vertical else 40) * unit))
    small_size = max(18, int((22 if vertical else 24) * unit))
    headline_font = (ImageFont.truetype(font_path, headline_size, index=index)
                     if font_path else ImageFont.load_default())
    topic_font = (ImageFont.truetype(font_path, topic_size, index=index)
                  if font_path else ImageFont.load_default())
    small_font = (ImageFont.truetype(font_path, small_size, index=index)
                  if font_path else ImageFont.load_default())

    display_topic = _audio_card_display_topic(topic, speaker)

    if vertical:
        # v3 固定坐标：标签 96~142，主标题 165~325，人物 360~830，
        # 字幕留白 874~1040，来源说明 1080~1160。
        tag = _audio_card_topic_tag(display_topic, speaker)
        tag_bbox = draw.textbbox((0, 0), tag, font=small_font)
        tag_w = tag_bbox[2] - tag_bbox[0] + int(28 * unit)
        draw.rounded_rectangle(
            (48, 96, 48 + tag_w, 142), radius=max(8, int(10 * unit)),
            fill=(35, 86, 170))
        draw.text((48 + int(14 * unit), 101), tag, font=small_font,
                  fill=(255, 255, 255))

        from presentation import cover_headline
        lines = cover_headline(topic, speaker)
        topic_size = max(34, int(58 * unit))
        topic_font = ImageFont.truetype(font_path, topic_size, index=index)
        line_h = int(topic_size * 1.15)
        title_y = 165
        title_boxes = []
        for i, line in enumerate(lines):
            draw.text((48, title_y + i * line_h), line, font=topic_font,
                      fill=(24, 44, 66))
            title_boxes.append(draw.textbbox((48,title_y+i*line_h),line,
                               font=topic_font))
        if len(lines)>2 or any(b[0]<38 or b[2]>682 or b[1]<150 or b[3]>325
                               for b in title_boxes):
            raise VisualQualityError('视频顶部短标题超出两行安全区域')
        Path(str(out_path)+'.title-proof.json').write_text(json.dumps({
            'version':1,'title':topic,'headline_lines':lines,'font_px':topic_size,
            'text_boxes':title_boxes,'max_lines':2,'bottom_limit':325,
            'matches_cover_headline':True},ensure_ascii=False,indent=2))

        x0, y0, x1, y1 = 44, 360, 676, 830
        draw.rounded_rectangle((x0, y0, x1, y1), radius=18,
                               fill=(218, 215, 206),
                               outline=(193, 151, 64), width=3)
        portrait_used = False
        if portrait_path and Path(portrait_path).is_file():
            try:
                portrait = Image.open(portrait_path).convert("RGB")
                portrait = ImageOps.fit(
                    portrait, (x1 - x0, y1 - y0),
                    method=Image.Resampling.LANCZOS)
                mask = Image.new("L", portrait.size, 0)
                ImageDraw.Draw(mask).rounded_rectangle(
                    (0, 0, portrait.size[0] - 1, portrait.size[1] - 1),
                    radius=16, fill=255)
                image.paste(portrait, (x0, y0), mask)
                portrait_used = True
            except Exception as e:
                print(f"[音频卡] 肖像嵌入失败，使用通用人物图标: {e}",
                      file=sys.stderr)
        if not portrait_used:
            if require_portrait:
                raise VisualQualityError("人物资料卡缺少可用真人参考图，禁止占位图出片")
            cx, head_y, head_r = width // 2, 505, 78
            draw.ellipse((cx - head_r, head_y - head_r, cx + head_r,
                          head_y + head_r), fill=(35, 48, 64))
            draw.rounded_rectangle((170, 590, 550, 790), radius=100,
                                   fill=(35, 48, 64))

        draw.rounded_rectangle((38, 874, 682, 1040), radius=18,
                               fill=(249, 249, 247),
                               outline=(214, 210, 200), width=2)
        draw.text((48, 1080), ("公开发言原声｜原始访谈画面" if live_video else
                             "公开发言原声｜人物资料图，非现场画面"),
                  font=small_font, fill=(89, 94, 99))
        draw.text((48, 1120), AUDIO_CARD_DISCLAIMER, font=small_font,
                  fill=(105, 105, 105))
    else:
        # 16:9 封面：结论在左、人物在右且占 35%~45%，缩略图仍可辨认。
        panel = (46, 74, width - 46, height - 74)
        draw.rounded_rectangle(panel, radius=max(18, int(28 * unit)),
                               fill=(245, 242, 232),
                               outline=(193, 151, 64),
                               width=max(2, int(4 * unit)))
        icon_x, icon_y = int(width * 0.86), int(height * 0.46)
        icon_r = int(height * 0.19)
        portrait_used = False
        if portrait_path and Path(portrait_path).is_file():
            try:
                portrait = Image.open(portrait_path).convert("RGB")
                portrait = ImageOps.fit(
                    portrait, (icon_r * 2, icon_r * 2),
                    method=Image.Resampling.LANCZOS)
                mask = Image.new("L", portrait.size, 0)
                ImageDraw.Draw(mask).ellipse(
                    (0, 0, portrait.size[0] - 1, portrait.size[1] - 1),
                    fill=255)
                image.paste(portrait, (icon_x - icon_r, icon_y - icon_r), mask)
                draw.ellipse((icon_x - icon_r, icon_y - icon_r,
                              icon_x + icon_r, icon_y + icon_r),
                             outline=(193, 151, 64),
                             width=max(2, int(4 * unit)))
                portrait_used = True
            except Exception as e:
                print(f"[封面] 肖像嵌入失败，使用通用人物图标: {e}",
                      file=sys.stderr)
        if not portrait_used:
            if require_portrait:
                raise VisualQualityError(
                    "v4 封面未能嵌入已核验人物图，拒绝使用深色占位图")
            draw.ellipse((icon_x - icon_r, icon_y - icon_r,
                          icon_x + icon_r, icon_y + icon_r), fill=(35, 48, 64))
        tag = _audio_card_topic_tag(display_topic, speaker)
        draw.rounded_rectangle((72, 112, 330, 170), radius=12,
                               fill=(35, 86, 170))
        draw.text((90, 120), tag, font=small_font, fill=(255, 255, 255))
        # 与竖版统一为 14 字 × 3 行；展示标题已限制在 42 字内。
        from presentation import cover_headline
        lines = cover_headline(topic, speaker)
        cover_font_px = 100
        cover_font = ImageFont.truetype(font_path, cover_font_px, index=index)
        cover_boxes = []
        for i, line in enumerate(lines):
            _draw_emphasis_line(
                draw, (60, 238 + i * 128),
                line, cover_font, stroke_width=3,
                stroke_fill=(45, 28, 20), centered=False)
            cover_boxes.append(draw.textbbox((60,238+i*128),line,font=cover_font,stroke_width=3))
        draw.text((72, 560), "公开访谈原声 · 个人观点非投资建议", font=small_font,
                  fill=(89, 94, 99))
        from presentation import select_cover_style, dark_cover
        selected_style = select_cover_style(False, topic,
            cover_style or os.environ.get("COVER_STYLE", "auto"))
        if selected_style == "dark":
            image, lines, cover_font_px, cover_boxes = dark_cover(
                portrait_path, topic, speaker, font_path, index)

    brand = Image.open(brand_watermark_path()).convert("RGBA")
    brand_w = int(width * (0.18 if vertical
                           else BRAND_WATERMARK_WIDTH_RATIO))
    brand_h = max(1, int(brand.height * brand_w / brand.width))
    brand = brand.resize((brand_w, brand_h), Image.LANCZOS)
    alpha = brand.getchannel("A").point(
        lambda value: int(value * BRAND_WATERMARK_OPACITY))
    brand.putalpha(alpha)
    margin = int(width * (0.035 if vertical
                          else BRAND_WATERMARK_MARGIN_RATIO))
    image.paste(brand, (width - brand_w - margin, margin), brand)
    out_path = Path(out_path)
    if out_path.suffix.lower() in {".jpg", ".jpeg"}:
        image.save(out_path, quality=93)
    else:
        image.save(out_path)
    if not vertical:
        from presentation import cover_proof
        cover_proof(image, out_path, lines, cover_font_px, cover_boxes, style=selected_style)
    return out_path


def make_review_assets(final, out, suffix, duration_sec):
    """发布前固定生成 30 秒预览和 6 帧接触表，供人机双重抽检。"""
    preview = out / f"preview_30s{suffix}.mp4"
    sheet = out / f"contact_sheet_6{suffix}.jpg"
    preview_sec = max(1.0, min(30.0, float(duration_sec)))
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(final),
        "-t", f"{preview_sec:.3f}", "-c", "copy", str(preview),
    ], check=True)
    fps = 6.0 / max(1.0, float(duration_sec))
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(final),
        "-vf", f"fps={fps:.8f},scale=240:-2,tile=3x2:padding=6:margin=6",
        "-frames:v", "1", "-q:v", "2", str(sheet),
    ], check=True)
    if preview.stat().st_size < 1024 or sheet.stat().st_size < 1024:
        raise VisualQualityError("30秒预览或6帧接触表生成不完整")
    return preview.name, sheet.name


def verify_final_live_identity(final, work, speaker, api_key, suffix="", target_times=None):
    """Verify the actual moving window, excluding the template/reference portrait."""
    directory=Path(work)/f"final_identity{suffix}"
    directory.mkdir(exist_ok=True)
    frames=[]
    duration=float(probe(final,"format=duration"))
    times=target_times if target_times is not None else [duration*i/7 for i in range(1,7)]
    if len(times)!=6 or any(not 0<=t<duration for t in times):
        raise VisualQualityError('成片人物复检的实际时间点无效')
    for i,t in enumerate(times,1):
        path=directory/f"frame_{i}.jpg"
        subprocess.run(['ffmpeg','-y','-loglevel','error','-ss',str(t),
            '-i',str(final),'-vf','crop=632:470:44:360','-frames:v','1',str(path)],
            check=True,timeout=45)
        frames.append(path)
    reference=_download_speaker_reference(speaker,Path(work))
    verdict=_local_identity_verdict(reference,frames,speaker)
    same={i for i in verdict.get('same_person_frames',[]) if type(i) is int and 1<=i<=6}
    proof={**verdict,'version':1,'speaker':speaker,'sample_count':6,
           'sampling_scope':'verified_guest_turns' if target_times is not None else 'whole_timeline',
           'sample_times':times}
    (directory/'verification.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    if len(same)<5 or float(verdict.get('confidence') or 0)<.75:
        raise VisualQualityError(f'成片动态窗口目标人物不足5/6帧：{len(same)}/6')
    if verdict.get('watermark_texts'):
        raise VisualQualityError('成片动态窗口仍有外部台标/账号：'+str(verdict['watermark_texts']))
    return proof


def argument_record_for_render(cues,picks,speaker,api_key,work,suffix):
    """Record the user's disabled model review; retain source integrity gates."""
    # Omitting words within an argument still needs its existing meaning check.
    # The ordinary production path is one continuous source range.
    if len(picks)!=1:
        return review_complete_argument(cues,picks,speaker,api_key,work,suffix)
    editorial.range_seconds(cues,picks[0])
    text=''.join(c['text'] for c in cues[picks[0]['start']:picks[0]['end']+1])
    error=editorial.transcript_integrity_error(text)
    if error:
        raise VisualQualityError(error)
    proof=dict(version=editorial.VERSION,status='skipped',
        review_protocol='model-review-disabled-v1',
        model_review_policy_version=editorial.MODEL_REVIEW_POLICY_VERSION,
        reason='user_disabled_model_completeness_review',
        transcript_sha256=editorial.text_digest(text))
    (work/f'editorial_review{suffix}.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2))
    print('[完整观点] 按用户设置跳过模型审核；保留连续源区间、时长及字幕完整性检查')
    return proof


def _produce_one(src, work, out, cues, speaker, occasion, api_key,
                 existing_subtitles, W, H, suffix, pick_cache_suffix="", target_sec=None,
                 allow_empty=False, visual_report=None, source_report=None,
                 prefer_live_video=False, existing_titles=None,
                 preselected_picks=None):
    """出一段视频。suffix='' 或 '_2' 等。target_sec 控制时长（短金句 180 / 中视频 420）。
    返回 meta dict；allow_empty=True 且本段没有够格金句时返回 None（不出片）。"""
    picks = (preselected_picks if preselected_picks is not None else
             pick_highlights(cues, speaker, api_key, work, pick_cache_suffix,
                             target_sec, allow_empty=allow_empty))
    if not picks:
        print(f"[段{suffix or '1'}] 无够格金句，跳过不出片")
        return None
    argument_review = argument_record_for_render(cues,picks,speaker,api_key,work,suffix)
    sel = sorted({i for p in picks for i in range(p["start"], p["end"] + 1)})
    total_sel = sum(cues[i]["end"] - cues[i]["start"] for i in sel)
    print(f"[段{suffix or '1'}] 选 {len(sel)} 条字幕,约 {int(total_sel)//60}:{int(total_sel)%60:02d}")

    # 标题与封面共用原文证据，但封面使用独立的完整短句。
    cw = copywrite(
        cues, sel, speaker, occasion, api_key, work, pick_cache_suffix,
        existing_titles=existing_titles,
        require_quote=(pick_cache_suffix != "_full"),
        reviewed_title=picks[0].get('editorial_title'),
        reviewed_cover=picks[0].get('editorial_cover'))

    # 质检与成片严格复用同一份清理计划，避免门禁验证 A、实际编码却执行 B。
    source_report = source_report or {}
    strategy = source_report.get("clean_strategy", "direct")
    native_plan=None;interview_plan=None;participant_reference=None
    if strategy=='audio_card' and prefer_live_video and len(picks)==1:
        native_plan=selected_native_clean_plan(src,work/f'native{suffix}',W,H,
            cues[picks[0]['start']]['start'],cues[picks[0]['end']]['end'])
        if native_plan:
            from source_selection import sentence_units,QUESTION
            units=sentence_units(cues[picks[0]['start']:picks[0]['end']+1])
            others=(source_report.get('visual_identity') or {}).get('different_person_frames') or []
            reference=work/f'identity_{others[0]}.jpg' if len(others)==1 else None
            if units and QUESTION.search(units[0]['text']) and reference and reference.is_file():
                interview_plan=native_plan;participant_reference=reference
                print('[访谈适配] 分别核验嘉宾与提问者；插图按原始时间保留，不用静态头像覆盖',flush=True)
            else:
                source_report={**source_report,**native_plan}
                strategy=source_report['clean_strategy']
    clean_vf = source_report.get("clean_video_filter") or f"crop={W//2*2}:{H//2*2}:0:0"
    clean_resolution = source_report.get("clean_output_resolution") or {}
    crop_w = int(clean_resolution.get("width") or (W // 2 * 2))
    crop_h = int(clean_resolution.get("height") or (H // 2 * 2))
    border_proof = None
    if strategy != 'audio_card':
        # OCR cleaning does not detect encoded black bars (#682). Measure the
        # actual cleaned source before deciding caption and watermark geometry.
        from source_geometry import refine_native_crop
        clean_vf, crop_w, crop_h, border_proof = refine_native_crop(
            source_report.get('geometry_source') or src, clean_vf, crop_w, crop_h, work, minimum=MIN_SHORT_EDGE)
        (work / f'border_geometry{suffix}.json').write_text(
            json.dumps(border_proof, ensure_ascii=False, indent=2))
    _logos = source_report.get("detected_corner_logos") or []
    print(f"[干净画面] strategy={strategy} output={crop_w}x{crop_h}")
    # A failed source-cleaning gate must never be bypassed by putting the same
    # dirty source into a guessed fixed crop. Native clean sources retain aspect;
    # genuinely unusable pictures become an explicitly labelled portrait/audio card.
    live_crop = None
    # 原画因字幕/包装无法作为整屏成片时，优先尝试“真人动态窗口”而不是
    # 直接退化成静态音频卡。候选窗口必须再次实渲染并确认无持续字幕/角标；
    # 最终成片还会继续经过 QR、黑边和角标复检，因此不降低 V11 安全门槛。
    if strategy == "audio_card" and prefer_live_video:
        candidate_crop = (reviewed_source_live_crop(source_report,W,H) or
                          audio_card_live_crop(W, H, src, cues[picks[0]["start"]]["start"]))
        if candidate_crop:
            try:
                preview_pick=picks[0]
                preview_start=cues[preview_pick['start']]['start']
                preview_duration=cues[preview_pick['end']]['end']-preview_start
                live_preview = _render_clean_preview(
                    src, work, candidate_crop, preview_duration,source_start=preview_start)
                # V11 原来把任何持续文字都视为不可用，导致大量官方访谈即使
                # 文字只落在动态窗口边缘也直接退成静态卡。这里不再用整帧
                # has_existing_subtitles 一票否决，而是以最终窗口的角标/二维码/
                # 黑边复检为硬门槛。人物身份门禁仍保持不变。
                remaining = detect_corner_logos(live_preview, frames=6, strict=True)
                if remaining:
                    print("[自动版式] 真人窗口仍有稳定来源角标，保留人物资料卡兜底")
                else:
                    live_crop = candidate_crop
                    print("[自动版式] 当前选段的窗口角标抽检通过；仍须逐帧取景及成片人物复检")
            except Exception as exc:
                print(f"[自动版式] 真人动态窗口预检失败，安全回退资料卡：{exc}")
    use_live_video = bool(live_crop)
    if strategy == "audio_card" and prefer_live_video and not use_live_video:
        print("[自动版式] 原画无法安全清理，使用已核验人物资料卡兜底")

    brand = brand_watermark_path()
    audio_card = None
    audio_card_portrait = None
    if strategy == "audio_card":
        # 视频顶部与封面共用完整短标题，投稿标题保留完整表述。
        first_pick = picks[0]
        audio_card_portrait = extract_audio_card_portrait(
            _download_speaker_reference(speaker,work),
            work / f"audio_card_portrait{suffix}.png")
        audio_card = make_audio_card(
            work / f"audio_card{suffix}.png", speaker, cw["cover_title"],
            portrait_path=audio_card_portrait, require_portrait=True, live_video=use_live_video)
    from presentation import layout_for, VERSION as PRESENTATION_VERSION
    layout = layout_for(crop_w, crop_h, strategy == "audio_card")
    en_map = {}
    parts = []
    interview_tracking=None
    framing_proofs=[]
    for n, p in enumerate(picks, 1):
        idx = list(range(p["start"], p["end"] + 1))
        s0, s1 = cues[idx[0]]["start"], cues[idx[-1]]["end"]
        entries = [{"start_sec": cues[i]["start"] - s0,
                    "end_sec": cues[i]["end"] - s0,
                    "zh": cues[i]["text"], "en": en_map.get(i, "")} for i in idx]
        ass = work / f"seg{suffix}{n}.ass"
        entries = semantic_caption_entries(entries, api_key, layout, work / f"semantic{suffix}-{n}.json",
                                           reviewed_groups=p.get('editorial_subtitles'))
        make_ass(entries, ass, crop_w, crop_h,
                 card_style=(strategy == "audio_card"))
        seg = work / f"seg{suffix}{n}.mp4"
        vertical = H > W
        seg_dur = s1 - s0
        # 片头片尾淡入淡出 0.4s：修「开头结束断帧」的视觉突兀（2026-08-27）
        fades=[]
        if n==1:fades.append('fade=t=in:st=0:d=0.4')
        if n==len(picks):fades.append(f'fade=t=out:st={max(0,seg_dur-.4):.2f}:d=0.4')
        fade=','.join(fades) or 'null'
        if strategy == "audio_card":
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-framerate", "30", "-i", str(audio_card),
                "-ss", str(s0), "-t", str(seg_dur), "-i", str(src),
            ]
            if use_live_video:
                # 只允许横屏源进入动态窗口；按窗口宽高比实裁并精确缩放，
                # 不使用 pad，因而不会产生右侧黑块。
                # Fixed crops become empty when the source switches cameras.
                # Track identity in the selected interval before composing the card.
                from live_tracking import render_tracked
                tracked=work/f'tracked{suffix}{n}.mp4'
                tracking=render_tracked(src,s0,seg_dur,tracked,
                    _download_speaker_reference(speaker,work),_local_face_models(),
                    LOCAL_FACE_COSINE_THRESHOLD,
                    exclusions=source_report.get('detected_corner_logos') or (),
                    context_crop=(interview_plan['native_context_proof']['crop_xywh'] if interview_plan else None),
                    participant_reference=participant_reference,
                    reference_samples=[work/f'identity_{i}.jpg' for i in
                        (source_report.get('visual_identity') or {}).get('same_person_frames',[])
                        if (work/f'identity_{i}.jpg').is_file()] if interview_plan else ())
                framing_proofs.append(tracking['framing'])
                if interview_plan:
                    from interview_graphics import clean_interview_graphics
                    tracking=clean_interview_graphics(tracked,tracking,entries,
                        source_report['source_sha256'],work/f'graphics{suffix}{n}')
                    interview_tracking={**tracking,'source_sha256':source_report['source_sha256']}
                cmd += ["-i",str(tracked)]
                live = (
                    "[2:v]setpts=PTS-STARTPTS[live];"
                    "[0:v][live]overlay=44:360[card];"
                    f"[card]ass={ass},{fade}[outv]"
                )
                cmd += ["-filter_complex", live,
                        "-map", "[outv]", "-map", "1:a:0"]
            else:
                vf = f"ass={ass},{fade}"
                cmd += ["-filter_complex", f"[0:v]{vf}[outv]",
                        "-map", "[outv]", "-map", "1:a:0"]
        else:
            vf = f"setpts=PTS-STARTPTS,{clean_vf},setsar=1,ass={ass},{fade}"
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error", "-ss", str(s0),
                "-t", str(seg_dur), "-i", str(src),
                "-loop", "1", "-framerate", "30", "-i", str(brand),
                "-filter_complex", brand_overlay_filter(vf, crop_w, crop_h),
                "-map", "[outv]", "-map", "0:a:0",
            ]
        cmd += [
            "-af", "asetpts=PTS-STARTPTS,highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:v", "libx264", "-preset", ("veryfast" if use_live_video else "slow"),
            "-crf", ("20" if use_live_video else "18"),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-r", "30",
            "-t", str(seg_dur), "-shortest", str(seg),
        ]
        subprocess.run(cmd, check=True, timeout=max(180, int(seg_dur * 8)))
        parts.append(seg)

    lst = work / f"concat{suffix}.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    final = out / f"final{suffix}.mp4"
    final_name = final.name          # 真实文件名，跳段后编号会与列表下标脱节，必须回传
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", str(lst), "-c", "copy", str(final)],
                   check=True, timeout=120)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(final)],
        capture_output=True, text=True).stdout.strip() or 0)
    if dur < editorial.MIN_SECONDS:
        raise VisualQualityError('实际成片不足120秒，隔离后更换完整观点')
    final_w, final_h = ensure_min_short_edge(final, label="裁切后成片")
    # audio_card 的整张画布、标题、字幕和水印均由本流程生成，人物图也来自
    # 权威参考照；再用通用角标 OCR 扫它只会把模板自有标题误报为第三方角标。
    # 真实原画策略仍必须逐帧复检。
    from presentation import verify_render
    live_checks = verify_render(final, layout)
    if border_proof is not None:
        live_checks['source_border_geometry'] = border_proof
    if use_live_video:
        elapsed=0; junction_times=[]
        for pick in picks[:-1]:
            elapsed+=cues[pick['end']]['end']-cues[pick['start']]['start']
            junction_times.extend((max(0,elapsed-.25),elapsed+.25))
        live_checks.update(verify_live_region_after_render(final,extra_times=junction_times,
            actor_times=interview_tracking['target_sample_times'] if interview_tracking else ()))
        live_checks['junction_frames_checked']=len(junction_times)
        live_checks['final_live_identity']=verify_final_live_identity(
            final,work,speaker,api_key,suffix,
            target_times=interview_tracking['target_sample_times'] if interview_tracking else None)
        external_logos = []
    else:
        external_logos = detect_external_logos_after_render(
            final, strategy, final_w, final_h)
    if external_logos:
        raise VisualQualityError(f"成片清理后仍检出外部角标：{external_logos}")
    transcript_text = "".join(cues[i]["text"] for i in sel)
    fingerprints = build_content_fingerprints(final, transcript_text)
    preview_name, contact_sheet_name = make_review_assets(
        final, out, suffix, dur)

    cover_name = "cover_16x9.jpg"
    cover = out / (f"cover{suffix}.jpg" if suffix else cover_name)
    cover_fallback_reason=None
    try:
        p0 = picks[0]
        from presentation import select_cover_style
        selected_cover_style = select_cover_style(strategy != "audio_card", cw["title"],
                                                  os.environ.get("COVER_STYLE", "auto"))
        if selected_cover_style != "photo":
            if audio_card_portrait is None:
                audio_card_portrait = extract_audio_card_portrait(
                    work / "speaker_reference.jpg", work / f"cover_portrait{suffix}.png")
            make_audio_card(cover, speaker, cw["cover_title"],
                            width=1280, height=720,
                            portrait_path=audio_card_portrait,
                            require_portrait=True, cover_style=selected_cover_style)
            cover_person_image_source = "authority_reference"
        else:
            try:
                make_cover(src, cues[p0["start"]]["start"], cues[p0["end"]]["end"],
                           cw["cover_title"], speaker, cover, video_filter=clean_vf,
                           preferred_time=(visual_report or {}).get("best_cover_time"),
                           reference_path=work / "speaker_reference.jpg")
                cover_person_image_source = "verified_source_frame"
            except VisualQualityError as exc:
                if os.environ.get('COVER_STYLE','auto')=='photo':raise
                cover_fallback_reason=str(exc)
                selected_cover_style=select_cover_style(False,cw['title'])
                if audio_card_portrait is None:
                    audio_card_portrait=extract_audio_card_portrait(
                        work / 'speaker_reference.jpg',work / f'cover_portrait{suffix}.png')
                make_audio_card(cover,speaker,cw['cover_title'],width=1280,height=720,
                                portrait_path=audio_card_portrait,require_portrait=True,
                                cover_style=selected_cover_style)
                cover_person_image_source='authority_reference'
                print('[封面] 现场帧不可用，已改用核验人物卡：'+str(exc))
    except Exception as e:
        raise VisualQualityError(f"封面生成/人物/角标复检失败：{e}") from e
    subtitle_files = []
    for n in range(1, len(picks) + 1):
        name = f"subtitles{suffix}-{n}.ass"
        (out / name).write_bytes((work / f"seg{suffix}{n}.ass").read_bytes())
        subtitle_files.append(name)
    rendered_subtitle_text = editorial.subtitle_files_text(out, subtitle_files)
    subtitle_integrity_error = editorial.transcript_integrity_error(rendered_subtitle_text)
    if subtitle_integrity_error:
        raise VisualQualityError(subtitle_integrity_error)
    return {
        "editorial_review": argument_review,
        "editorial_policy_version": editorial.VERSION,
        "subtitle_files": subtitle_files,
        "subtitle_text_sha256": editorial.text_digest(rendered_subtitle_text),
        "final": final_name,
        "title": cw["title"], "desc": cw["desc"], "tags": cw["tags"],
        "cover_title":cw['cover_title'], "cover_copy":cw['cover_copy'],
        "video_title":cw['cover_title'] if strategy=='audio_card' else None,
        "video_title_proof":(json.loads(Path(str(audio_card)+'.title-proof.json').read_text())
                             if audio_card else None),
        "title_candidates":cw['title_candidates'],"packaging_version":cw['packaging_version'],
        "cover_fallback_reason":cover_fallback_reason,
        "cover": cover.name if cover else None,
        "preview_30s": preview_name,
        "contact_sheet_6": contact_sheet_name,
        "review_assets_verified": True,
        "title_quality_verified": cw.get("title_quality_verified") is True,
        "visual_standard_version": VISUAL_STANDARD_VERSION,
        "cover_standard_version": COVER_STANDARD_VERSION,
        "cover_person_image_verified": True,
        "cover_person_image_source": cover_person_image_source,
        "presentation_version": PRESENTATION_VERSION,
        "layout_proof": layout,
        "cover_proof": json.loads(Path(str(cover)+".proof.json").read_text()),
        "subtitle_word_boundaries_verified": True,
        "subtitle_semantic_groups_verified": True,
        "subtitle_readability_version": layout["readability_version"],
        "subtitle_edit_proofs": [f"_tmp/semantic{suffix}-{n}.editing.json" for n in range(1,len(picks)+1)],
        **live_checks,
        "duration_sec": round(dur, 1),
        "resolution": {"width": final_w, "height": final_h,
                       "short_edge": min(final_w, final_h)},
        "fingerprints": fingerprints,
        "watermark_removed": strategy != "direct",
        "watermark_verified": True,
        "clean_strategy": strategy,
        "native_context_proof":(native_plan or {}).get('native_context_proof'),
        "interview_context":interview_tracking,
        "framing_proofs":framing_proofs,
        "audio_card_template": (AUDIO_CARD_TEMPLATE
                                if strategy == "audio_card" else None),
        "render_mode": ("live_video_card" if use_live_video
                        and strategy == "audio_card" else strategy),
        "brand_watermark_applied": True,
        "brand_watermark": {
            "name": "园来滚雪球", "position": "top-right",
            "width_ratio": BRAND_WATERMARK_WIDTH_RATIO,
            "opacity": BRAND_WATERMARK_OPACITY,
        },
        "segments": [{"start": cues[p["start"]]["start"],
                      "end": cues[p["end"]]["end"], "reason": p["reason"]}
                     for p in picks],
    }


class PartDeadlineExceeded(BaseException):
    """A wall-clock deadline must escape lower-level broad Exception retries."""


def produce_part_with_budget(*args, budget_sec=None, **kwargs):
    import signal
    seconds=float(budget_sec if budget_sec is not None else
                  os.environ.get('PART_RENDER_BUDGET_SEC','1800'))
    if not hasattr(signal,'setitimer'):
        return _produce_one(*args,**kwargs)
    def expired(signum,frame):
        raise PartDeadlineExceeded()
    previous=signal.signal(signal.SIGALRM,expired)
    timer=signal.setitimer(signal.ITIMER_REAL,seconds)
    try:
        return _produce_one(*args,**kwargs)
    except PartDeadlineExceeded:
        raise PartProductionUnavailable(f'单片超过{seconds:g}秒生产预算，保留原始证据并隔离后继续后续片段') from None
    finally:
        signal.setitimer(signal.ITIMER_REAL,*timer)
        signal.signal(signal.SIGALRM,previous)


def selected_part_numbers(spec, work_items):
    if not re.fullmatch(r'[1-9][0-9]*(,[1-9][0-9]*)*',spec):
        raise ValueError('选段补产编号必须是正整数')
    numbers={int(n) for n in spec.split(',')}
    if not numbers.issubset(set(range(1,len(work_items)+1))):
        raise ValueError('补产编号不在本次已验证选段中')
    if any(not work_items[n-1][2] for n in numbers):
        raise ValueError('按条补产需要已完成并验证的选段，不能指定未选片的母片分块')
    return numbers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only-reviewed-parts', default='',
                    help='Recovery only: comma-separated reviewed source part numbers; default all')
    ap.add_argument('--only-selected-parts', default='',
                    help='Render only these validated automatic selections; all gates still run')
    ap.add_argument("--source", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--speaker", default="林园")
    ap.add_argument("--occasion", default="")
    ap.add_argument("--source-platform", default="", help="来源平台(bilibili/weibo/tencent 等)")
    ap.add_argument("--dry-run", action="store_true", help="只挑金句,不出片")
    ap.add_argument("--source-check-only", action="store_true",
                    help="只执行下载后素材质检，不进入 ASR/切片")
    ap.add_argument("--source-report", default="",
                    help="素材质检报告路径；前置检查和正式出片共用")
    ap.add_argument("--target-parts", type=int, default=0,
                    help="专用对标模式：固定产出多少条观点切片")
    ap.add_argument("--include-full", action="store_true",
                    help="在观点切片后追加一条完整访谈")
    ap.add_argument("--prefer-live-video", action="store_true",
                    help="默认使用裁净后的真人动态画面卡；静态肖像仅作失败回退")
    ap.add_argument("--split-highlights", action="store_true",
                    help="把每个完整金句独立渲染/隔离，单条失败不淘汰同源其他金句")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_file():
        sys.exit(f"找不到源:{src}")
    api_key = load_key()
    if TEXT_BACKEND == 'siliconflow' and not api_key:
        sys.exit("缺 SILICONFLOW_API_KEY(放 .env 或环境变量)")

    out = BASE / "deliver" / args.slug
    work = out / "_tmp"
    work.mkdir(parents=True, exist_ok=True)

    report_path = Path(args.source_report) if args.source_report else \
        work / "source_quality.json"
    if args.source_check_only:
        report = run_source_quality_gate(
            src, work, args.speaker, api_key, report_path)
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get("passed") is True else 2

    try:
        if args.source_report:
            source_report = load_source_quality_report(src, report_path)
        else:
            source_report = run_source_quality_gate(
                src, work, args.speaker, api_key, report_path)
            if source_report.get("passed") is not True:
                raise VisualQualityError(
                    source_report.get("reason") or "素材质检未通过")
    except VisualQualityError as e:
        print(json.dumps({"stage": "source-quality", "reason": str(e)},
                         ensure_ascii=False), file=sys.stderr)
        return 2

    resolution = source_report["resolution"]
    W, H = int(resolution["width"]), int(resolution["height"])
    clean_resolution = source_report.get("clean_output_resolution") or resolution
    output_w = int(clean_resolution.get("width") or W)
    output_h = int(clean_resolution.get("height") or H)
    existing_subtitles = False
    visual_report = source_report["visual_identity"]

    cues = transcribe(src, work, api_key)
    # 元数据描述实际成片画布；音频卡统一为 16:9，不能沿用原素材方向。
    vertical = output_h > output_w

    # 平台推断
    platform = args.source_platform or ""
    if not platform:
        src_str = str(src).lower()
        if "bilibili" in src_str or "bv" in src_str:
            platform = "bilibili"
        elif "weibo" in src_str or "weibocdn" in src_str:
            platform = "weibo"
        elif "tencent" in src_str or "qq.com" in src_str:
            platform = "tencent"
        elif "xueqiu" in src_str:
            platform = "xueqiu"
        elif "douyin" in src_str:
            platform = "douyin"
        elif "haokan" in src_str:
            platform = "haokan"
        elif "netease" in src_str or "163.com" in src_str:
            platform = "netease"
        else:
            platform = "unknown"

    # A reviewed edit list fixes continuous topic boundaries only. Every range
    # still goes through independent argument, caption, visual and media gates.
    from curated_editorial import source_ranges
    curated=source_ranges(cues,source_report.get('source_sha256'))
    if curated is not None:
        chunks=[(a,b) for a,b,_ in curated]
        print(f'[编辑选段] 已核对来源的连续完整观点：{len(chunks)}条；逐条重新质检')
    elif args.target_parts:
        if args.target_parts != 13:
            sys.exit("当前对标模式只支持已核验的 13 条结构")
        chunks = _chunk_by_duration_profile(cues, COMPETITOR_13_DURATION_PROFILE)
    else:
        chunks = _chunk_by_time(cues)
        # 去重：先字符级（逐字重复兜底），再 LLM 观点去重（语义重复）
        chunks = _dedup_chunks_char(chunks, cues)
        chunks = _dedup_chunks_by_llm(chunks, cues, api_key, work)
        # Give the editor context across mechanical chunk boundaries. Final
        # outputs are still individual continuous arguments and content-deduped.
        expanded=[]
        for a,b in chunks:
            left,right=a,b
            while left>0 and cues[a]['start']-cues[left-1]['start']<=60:
                left-=1
            while right+1<len(cues) and cues[right+1]['end']-cues[b]['end']<=60:
                right+=1
            expanded.append((left,right))
        chunks=expanded
    if args.dry_run:
        # dry-run 只看金句，不切分
        p = pick_highlights(cues, args.speaker, api_key, work)
        for pp in p:
            print(f"\n── {pp['reason']} ──")
            for i in range(pp["start"], pp["end"] + 1):
                print(f"  {cues[i]['text']}")
        return 0

    # 一个时间块可能含多个独立金句。逐条生产时先保留模型选出的完整范围，
    # 后续每个范围单独进入画面/字幕门禁和隔离目录；坏片不会拖死同块好片。
    selection_failures = []
    work_items = curated if curated is not None else [(a, b, None) for a, b in chunks]
    source_picks=[]
    if (curated is None and args.split_highlights and not args.target_parts
            and not args.only_selected_parts and os.environ.get('SOURCE_EDITORIAL_FIRST')=='true'):
        from source_selection import select
        source_picks=select(cues,whole_source=True)
        if source_picks:
            work_items=[(p['start'],p['end'],[{**p,'start':0,'end':p['end']-p['start']}]) for p in source_picks]
            (work/'source_question_answers.json').write_text(json.dumps(source_picks,ensure_ascii=False,indent=2))
            print(f'[原文选段] 直接保留{len(source_picks)}组原始提问及连续回答，跳过模型选段',flush=True)
    if curated is None and args.split_highlights and not args.target_parts and not source_picks:
        work_items = []
        for block_no, (a, b) in enumerate(chunks, 1):
            block_cues = cues[a:b + 1]
            try:
                picks = pick_highlights(
                    block_cues, args.speaker, api_key, work,
                    suffix=f"_block_{block_no}", target_sec=TARGET_SEC,
                    allow_empty=True)
            except LocalTextUnavailable as exc:
                selection_failures.append(dict(stage='editorial-selection' if isinstance(exc,SelectionIncomplete)
                    else 'editorial-service',part=block_no,
                    reason=str(exc),error_type=type(exc).__name__,retryable=True))
                print(f'[完整观点] 运行故障，保留第{block_no}块原始转写：{exc}',flush=True)
                continue
            if not picks:
                selection_failures.append(dict(stage='editorial-selection', part=block_no,
                    reason='本轮选段没有返回通过120秒和连续上下文检查的候选',
                    error_type='NoEligibleArgument', retryable=False))
            for pick in picks:
                lo, hi = int(pick["start"]), int(pick["end"])
                if 0 <= lo <= hi < len(block_cues):
                    whole = {**pick, "start": 0, "end": hi - lo}
                    work_items.append((a + lo, a + hi, [whole]))

    # 默认所有日常片遵循同一个2～3分钟完整观点目标；不按字幕数量强行拉长某条。
    mid_idx = None

    from batch_delivery import quarantine_part, write_json
    retry_parts=None
    if args.only_reviewed_parts:
        if curated is None or not re.fullmatch(r'[1-9][0-9]*(,[1-9][0-9]*)*',args.only_reviewed_parts):
            raise ValueError('按条补产仅接受已核对选段的正整数编号')
        retry_parts={int(n) for n in args.only_reviewed_parts.split(',')}
        if not retry_parts.issubset(set(range(1,len(work_items)+1))):
            raise ValueError('补产编号不在已核对选段中')
    if args.only_selected_parts:
        if args.only_reviewed_parts:
            raise ValueError('不能同时指定人工核对与自动选段编号')
        retry_parts=selected_part_numbers(args.only_selected_parts,work_items)
    metas, rejected = [], list(selection_failures)
    publication_state={}
    if os.environ.get('PUBLICATION_STATE_PATH'):
        publication_state=json.loads(Path(os.environ['PUBLICATION_STATE_PATH']).read_text())
        if not isinstance(publication_state.get('published'),dict):
            raise ValueError('发布历史不可用，不能把未核对的母片当成未使用')
    # Persist complete metadata as soon as a part passes all checks. A later bad
    # part cannot erase earlier successes; diagnostics stay outside delivery.
    def checkpoint():
        rows = [{"slug": args.slug, "source": str(src), "speaker": args.speaker,
                 "occasion": args.occasion, **m,
                 "quality_gate_version": QUALITY_GATE_VERSION,
                 "source_sha256": source_report.get('source_sha256'),
                 "source_platform": platform,
                 "watermark_cropped": bool(m.get("watermark_removed")),
                 "watermark_verified": bool(m.get("watermark_verified")),
                 "visual_identity": visual_report,
                 "subtitles_burned": True, "has_existing_subtitles": False,
                 "raw_has_existing_subtitles": bool(source_report.get("raw_has_existing_subtitles")),
                 "clean_filter_verified": bool(source_report.get("clean_filter_verified")),
                 "vertical": m["resolution"]["height"] > m["resolution"]["width"],
                 "asr_model": ASR_BACKEND,
                 "llm": LOCAL_LLM_MODEL if TEXT_BACKEND=='local' else MODELS[0],
                 "text_backend": TEXT_BACKEND,
                 "generated_at": datetime.now().isoformat(timespec="seconds")}
                for m in metas]
        if rows:
            write_json(out / "meta.json", rows[0] if len(rows) == 1 else rows)
        live = sum(m.get("render_mode") != "audio_card" for m in metas)
        write_json(out / "batch_report.json", {
            "slug": args.slug, "accepted": len(metas), "rejected": rejected,
            "accepted_finals": [m["final"] for m in metas],
            "live_video": live, "audio_card": len(metas) - live,
            "live_ratio": live / len(metas) if metas else 0,
            "retryable": not metas and any(r.get('retryable') for r in rejected),
            "selection_completed": not any(r.get('retryable') for r in selection_failures),
            "quality_gate_version": QUALITY_GATE_VERSION})

    for ci, (a, b, preselected_picks) in enumerate(work_items):
        if retry_parts is not None and ci+1 not in retry_parts:
            continue
        suffix = "" if len(work_items) == 1 else f"_{ci + 1}"
        seg_cues = cues[a:b + 1]
        target_sec = (COMPETITOR_13_DURATION_PROFILE[ci]
                      if args.target_parts else
                      (TARGET_SEC_MID if ci == mid_idx else TARGET_SEC))
        if ci == mid_idx:
            print(f"[中视频] 第{ci+1}段做成 {TARGET_SEC_MID//60} 分钟话题片")
        try:
            if preselected_picks and publication_state:
                segments=[dict(start=seg_cues[p['start']]['start'],end=seg_cues[p['end']]['end'])
                          for p in preselected_picks]
                reuse_error=editorial.source_reuse_error(
                    dict(source_sha256=source_report.get('source_sha256'),segments=segments),
                    os.environ.get('SOURCE_ORIGIN_URL',''),publication_state)
                if reuse_error:
                    raise VisualQualityError(reuse_error)
            # Context expansion can propose overlapping candidates; retain the
            # first accepted complete argument, never duplicate the same speech.
            if preselected_picks:
                ranges=[(seg_cues[p['start']]['start'],seg_cues[p['end']]['end']) for p in preselected_picks]
                if any(min(hi,s['end'])-max(lo,s['start'])>.3
                       for lo,hi in ranges for old in metas for s in old.get('segments',[])):
                    raise VisualQualityError('与本批已通过的完整观点时间段重叠，跳过重复选段')
            m = produce_part_with_budget(src, work, out, seg_cues, args.speaker, args.occasion,
                             api_key, existing_subtitles, W, H, suffix,
                             pick_cache_suffix=suffix, target_sec=target_sec,
                             allow_empty=(len(chunks) > 1 and not args.target_parts),
                             visual_report=visual_report,
                             source_report=source_report,
                             prefer_live_video=args.prefer_live_video,
                             existing_titles=[x["title"] for x in metas],
                             preselected_picks=preselected_picks)
        except (VisualQualityError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
            # LLM/part time budgets are local to one candidate.  They must not
            # abort the remaining candidates from the same mother video.
            service_timeout = isinstance(e, RuntimeError) and (
                '时间预算' in str(e) or 'LLM 调用' in str(e))
            failure = {"stage": "part-quality", "reason": str(e), "part": ci + 1,
                       "error_type": type(e).__name__,
                       "retryable":isinstance(e,EditorialReviewUnavailable) or service_timeout}
            print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
            quarantine_part(out, suffix)
            rejected.append(failure)
            checkpoint()
            continue
        if m is not None:
            m["part"] = ci + 1
            metas.append(m)
        checkpoint()

    if args.target_parts and len(metas) != args.target_parts:
        print(f"❌ 对标批次要求 {args.target_parts} 条，实际仅 {len(metas)} 条",
              file=sys.stderr)
        return 2

    if args.include_full:
        full_suffix = "_full"
        try:
            # Full interviews already have a defined range. A legacy bare-list
            # cache is invalidated by the selector and used to send the entire
            # transcript back through short-clip selection (incident #617).
            full_picks=[dict(start=0,end=len(cues)-1,score=10,reason='完整访谈原声')]
            editorial.range_seconds(cues,full_picks[0])
            full_meta = _produce_one(
                src, work, out, cues, args.speaker, args.occasion, api_key,
                existing_subtitles, W, H, "_14", pick_cache_suffix=full_suffix,
                target_sec=max(1, int(cues[-1]["end"] - cues[0]["start"])),
                allow_empty=False, visual_report=visual_report,
                source_report=source_report,
                prefer_live_video=args.prefer_live_video,
                existing_titles=[x["title"] for x in metas],
                preselected_picks=full_picks)
            if full_meta is None:
                raise VisualQualityError('完整版未生成')
        except (VisualQualityError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
            failure={"stage":"part-quality","reason":str(e),"part":"full",
                     "error_type":type(e).__name__,
                     "retryable":isinstance(e,EditorialReviewUnavailable)}
            print(json.dumps(failure,ensure_ascii=False),file=sys.stderr)
            quarantine_part(out, "_14")
            rejected.append(failure)
            checkpoint()
            if args.target_parts:
                return 2
        else:
            full_meta["content_type"] = "full_interview"
            metas.append(full_meta)

    checkpoint()
    if not metas:
        print("❌ 本素材没有通过全部门禁的成片，换下一个候选", file=sys.stderr)
        return 2 if rejected else 1

    n = len(metas)
    print(f"\n✅ 出片完成: {n} 条")
    for i, m in enumerate(metas):
        print(f"   [{i+1}] {m['title']}  ({m['duration_sec']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
