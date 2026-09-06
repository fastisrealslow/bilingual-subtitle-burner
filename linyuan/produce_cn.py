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
PRESENTATION_RULES_VERSION = 2
# 中文生产只允许本地 CPU 识别；不自动回退识别 API 或 large-v3。
# legacy Whisper 函数保留供历史代码读取，不进入本生产入口。
WHISPER = os.environ.get("WHISPER_MODEL") or "/home/node/.cache/whisper/large-v3"
ASR_PIPELINE_VERSION = 3
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

# 第一财经 2026-08-22《投资人说》官方节目封面。这里只作为机器人物比对的
# 参考图，不会进入成片或对外分发；可用环境变量替换为自有参考图 URL。
LINYUAN_REFERENCE_URL = os.environ.get("LINYUAN_REFERENCE_URL") or (
    "https://imgcdn.yicai.com/vms-new/2026/08/"
    "b6e325e8-6616-46ed-902e-2987008296f5.jpg"
)
VISUAL_GATE_VERSION = 2
VISUAL_SAMPLE_COUNT = 6
VISUAL_MIN_MATCHES = 2
VISUAL_MIN_MATCH_RATIO = 0.50
VISUAL_MIN_CONFIDENCE = 0.75
MIN_SHORT_EDGE = 480
SOURCE_MIN_DURATION = 90
SOURCE_MAX_DURATION = 7200
FINGERPRINT_VERSION = 1
QUALITY_GATE_VERSION = 11
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
AUDIO_CARD_TEMPLATE = "live_editorial_v3_competitor_parity"
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

TARGET_SEC = 180          # 成片目标时长（短金句）
MIN_HIGHLIGHT_SCORE = 7   # 金句评分门槛：低于此分不出片（2026-09-02）
                          # 依据：同期 B站实测 <30s 炸裂金句播放中位 10.8 万，
                          # 我们 1~3 分钟平铺内容中位 23。宁缺毋滥。
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
        verdict = _call_identity_vlm(reference, chunk, speaker, api_key)
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


def _call_identity_vlm(reference, frames, speaker, api_key):
    """把权威参考照和源片多帧一起交给 VLM 做目标人物在场核验。"""
    content = [
        {"type": "text", "text": f"参考图：已确认是目标人物【{speaker}】本人。"},
        {"type": "image_url", "image_url": {"url": _image_data_url(reference)}},
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
            f"任务是逐帧判断参考图中的{speaker}本人是否出现在画面任意位置。"
            "一帧可能同时出现主持人、嘉宾或多人：只要目标人物也在场，即归入"
            " same_person_frames，绝不能因为另一个人更大、更居中或正在说话而归入"
            " different_person_frames。只有清楚看到人脸、且能确认目标人物完全不在画面中，"
            "才归入 different_person_frames；遮挡、侧脸过小或看不清则归为 uncertain。"
            "同时记录屏幕叠加的外部账号/平台角标；无文字的彩色图形台标也须记录为[图形台标]，"
            "不要把真实场景里的字画、衣服文字或物品当叠加水印。只返回 JSON object："
            '{"same_person_frames":[1],"different_person_frames":[2],'
            '"uncertain_frames":[3],"best_cover_frame":1,"confidence":0.95,'
            '"watermark_texts":["某账号"],"reason":"简短依据"}'
        ),
    })
    payload = json.dumps({
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 600,
        "temperature": 0.0,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        SF_URL, data=payload,
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode())
            return _parse_json_object(data["choices"][0]["message"]["content"])
        except Exception as e:
            last = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise VisualQualityError(f"人物 VLM 校验不可用：{last}")


def verify_source_identity(src, work, speaker, api_key):
    """在 ASR 前确认整片主角确实是指定人物，并返回可用封面帧时间。"""
    reference = _download_speaker_reference(speaker, work)
    frames, times = _sample_visual_frames(src, work)
    verdict = _call_identity_vlm(reference, frames, speaker, api_key)
    if not identity_verdict_passes(verdict, len(frames)):
        verdict = _retry_identity_vlm_in_chunks(
            reference, frames, speaker, api_key)
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
        "model": VISION_MODEL,
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


def llm(messages, api_key, temperature=0.3, max_tokens=2000, budget_sec=None):
    """调 LLM,限流时自动换模型。硅基流动的限流是分模型的。"""
    cache_dir = BASE / ".llm_cache"
    cache_dir.mkdir(exist_ok=True)
    ckey = hashlib.sha256(json.dumps(
        {"m": messages, "t": temperature, "mt": max_tokens},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cf = cache_dir / f"{ckey}.json"
    if cf.exists():
        try:
            out = json.loads(cf.read_text(encoding="utf-8"))["content"]
            print("[llm-cache] 命中,不发请求")
            return out
        except (ValueError, KeyError, OSError):
            print("[llm-cache] 缓存损坏,重新请求", file=sys.stderr)

    last = None
    deadline = time.monotonic() + budget_sec if budget_sec else None
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


def _asr_cache_identity(src):
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
    if ASR_BACKEND not in ("sensevoice", "funasr"):
        raise ValueError(f"中文生产不支持 ASR_BACKEND={ASR_BACKEND!r}；只允许已验证的 CPU 离线后端")
    src, work = Path(src), Path(work)
    work.mkdir(parents=True, exist_ok=True)
    cache, provenance = work / "cues_raw.json", work / "asr_cache.json"
    identity = _asr_cache_identity(src)
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
                    "chunks_dedup.json", "semantic*.json"):
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
    else:
        cues = _transcribe_funasr(src, work)
    _asr_quality_gate(cues, _audio_duration(src))
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp = provenance.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(identity=identity, cues_sha256=_sha256_file(cache)),
                              ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(provenance)
    return cues


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


def _funasr_tokens_to_cues(tokens, timestamps, offset, chunk_dur):
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
        en = timestamps[i + 1] if i + 1 < len(timestamps) else chunk_dur
        en = min(en, st + MAX_TOKEN_SEC)   # 单 token 封顶，避免跨静音把字幕拖长
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
def pick_highlights(cues, speaker, api_key, work, suffix="", target_sec=None, allow_empty=False):
    """让 LLM 挑金句段落。返回 [(起cue索引, 止cue索引), ...]。
    suffix 用于长视频拆多条时区分各段的缓存（否则第 2 段会命中第 1 段的
    highlights.json，返回超出本段范围的索引 → IndexError）。"""
    cache = work / f"highlights{suffix}.json"
    if cache.exists():
        print("[金句] 命中缓存")
        return json.loads(cache.read_text(encoding="utf-8"))

    target = target_sec or TARGET_SEC
    numbered = "\n".join(
        f"{i}|{int(c['start'])//60}:{int(c['start'])%60:02d}|{c['text']}"
        for i, c in enumerate(cues))
    prompt = f"""下面是{speaker}一段讲话的字幕,格式为「序号|时间|文本」(序号从 0 开始计数)。

请挑出**最有传播力的金句段落**,每段约 {target} 秒。

⚠️ 允许一个都不选（返回空数组 []）。这段素材如果全是开场白/流程性内容/寒暄,就返回 []。
宁可不出片,也不要把没传播力的内容做成视频。

【为什么严格】2026-09-01 实测同期 B站林园内容：
  <30 秒的炸裂金句切片，播放中位 10.8 万；
  我们发的 1~3 分钟平铺内容，播放中位 23。
差距不在剪辑，在选的这段话本身有没有冲击力。

【每段必须打分】给 0~10 分,只有 **≥7 分**的才放进结果:
  9~10 分：有冲突/反常识/大数字，单独拎出来就能当标题
         例「股市里赚到大钱的人都是呆子笨蛋」「我这么有钱的人，怎么会给穷人道歉」
  7~8 分：有具体数字或明确判断，信息量足
         例「8000块做到20亿」「片仔癀股价未来或加两个零」
  ≤6 分：一律不要 —— 包括:
         开场白/致辞/流程语（「大家好」「手机静音」「感谢主办方」「我们开始吧」）
         寒暄客套、自我介绍、对主持人的回应
         没有结论的铺垫、含糊其辞的套话
         语义残缺、识别错乱

【硬性要求】
1. 第一句就要是钩子 —— 观众划到的前 2 秒决定去留,不要用铺垫开头
2. 每段语义完整,有观点或有具体案例
3. 单段控制在 {target} 秒左右,不要贪长

只输出 JSON 数组,不要任何解释（可以是空数组）:
[{{"start":起始序号,"end":结束序号,"score":分数,"reason":"选它的理由(10字内)"}}]

字幕:
{numbered}"""

    try:
        out = llm([{"role": "user", "content": prompt}], api_key, temperature=0.2)
        (work / f"highlight_response{suffix}.txt").write_text(out, encoding="utf-8")
        picks = parse_llm_json_array(out)
    except Exception as e:
        # LLM 偶发返回无法解析的格式（空/截断/字符串数组等），parse 会 raise。
        # 这里兜住：降级取前段出片，绝不因解析失败废掉整条（2026-08-31 线上崩溃）
        print(f"[金句] LLM 输出解析失败，降级取前段: {e}")
        picks = []

    if not picks:
        # Raw offline ASR contains repetitions and fixed-length cue fragments.
        # Judge a continuous argument across cues, rather than requiring each
        # individual ASR fragment to be a polished, sensational quotation.
        review_prompt = (
            f"你是{speaker}访谈编辑。以下是同一段连续讲话的原始ASR，"
            "序号之间不是语义边界，必须连起来读；口语重复和停顿不等于没有观点。"
            "请挑1到3段能够独立理解的具体判断、解释或案例。不要开场寒暄或主持人问题。"
            "保留原话，不编造数字，不要求夸张标题。只选语义闭合且信息量达到7/10的段落，"
            f"每段约20到{min(target,180)}秒。没有才返回[]。"
            "start/end必须是下面左栏的0起始序号，不是秒数。只输出JSON数组："
            '[{"start":0,"end":8,"score":8,"reason":"完整观点"}]。\n'+numbered)
        try:
            reviewed = llm([{"role":"user","content":review_prompt}], api_key,
                           temperature=0, max_tokens=2000, budget_sec=90)
            (work / f"highlight_review_response{suffix}.txt").write_text(reviewed, encoding="utf-8")
            picks = parse_llm_json_array(reviewed)
        except (ValueError, RuntimeError, KeyError, TypeError) as exc:
            print(f"[金句复核] {exc}")

    valid = []
    for p in picks:
        try:
            a, b = int(p["start"]), int(p["end"])
        except (ValueError, KeyError, TypeError):
            continue
        try:
            score = float(p.get("score", 10))
        except (TypeError, ValueError):
            score = 10.0
        if score < MIN_HIGHLIGHT_SCORE:
            print(f"[金句] 丢弃低分段 {a}-{b}（{score} 分 < {MIN_HIGHLIGHT_SCORE}）：{p.get('reason','')}")
            continue
        if 0 <= a <= b < len(cues):
            valid.append({"start": a, "end": b, "score": score, "reason": p.get("reason", "")})
            continue
        # 容错：LLM 误用 1-based 序号（把第一条当序号 1），统一减 1
        if 1 <= a <= b <= len(cues):
            valid.append({"start": a - 1, "end": b - 1, "score": score, "reason": p.get("reason", "")})
    if not valid:
        if allow_empty:
            # 长视频拆多段时，某段全是开场白/流程语很正常 —— 直接跳过这一段，
            # 不要硬凑。2026-09-02 事故：25 分钟北大演讲拆出 6 条，第 1 条是
            # 「希望大家能够安静下来，手机静音不干扰讲座」，就是无脑降级的结果。
            print("[金句] 本段无够格金句（或 LLM 未返回），跳过该段不出片")
            cache.write_text("[]", encoding="utf-8")
            return []
        # 单段素材：不废掉整条，降级取前 target 秒
        end_idx = 0
        total = 0.0
        for i, c in enumerate(cues):
            total += c["end"] - c["start"]
            if total >= target:
                end_idx = i
                break
        else:
            end_idx = len(cues) - 1
        valid = [{"start": 0, "end": max(1, end_idx), "reason": "降级取前段"}]
        print(f"[金句] LLM 未返回有效区间，降级取前段(至第 {end_idx} 条)")
    # 切片边界尽量落在完整句，但绝不能为找句号跨几十秒/几分钟回退。
    # SenseVoice 的句号并不稳定，旧版无限 while 会把 30~90s 金句扩成 2~4 分钟，
    # 既破坏开头钩子也直接拖垮渲染。这里将对齐限制在 8 秒/4 cue：
    # 先找最近的上一句结束；找不到就向前丢掉当前残句，宁可少几秒也不从半句话开头。
    SENT_TAIL = "。！？!?"
    MAX_ALIGN_SEC = 8.0
    MAX_ALIGN_CUES = 4
    for v in valid:
        a0, b0 = v["start"], v["end"]
        a, b = a0, b0
        found = False
        for j in range(a0 - 1, max(-1, a0 - MAX_ALIGN_CUES - 1), -1):
            if cues[a0]["start"] - cues[j]["end"] > MAX_ALIGN_SEC:
                break
            prev = (cues[j].get("text") or "").rstrip()
            if prev and prev[-1] in SENT_TAIL:
                a = j + 1
                found = True
                break
        if not found:
            for j in range(a0, min(b0, a0 + MAX_ALIGN_CUES) + 1):
                if cues[j]["end"] - cues[a0]["start"] > MAX_ALIGN_SEC:
                    break
                cur = (cues[j].get("text") or "").rstrip()
                if cur and cur[-1] in SENT_TAIL and j + 1 <= b0:
                    a = j + 1
                    break
        for j in range(b0, min(len(cues), b0 + MAX_ALIGN_CUES + 1)):
            if cues[j]["end"] - cues[b0]["end"] > MAX_ALIGN_SEC:
                break
            cur = (cues[j].get("text") or "").rstrip()
            b = j
            if cur and cur[-1] in SENT_TAIL:
                break
        v["start"], v["end"] = a, b
    # 去重 + 合并重叠区间。2026-09-02 实测事故：LLM 会对同一段内容给出多个
    # 重叠区间（如 14-62 返回 4 次，理由各不相同），边界对齐后 collapse 成完全
    # 相同的范围，却被当作多个独立片段各剪一遍再拼接 —— 180s 的目标片被拼成
    # 957s（16 分钟）。必须先合并再出片。
    valid.sort(key=lambda v: (v["start"], v["end"]))
    merged = []
    for v in valid:
        if merged and v["start"] <= merged[-1]["end"]:
            prev = merged[-1]
            prev["end"] = max(prev["end"], v["end"])
            if v.get("score", 0) > prev.get("score", 0):
                prev["score"], prev["reason"] = v.get("score", 0), v.get("reason", "")
            continue
        merged.append(dict(v))
    if len(merged) < len(valid):
        print(f"[金句] 合并重叠区间: {len(valid)} → {len(merged)} 段")
    # 总时长封顶：超过目标的 1.8 倍就按分数保留最好的几段
    def _dur(v):
        return cues[v["end"]]["end"] - cues[v["start"]]["start"]
    cap = target * 1.8
    if sum(_dur(v) for v in merged) > cap:
        merged.sort(key=lambda v: -v.get("score", 0))
        kept, acc = [], 0.0
        for v in merged:
            if acc + _dur(v) > cap and kept:
                continue
            kept.append(v)
            acc += _dur(v)
        merged = sorted(kept, key=lambda v: v["start"])
        print(f"[金句] 总时长超 {cap:.0f}s，按分数保留 {len(merged)} 段（{acc:.0f}s）")
    valid = merged
    cache.write_text(json.dumps(valid, ensure_ascii=False, indent=1), encoding="utf-8")
    for v in valid:
        d = cues[v["end"]]["end"] - cues[v["start"]]["start"]
        print(f"[金句] {v['start']}-{v['end']} ({d:.0f}s) {v.get('score','')} {v['reason']}")
    return valid


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
    a,b=spans[-1]
    return text.endswith('还更') or text[a:b] in {
        '更加','因为','所以','如果','那么','但是','而且','以及','把','被',
        '与','比','是','要','会','能','将','对','向','愿意','暂时'}


def dependent_caption_start(text):
    from presentation import word_spans
    spans=word_spans(text)
    if not spans:
        return False
    a,b=spans[0]
    return text[a:b] in {'的','地','得'}


def apply_semantic_groups(entries, texts, capacity, font_px=None, min_font_px=38):
    """Validate model-selected boundaries against source characters and timing."""
    from presentation import word_spans, wrap_words
    strip = lambda t: re.sub(r'[\s，。！？；：、]', '', t)
    chars=[]; entry_bounds=set(); punctuation_bounds=set()
    for i,e in enumerate(entries):
        a,b=float(e['start_sec']),float(e['end_sec'])
        if i+1<len(entries): b=min(b,float(entries[i+1]['start_sec']))
        original=e.get('zh','')
        for j,c in enumerate(original):
            if strip(c):
                chars.append((c,a+(b-a)*j/len(original),a+(b-a)*(j+1)/len(original)))
            elif c in '，。！？；：、':
                punctuation_bounds.add(len(chars))
        if chars:
            entry_bounds.add(len(chars))
    source=''.join(c[0] for c in chars)
    if not isinstance(texts,list) or not texts or not all(isinstance(t,str) and strip(t) for t in texts):
        raise ValueError('完整意群分组为空或格式错误')
    if ''.join(strip(t) for t in texts)!=source:
        raise ValueError('意群分组改写或丢失原话，拒绝烧录')
    bounds={0,len(source)}|{b for a,b in word_spans(source)}

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
        pieces = (split_long_group(offset, end) if b-a>8 else
                  [(offset, end, text, *fit_lines(text))])
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


def semantic_caption_entries(entries, api_key, layout, cache_path):
    """Use the language model for meaning; validate every character locally."""
    capacity=layout['line_capacity']
    cache_path=Path(cache_path)
    if cache_path.exists():
        try:
            return apply_semantic_groups(
                entries, json.loads(cache_path.read_text()), capacity,
                layout.get('subtitle_font_px'))
        except (ValueError,TypeError): pass
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
                '词序列：'+json.dumps(choices,ensure_ascii=False))
            answer=llm([{'role':'user','content':request}],api_key,
                       temperature=0,max_tokens=1200,budget_sec=45)
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
            '原始ASR含标点及时间：'+json.dumps(entries,ensure_ascii=False)+
            '。连续原文：'+transcript+'。带id的完整词序列：'+json.dumps(tokens,ensure_ascii=False))
    error=''
    for attempt in range(3):
        try:
            response=llm([{'role':'user','content':prompt+error}],api_key,temperature=0,max_tokens=6000,budget_sec=60)
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
            texts=[transcript[a:b] for a,b in zip([0]+breaks,breaks)]
            result=apply_semantic_groups(
                entries, texts, capacity, layout.get('subtitle_font_px'))
            cache_path.write_text(json.dumps(texts,ensure_ascii=False,indent=2))
            return result
        except (ValueError,KeyError,TypeError,RuntimeError) as exc:
            print('[意群重试]',str(exc),flush=True)
            error='\n上次输出未通过严格校验：'+str(exc)+'。请重新按原文输出全部字幕。'
            if 'texts' in locals():
                error+='上次分屏文本：'+json.dumps(texts,ensure_ascii=False)
    raise ValueError('完整意群字幕重试3次仍未通过'+error)


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
        longest = max((len(line) 