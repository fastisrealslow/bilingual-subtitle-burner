#!/usr/bin/env python3
"""出片:任意中文视频 → 3 分钟双语字幕短片。

和 bilingual-subtitle-burner 的 produce.py 的关系
------------------------------------------------
produce.py 是给**英文片源**设计的(direction 写死 en2zh、srt_lang 写死 en),
且依赖仓库里另外 5 个本地缺失的模块。这里不改它,而是针对中文源单独实现,
复用同一套思路:转写 → 挑金句 → 翻译 → 烧字幕 → 拼接。

针对中文股东会素材做的三处专门处理(produce.py 都没有):
  1. large-v3 + 词级时间戳重新组句 -- medium 会切出 60 秒一段的巨块没法当字幕
  2. 术语表纠正 ASR 专名 -- 现场录音把「安宫牛黄丸」听成「安牛」是常态
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
# 本地用已下好的绝对路径;CI 里用 HF 模型名(faster-whisper 自己拉)。
# 两边都是 large-v3:实测 4 核 runner 上实时率 1.17x,完全跑得动,
# 而 small 会输出繁体、把「安宫」听成「安公」,质量差距是决定性的。
WHISPER = os.environ.get("WHISPER_MODEL") or "/home/node/.cache/whisper/large-v3"

# ASR 后端选择：sensevoice（SenseVoice-Small，非LLM结构上不会死循环，推荐）|
# whisper（旧 large-v3 可回滚）| funasr（Fun-ASR-Nano，LLM架构有死循环风险，弃用仅保留）
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
MIN_SHORT_EDGE = 360
SOURCE_MIN_DURATION = 90
SOURCE_MAX_DURATION = 5400
FINGERPRINT_VERSION = 1
QUALITY_GATE_VERSION = 7
# 对标「园园滚雪球」实际成片后的音频卡规格：它的静态人物卡/活动拼图均以
# 9:16 竖版上传，B站桌面播放器自行补黑边；移动端则直接占满屏幕。我们保留
# 这种有效的版式，但不复制对方插画或照片资产，改用自有的通用编辑卡视觉。
AUDIO_CARD_WIDTH = 720
AUDIO_CARD_HEIGHT = 1280
AUDIO_CARD_TEMPLATE = "portrait_editorial_v2"
ALLOW_AUDIO_CARD = os.environ.get("ALLOW_AUDIO_CARD", "1") != "0"

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
    "安牛": "安宫牛黄丸", "安工": "安宫牛黄丸", "按钮": "安宫牛黄丸",
    "大人堂": "达仁堂", "达人塔": "达仁堂", "大二代": "达仁堂",
    "塑效": "速效救心丸", "速效": "速效救心丸",
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
    "负利增长": "复利增长", "历史的地位被低估": "历史的低位被低估",
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
            "同时记录看得到的外部账号/平台角标文字。只返回 JSON object："
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


def llm(messages, api_key, temperature=0.3, max_tokens=2000):
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
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
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


def _llm_smooth_cues(cues, api_key):
    """LLM 通顺化：去口水磕巴、通顺句子，保数字词义和时间戳。

    2026-08-29 实测：字幕「脏」= ASR 把口语原样转出（这个/就是/是吧/磕巴）。
    免费模型会篡改数字（99.8%→8.8%、2009→222012），故用付费 DeepSeek-V3 + 相似度校验。
    逐批处理、保持句数对应（时间戳不变），输出与原文相似度过低就保留原文（防篡改）。
    """
    if not api_key or not cues:
        return cues
    out_cues = list(cues)
    BATCH = 12
    for i in range(0, len(cues), BATCH):
        batch = cues[i:i + BATCH]
        numbered = "\n".join(f"{j}.{c['text']}" for j, c in enumerate(batch, 1))
        prompt = ("下面是 " + str(len(batch)) + " 句语音识别字幕，含口水话（这个/就是/是吧/我们）、磕巴重复。\n\n"
                  "请逐句做最小清理（去口水话、去磕巴重复、修明显错字），但严格要求：\n"
                  "1. 逐句输出，每句一行，格式「数字.清理后的句子」\n"
                  "2. 保持每句的编号、顺序、句数不变（共 " + str(len(batch)) + " 句）\n"
                  "3. 绝不改动任何数字、百分比、金额（如 99.8%、2009、8000）\n"
                  "4. 忠实原意，不合并、不拆分、不补充内容、不换用词\n\n"
                  "字幕：\n" + numbered)
        try:
            out = llm([{"role": "user", "content": prompt}], api_key, temperature=0.0, max_tokens=1200)
        except Exception as e:
            print(f"[通顺化] LLM 不可用，跳过这批: {e}", file=sys.stderr)
            continue
        # 解析逐行输出「数字.句子」
        parsed = {}
        for line in out.splitlines():
            line = line.strip()
            m = re.match(r"^(\d+)[.、．]\s*(.+)$", line)
            if m:
                parsed[int(m.group(1))] = m.group(2).strip()
        for j, c in enumerate(batch, 1):
            new_text = parsed.get(j)
            if not new_text:
                continue
            # 相似度校验：输出与原文差异过大（可能篡改），保留原文
            ratio = difflib.SequenceMatcher(None, c["text"], new_text).ratio()
            if ratio < 0.55:
                print(f"[通顺化] 第{i+j}句相似度过低({ratio:.2f})，保留原文", file=sys.stderr)
                continue
            out_cues[i + j - 1] = dict(c, text=new_text)
    return out_cues




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
    """转写入口：按 ASR_BACKEND 分发。质量闸门在这里统一把关——
    缓存命中和所有后端都要过闸（2026-09-01：原来闸门只在 sensevoice 分支内，
    缓存命中会直接绕过）。"""
    cache = work / "cues_raw.json"
    if cache.exists():
        print("[asr] 命中缓存")
        cues = json.loads(cache.read_text(encoding="utf-8"))
        _asr_quality_gate(cues, _audio_duration(src))
        return cues
    if ASR_BACKEND == "sensevoice":
        # sensevoice 内部已在 LLM 通顺化之前过闸（提前失败省 LLM 开销），此处不重复
        return _transcribe_sensevoice(src, work, api_key)
    if ASR_BACKEND == "funasr":
        cues = _transcribe_funasr(src, work)
    else:
        cues = _transcribe_whisper(src, work, api_key)
    _asr_quality_gate(cues, _audio_duration(src))
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


def _transcribe_sensevoice(src, work, api_key=None):
    """SenseVoice-Small 转写：非 LLM（CTC）架构，结构上不会死循环，自带标点。

    相对 Fun-ASR-Nano 的关键优势：2026-08-27 批量实测 Fun-ASR-Nano 长视频
    死循环率 43%（「吃一粒吃一粒」「吓吓吓」无限重复吞内容），调 temperature 治不了根；
    SenseVoice 是非自回归 CTC，不存在 LLM 贪婪解码死循环，10 条音频 0 死循环。
    CER 略高（7.8% vs 4.5%），但偶发错字可用术语表/LLM 兜，死循环兜不住。
    """
    import numpy as np
    import wave
    from sherpa_onnx import OfflineRecognizer
    cache = work / "cues_raw.json"

    # 1. 提取 16k 单声道 PCM
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
    print(f"[asr] 音频 {total_dur:.0f}s，SenseVoice-Small 转写")

    # 2. 加载模型（int8 onnx；use_itn=True 输出带标点）
    recognizer = OfflineRecognizer.from_sense_voice(
        model=f"{SENSEVOICE_DIR}/model.int8.onnx",
        tokens=f"{SENSEVOICE_DIR}/tokens.txt",
        num_threads=1, use_itn=True,
    )

    # 3. 分 chunk 转写（SenseVoice 整段 >60s 会退化输出空：实测 60s 正常、90s 退到 18 字、
    #    120s+ 只剩几个字）。故按 60s 切段 + overlap 3s 缓冲边界词，只输出核心区。
    CHUNK_SEC = 60.0
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
            seg_cues = _funasr_tokens_to_cues(list(r.tokens), list(r.timestamps), seg_start, seg_end - seg_start)
            for c in seg_cues:
                if t <= c["start"] < t + CHUNK_SEC:
                    cues.append(c)
        print(f"  [chunk {seg_start:6.0f}-{seg_end:6.0f}s] {len(r.tokens)} tokens", flush=True)
        t += CHUNK_SEC

    cues = _merge_cues(cues)
    # 去重 + GLOSSARY 纠错（SenseVoice 偶发小错字，如「金钱二」→「金钱上」）
    for c in cues:
        c["text"] = _de_loop_text(c["text"])
    cues = _dedup_consecutive(cues)
    for c in cues:
        c["text"] = fix_terms(c["text"])
    # 质量闸门（提前判一次：在 LLM 通顺化之前失败，省掉 LLM 调用开销；
    # transcribe() 入口还会再统一判一次，覆盖缓存命中的情况）
    _asr_quality_gate(cues, total_dur)
    # LLM 通顺化：去口水磕巴（付费 DeepSeek-V3 + 相似度防篡改，2026-08-29）
    cues = _llm_smooth_cues(cues, api_key)
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[asr] {len(cues)} 条字幕")
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
            # 去掉句末的句号/逗号/顿号/分号/冒号（字幕无需句号收尾），保留问号/感叹号（有语气）
            buf_text = buf_text.rstrip("。，、；：")
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
            buf_text += tok
            _flush()
        elif tok in "，、；：,;:":
            buf_text += tok
            if len(buf_text) >= MAX_CHARS - 4:
                _flush()
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
    m = re.search(r"(.{1,8}?)\1{3,}", text)
    if m:
        unit = m.group(1)
        return text[:m.start()] + unit * 2
    return text


def _dedup_consecutive(cues, sim=0.9):
    """相邻字幕条去重：连续相同或高度相似的只留第一条，延长 end。

    ASR 对同一句音频重复识别会产出连续重复的字幕（如「对吧？」×13 条、
    「我们最近看到的…」×8 条），观感极差。这里合并连续重复的条。
    """
    out = []
    for c in cues:
        t = c["text"]
        if out:
            prev = out[-1]["text"]
            if t == prev or (t and prev and difflib.SequenceMatcher(None, t, prev).ratio() > sim):
                out[-1]["end"] = c["end"]
                continue
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
        picks = parse_llm_json_array(out)
    except Exception as e:
        # LLM 偶发返回无法解析的格式（空/截断/字符串数组等），parse 会 raise。
        # 这里兜住：降级取前段出片，绝不因解析失败废掉整条（2026-08-31 线上崩溃）
        print(f"[金句] LLM 输出解析失败，降级取前段: {e}")
        picks = []

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
    # 边界对齐到句末标点：金句区间的头尾若是逗号分句（半句），
    # 则 start 向左退到最近的句号句、end 向右扩到最近的句号句，保证头尾完整。
    SENT_TAIL = "。！？!?"
    for v in valid:
        a, b = v["start"], v["end"]
        while a > 0 and cues[a]["text"].rstrip() and cues[a]["text"].rstrip()[-1] not in SENT_TAIL:
            a -= 1
        while b < len(cues) - 1 and cues[b]["text"].rstrip() and cues[b]["text"].rstrip()[-1] not in SENT_TAIL:
            b += 1
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


def make_ass(entries, path, W, H, card_style=False):
    """竖版适配:字号按高度算、抬到安全区。burner 的 make_ass 是按 16:9 调的,
    720x1280 下算出来才 29px,且会被平台底部 UI 遮住。

    字体必须可换:本机有 Microsoft YaHei,CI 的 ubuntu 上只有 Noto Sans CJK。
    libass 按名字找字体,找不到就 fallback 到无 CJK 字形的字体 -- 中文字幕
    会烧成一排豆腐块。workflow 里通过 ZH_FONT/EN_FONT 传入。"""
    font_zh = os.environ.get("ZH_FONT", "Microsoft YaHei")
    font_en = os.environ.get("EN_FONT", "Arial")
    vertical = H > W
    if vertical:
        # 竖屏：按高度比例 + 抬高避开底部 UI
        zh = max(38, int(H * 0.05))
        en = max(26, int(H * 0.035))
        mv = int(H * 0.09)
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
                lines = lines[:1] + ["".join(lines[1:])]  # 最多 2 行
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
            lines = lines[:1] + [" ".join(lines[1:])]  # 最多 2 行
        return "\\N".join(lines)

    def ts(s):
        return f"{int(s//3600)}:{int(s%3600//60):02d}:{s%60:05.2f}"

    # 音频卡沿用对标账号最易读的「黄字黑边」字幕；真实原画仍保持白字，避免
    # 在浅色/暖色现场画面上产生不必要的品牌化偏色。
    zh_color = "&H0000D7FF" if card_style else "&H00FFFFFF"
    zh_outline = 5 if card_style else 3
    L = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
         "", "[V4+ Styles]",
         "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
         "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
         "MarginL, MarginR, MarginV, Encoding",
         f"Style: EN,{font_en},{en},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
         f"-1,0,0,0,100,100,0,0,1,2,1,2,20,20,{mv + zh*2 + 10},1",
         f"Style: ZH,{font_zh},{zh},{zh_color},&H000000FF,&H00000000,"
         f"&H80000000,-1,0,0,0,100,100,0,0,1,{zh_outline},1,2,20,20,{mv},1",
         "", "[Events]",
         "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
         "Effect, Text"]
    for e in entries:
        a, b = ts(e["start_sec"]), ts(e["end_sec"])
        et, zt = wrap(e.get("en"), ew, False), wrap(e.get("zh"), zw, True)
        if et:
            L.append(f"Dialogue: 0,{a},{b},EN,,0,0,0,,{et}")
        if zt:
            L.append(f"Dialogue: 0,{a},{b},ZH,,0,0,0,,{zt}")
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


def _render_clean_preview(src, work, video_filter, duration):
    """渲染一小段清理后预览，供硬字幕二次复检。"""
    preview = Path(work) / "clean_preview.mp4"
    start = max(0.0, min(duration * 0.35, max(0.0, duration - 24.0)))
    clip_duration = max(4.0, min(24.0, duration - start))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}",
           "-t", f"{clip_duration:.2f}", "-i", str(src)]
    if video_filter:
        cmd += ["-vf", video_filter]
    cmd += ["-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            str(preview)]
    subprocess.run(cmd, check=True, capture_output=True)
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


def run_source_quality_gate(src, work, speaker, api_key, report_path=None):
    """下载后的素材闸门；任何 ASR、切片和编码开始前必须通过。"""
    report_path = Path(report_path or (work / "source_quality.json"))
    report_path.parent.mkdir(parents=True, exist_ok=True)
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
    except Exception as e:
        # 质检服务未知异常也必须失败关闭，不能把“没检成”当成“已合格”。
        report["reason"] = f"素材质检不可用：{e}"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8")
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


def copywrite(cues, sel, speaker, occasion, api_key, work, suffix=""):
    """LLM 生成 B站标题/简介/标签(参考原库 scripts/copywrite.py)。

    钩子式标题:prompt 要求带反常识/数字/冲突钩子（对标竞品高播放标题），但严禁编造，结果落 meta.json,
    投稿脚本优先读这里,不再用 occasion 硬拼。
    suffix 区分长视频拆多条的各段缓存（否则每段复用同一条文案）。
    """
    cache = work / f"copywrite{suffix}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    sample = "\n".join(cues[i]["text"] for i in sel[:20])
    prompt = f"""这是{speaker}在「{occasion}」发言的字幕节选:

{sample}

为它生成 B站投稿文案。

【标题写法：必须模仿高播放竞品的「原话引用体」】
2026-09-01 实测同期 B站「林园」内容 220 条，播放中位数对比：
  竞品「园园滚雪球」1956、「唐晶晶的价值观」1006、「投资就是滚雪球」584
  我们「园来滚雪球」只有 23 —— 差 33 倍，处于第 1 百分位。
差距的核心是标题写法：

  ✅ 竞品（高播放）= 「{speaker}：」+ 他本人说的原话金句（第一人称、口语、有态度）
     「股神林园：现在消费和医药的回报是我从事资本市场以来最值得的时候」
     「林园：股市里赚到大钱的人都是"呆子""笨蛋"」
     「股神林园：没留意泡泡玛特这类新消费，精神消费就看它从事的门槛高不高」
  ❌ 我们（低播放）= 第三人称摘要体，像新闻导语
     「林园谈创新药投资：为何不投癌症而选中药」
     「林园谈茅台：600元时不再确定，超前20年的投资逻辑」

标题要求（严格遵守）:
1. 必须以「{speaker}：」或「股神{speaker}：」开头
2. 冒号后面必须是**从字幕里摘出来的他本人的原话**（可精简去口水词、可合并相邻两句，但不能改变意思、不能替换成书面语）
3. 长度 26~36 字（竞品中位 32~34 字；我们过去 22 字太短、信息量不足）
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
    try:
        out = llm([{"role": "user", "content": prompt}], api_key, temperature=0.4)
        m = re.search(r"\{.*\}", out, re.S)
        d = json.loads(m.group(0))
        assert d.get("title")
    except Exception:
        out = ""
        d = {"title": f"{speaker}：{occasion}"[:36],
             "desc": f"{speaker}在{occasion}的发言精选。",
             "tags": [speaker, "价值投资"]}
    # 兜底清洗：prompt 说了不许带链接，但 LLM 不一定听话，程序层再洗一遍
    if d.get("desc"):
        clean_desc = re.sub(r"https?://\S+|www\.\S+|t\.cn/\S+|@[\w\u4e00-\u9fa5]{2,20}", "", d["desc"])
        clean_desc = re.sub(r"[（(]\s*[）)]|\s{2,}", " ", clean_desc).strip(" ，,、;；")
        if clean_desc != d["desc"]:
            print(f"[文案] 简介已清除链接/引流信息")
            d["desc"] = clean_desc
    d.setdefault("tags", [speaker])
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


def make_cover(src, seg_start, seg_end, title, speaker, out_path,
               video_filter="", preferred_time=None):
    """封面:抽帧 → 人脸检测裁切 → 16:9 → 底部渐变 → 标题大字。

    竖屏视频也输出 16:9 横屏封面(2026-08-23 修复):B站封面信息流是横屏显示,
    竖屏画面居中贴到 1280x720,两侧用放大模糊的原帧做背景。
    竖屏排版自适应(2026-08-21 修复):之前用横屏硬编码参数(64px字号×17字/行),
    720px 宽的竖屏画布装不下 1007px 文字 → 标题溢出、人脸被挤。
    小帧人脸检测:360x640 低清源 haar 检不出脸 → 提前放大再检测。
    抽帧位置:取段落偏前位置,避开字幕最密集的说话中段。
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    tmp = out_path.with_suffix(".frame.png")
    mid = seg_start + (seg_end - seg_start) / 2
    # 人物闸门给出的 preferred_time 已经与参考照核验为本人；围绕该时间取三帧。
    # 没有核验时间时才退回原来的段内多帧策略。
    frames = []
    if preferred_time is not None:
        sample_times = [max(0, preferred_time + d) for d in (-0.6, 0, 0.6)]
    else:
        sample_times = [max(0, mid + p * (seg_end - seg_start))
                        for p in (-0.30, -0.20, -0.10, 0, 0.10, 0.20, 0.30)]
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

    # 多帧人脸聚类，选「跨帧持续出镜」的主讲人（林园），而非单帧「大且居中」的主持人。
    # （2026-08-29 修复：专访里女主持居中脸大，旧评分误选主持人；现统计多帧出现次数）
    best_frame = frames[0] if frames else tmp
    best_face = None
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = _cascade(cascade_path)
        groups = []  # {'center':(cx,cy,wn), 'count':int, 'faces':[(x,y,w,h,fp)]}
        for fp in frames:
            img_cv = cv2.imread(str(fp))
            if img_cv is None:
                continue
            fh, fw = img_cv.shape[:2]
            scale = 2.0 if max(fh, fw) < 720 else 1.0
            if scale > 1.0:
                img_cv = cv2.resize(img_cv, (fw*2, fh*2), interpolation=cv2.INTER_CUBIC)
                fh, fw = fh*2, fw*2
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3,
                                             minSize=(120, 120) if scale > 1.0 else (80, 80))
            for f in faces:
                fx, fy, fw2, fh2 = f
                cx = (fx + fw2/2) / fw   # 归一化中心
                cy = (fy + fh2/2) / fh
                wn = fw2 / fw            # 归一化宽度
                best_g, best_d = None, 0.15
                for g in groups:
                    gcx, gcy, gwn = g["center"]
                    d = ((cx - gcx) ** 2 + (cy - gcy) ** 2 + (wn - gwn) ** 2) ** 0.5
                    if d < best_d:
                        best_d, best_g = d, g
                if best_g is not None:
                    n = best_g["count"]
                    best_g["center"] = ((best_g["center"][0]*n + cx) / (n+1),
                                        (best_g["center"][1]*n + cy) / (n+1),
                                        (best_g["center"][2]*n + wn) / (n+1))
                    best_g["count"] = n + 1
                    best_g["faces"].append((fx/scale, fy/scale, fw2/scale, fh2/scale, fp))
                else:
                    groups.append({"center": (cx, cy, wn), "count": 1,
                                   "faces": [(fx/scale, fy/scale, fw2/scale, fh2/scale, fp)]})
        if groups:
            # 跨帧出现次数最多 = 主讲人；同簇内取面积最大的那一帧
            best_g = max(groups, key=lambda g: g["count"])
            fx, fy, fw2, fh2, fp = max(best_g["faces"], key=lambda x: x[2] * x[3])
            best_face = (int(fx), int(fy), int(fw2), int(fh2))
            best_frame = fp
    except Exception as e:
        # 2026-09-01 血的教训：这里原来是 except: pass 静默吞异常。
        # CI 的 pip install opencv-python 未锁版本，装到 OpenCV 5.0 后
        # cv2.CascadeClassifier 被移除 → 人脸检测全程失败但无任何日志 →
        # 封面退化成「中间裁一刀取第一帧」→ 出现「观众后脑勺封面」(盲评 2/10)、
        # 「女主播当封面」(4/10)。异常必须喊出来。
        print(f"[封面] ⚠️ 人脸检测失败({type(e).__name__}: {e})，退化为居中裁切", file=sys.stderr)

    img = Image.open(best_frame).convert("RGB")
    w, h = img.size

    # 以人脸为中心裁切,保持目标比例
    vertical = h > w
    if vertical:
        # 竖屏视频 → 16:9 横屏封面（B站封面是横屏显示）：
        # 竖屏主体居中贴到 1280x720，两侧用放大模糊的原帧做背景（毛玻璃，非纯黑边）
        tw = min(w, int(h * 9 / 16))
        if best_face is not None:
            fx, fy, fw, fh = best_face
            cx = fx + fw // 2
            x0 = max(0, min(cx - tw // 2, w - tw))
            top = max(0, fy - int(fh * 0.8))
            bottom = min(h, top + int(tw * 16 / 9))
            if bottom - top < int(tw * 16 / 9):
                top = max(0, bottom - int(tw * 16 / 9))
            img = img.crop((x0, top, x0 + tw, bottom))
        else:
            x0 = (w - tw) // 2
            img = img.crop((x0, 0, x0 + tw, h))
        # 背景：原始帧放大到 1280x720 并高斯模糊
        bg = Image.open(best_frame).convert("RGB").resize((1280, 720), Image.LANCZOS) \
            .filter(ImageFilter.GaussianBlur(30))
        # 竖屏主体缩放到高度 720；以人脸为锚水平偏移，让人脸落到 1280 中央
        # （2026-08-27 实拍：原来横条居中贴，人脸偏左不居中，主体「没放出来」）
        fg_h = 720
        fg_w = max(1, int(img.width * fg_h / img.height))
        fg = img.resize((fg_w, fg_h), Image.LANCZOS)
        canvas = bg.copy()
        if best_face is not None:
            face_x_in_fg = int(cx * fg_w / max(1, tw))  # 人脸在缩放后 fg 中的 x
            fg_x = 1280 // 2 - face_x_in_fg
        else:
            fg_x = (1280 - fg_w) // 2
        fg_x = max(0, min(fg_x, 1280 - fg_w))  # 边界保护
        canvas.paste(fg, (fg_x, 0))
        img = canvas
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

    font_path = None
    for cand in ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
                 "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
                 "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"]:
        if Path(cand).exists():
            font_path = cand
            break
    idx = _sc_face_index(font_path) if font_path and font_path.endswith(".ttc") else 0
    # 字号按画布宽自适应（用户 2026-08-26 反馈封面文字再大、标签+0.5倍）
    title_size = 48 if W < 1000 else 74
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
    lines = wrap_cover_title(title, chars_per_line, max_lines)
    y = H - margin_bottom - line_h * len(lines)
    for ln in lines:
        # 白字黑边(描边厚度自适应)
        stroke = 3 if W < 1000 else 2
        d.text((40, y), ln, font=f_title, fill=(255, 255, 255),
               stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += line_h
    img.save(out_path, quality=92)
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


def has_existing_subtitles(src):
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
        cov = ocr_row_coverage(src, frames=8)
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
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def ocr_row_coverage(src, frames=6, max_w=640):
    """抽帧 OCR，统计每 1% 行位置被文字框覆盖的「帧比例」(长度 100 的列表)。

    2026-09-01 换掉原来的 Canny 边缘密度方案：边缘密度会把人物轮廓、K线图、
    装饰线条都当成文字，还漏检半透明台标；实测 5 条有标准答案的素材，
    OCR 全部命中（含之前漏掉的「红星资本局」「金融界 JRJ.com」台标）。
    用「帧比例」而不是单帧结果，是为了区分常驻贴片和一闪而过的内容。
    """
    key = str(src)
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
        if got:
            cov = (hit / got).tolist()
    except Exception as e:
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


def safe_crop_plan(src, W, H, stable=0.4, clean=0.2, max_cut=0.24):
    """算安全裁切方案 (crop_w, crop_h, crop_x, crop_y)，只为「腾出干净的字幕位」。

    历史教训（2026-09-01 三次迭代）：
      ① 按检测带裁掉所有贴片 → 中部贴片去不掉、还切到人头，失败回滚；
      ② 高斯模糊遮挡 → 盲评 3/10，比不处理更差；
      ③ 固定裁检测到的底部带 → 多行字幕只切掉一行，16 组只过 11 组(69%)。
    现在的做法：用 OCR 逐行统计「文字覆盖帧比例」，**从底部往上裁到干净为止**。

    参数：
      stable  行覆盖率 ≥ 此值视为常驻文字（贴片/硬字幕）
      clean   裁完后底部区域允许的最大覆盖率
      max_cut 总裁切上限。实看对标账号后收紧到 24%：它对横屏原片基本不裁，
              竖屏包装则保留完整主体；超过这个比例不再硬切，转音频卡重建。
    """
    cov = ocr_row_coverage(src)
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
    bottom_now = sum(cov[70:90]) / 20
    if bottom_now <= clean:
        print(f"[裁切] 字幕区本就干净（70~90% 覆盖 {bottom_now:.0%}），无需裁切")
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


def _chunk_by_time(cues, chunk_sec=240):
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
    """音频卡标题优先按词换行；词边界装不下时退回定长分行。

    `wrap_cover_title` 为普通封面设计，会为了不拆词而正确地拒绝超行标题；
    音频卡标题已经按画布容量截短，不能再因英文长词或混排词边界废掉成片。
    """
    try:
        return wrap_cover_title(title, chars_per_line, max_lines=max_lines)
    except VisualQualityError:
        return [title[i:i + chars_per_line]
                for i in range(0, len(title), chars_per_line)][:max_lines]


def extract_audio_card_portrait(src, at_sec, out_path):
    """从已核验原片提取主讲人肖像；失败时返回 None 供模板回退。

    只截取最大人脸附近的正方形区域，不把原片标题条或账号角标带进卡片。
    该图仅是同一素材的视觉摘取，不做身份推断；人物身份仍由前置多帧门禁负责。
    """
    try:
        import cv2
        cap = cv2.VideoCapture(str(src))
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, float(at_sec)) * 1000)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _cascade(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        ).detectMultiScale(gray, 1.1, 4, minSize=(48, 48))
        if len(faces) == 0:
            return None
        x, y, w, h = max(faces, key=lambda box: box[2] * box[3])
        H, W = frame.shape[:2]
        # 若源片顶部有持续标题条，肖像裁切也必须绕开它；否则虽然主画布已
        # 重建，标题残片仍会被带进圆形人物区。行覆盖结果在前置门禁已缓存，
        # 生产路径通常不会增加一次 OCR。
        top_guard = 0
        cov = ocr_row_coverage(src)
        run_start = None
        for i, ratio in enumerate(cov[:55]):
            if ratio >= 0.50 and run_start is None:
                run_start = i
            elif ratio < 0.50 and run_start is not None:
                if i - run_start >= 8:
                    top_guard = int(H * min(55, i + 2) / 100)
                    run_start = None
                    break
                run_start = None
        if not top_guard and run_start is not None and 55 - run_start >= 8:
            top_guard = int(H * 0.55)

        side = int(max(w, h) * 1.75)
        side = min(side, W, H - top_guard)
        if side < max(w, h):
            return None
        cx = x + w // 2
        cy = y + h // 2 + int(h * 0.22)
        x0 = max(0, min(W - side, cx - side // 2))
        y0 = max(top_guard, min(H - side, cy - side // 2))
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
                    portrait_path=None):
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

    display_topic = re.sub(r"\s+", " ", topic or "投资观点精选").strip()
    if len(display_topic) > 39:
        display_topic = display_topic[:38] + "…"

    if vertical:
        margin_x = int(width * 0.07)
        # 顶部身份标签与对标账号的大标题层级相同，但采用自有红金配色。
        label = f"{speaker} · 公开发言原声"
        label_box = draw.textbbox((0, 0), label, font=small_font)
        label_w = label_box[2] - label_box[0] + int(34 * unit)
        label_h = label_box[3] - label_box[1] + int(20 * unit)
        label_x = (width - label_w) // 2
        label_y = int(height * 0.105)
        draw.rounded_rectangle((label_x, label_y, label_x + label_w,
                                label_y + label_h),
                               radius=max(8, int(12 * unit)),
                               fill=(155, 39, 35))
        draw.text((label_x + int(17 * unit), label_y + int(7 * unit)), label,
                  font=small_font, fill=(255, 246, 220))

        lines = _wrap_audio_card_title(display_topic, 13, max_lines=3)
        line_h = int(topic_size * 1.22)
        title_y = int(height * 0.17)
        for i, line in enumerate(lines):
            box = draw.textbbox((0, 0), line, font=topic_font, stroke_width=3)
            x = (width - (box[2] - box[0])) // 2
            draw.text((x, title_y + i * line_h), line, font=topic_font,
                      fill=(255, 205, 24),
                      stroke_width=max(2, int(3 * unit)),
                      stroke_fill=(67, 29, 20))

        # 自有抽象人物/话筒视觉：保持“人物卡”的重心，但不复制对方插画。
        cx = width // 2
        halo_r = int(width * 0.29)
        halo_y = int(height * 0.57)
        draw.ellipse((cx - halo_r, halo_y - halo_r, cx + halo_r,
                      halo_y + halo_r), fill=(245, 242, 232),
                     outline=(193, 151, 64), width=max(3, int(5 * unit)))
        portrait_used = False
        if portrait_path and Path(portrait_path).is_file():
            try:
                portrait_r = int(width * 0.255)
                portrait = Image.open(portrait_path).convert("RGB")
                portrait = ImageOps.fit(
                    portrait, (portrait_r * 2, portrait_r * 2),
                    method=Image.Resampling.LANCZOS)
                mask = Image.new("L", portrait.size, 0)
                ImageDraw.Draw(mask).ellipse(
                    (0, 0, portrait.size[0] - 1, portrait.size[1] - 1),
                    fill=255)
                image.paste(portrait,
                            (cx - portrait_r, halo_y - portrait_r), mask)
                portrait_used = True
            except Exception as e:
                print(f"[音频卡] 肖像嵌入失败，使用通用人物图标: {e}",
                      file=sys.stderr)
        if not portrait_used:
            head_r = int(width * 0.105)
            head_y = int(height * 0.505)
            draw.ellipse((cx - head_r, head_y - head_r, cx + head_r,
                          head_y + head_r), fill=(35, 48, 64))
            shoulder_w = int(width * 0.27)
            shoulder_h = int(height * 0.125)
            draw.rounded_rectangle((cx - shoulder_w, int(height * 0.57),
                                    cx + shoulder_w,
                                    int(height * 0.57) + shoulder_h),
                                   radius=int(width * 0.11), fill=(35, 48, 64))
        mic_x = cx + int(width * 0.19)
        mic_y = int(height * 0.56)
        mic_r = int(width * 0.045)
        draw.ellipse((mic_x - mic_r, mic_y - mic_r,
                      mic_x + mic_r, mic_y + mic_r), fill=(193, 151, 64))
        draw.line((mic_x, mic_y + mic_r, mic_x, mic_y + mic_r * 3),
                  fill=(193, 151, 64), width=max(5, int(8 * unit)))
        draw.arc((mic_x - mic_r * 2, mic_y, mic_x + mic_r * 2,
                  mic_y + mic_r * 3), 5, 175, fill=(193, 151, 64),
                 width=max(4, int(6 * unit)))

        note = "画面重建 · 经核验讲话音频"
        note_box = draw.textbbox((0, 0), note, font=small_font)
        draw.text(((width - (note_box[2] - note_box[0])) // 2,
                   int(height * 0.765)), note, font=small_font,
                  fill=(89, 94, 99))
        draw.line((margin_x, int(height * 0.805), width - margin_x,
                   int(height * 0.805)), fill=(196, 190, 177),
                  width=max(1, int(2 * unit)))
    else:
        # B站封面仍需 16:9：沿用同一视觉语言，避免直接拿竖卡充当横封面。
        panel = (int(width * 0.07), int(height * 0.17),
                 int(width * 0.93), int(height * 0.72))
        draw.rounded_rectangle(panel, radius=max(18, int(28 * unit)),
                               fill=(245, 242, 232),
                               outline=(193, 151, 64),
                               width=max(2, int(4 * unit)))
        icon_x, icon_y = int(width * 0.19), int(height * 0.43)
        icon_r = int(height * 0.11)
        draw.ellipse((icon_x - icon_r, icon_y - icon_r,
                      icon_x + icon_r, icon_y + icon_r), fill=(35, 48, 64))
        draw.text((int(width * 0.29), int(height * 0.25)),
                  f"{speaker}公开发言原声", font=headline_font,
                  fill=(45, 50, 55))
        lines = _wrap_audio_card_title(display_topic, 19, max_lines=3)
        for i, line in enumerate(lines):
            draw.text((int(width * 0.29),
                       int(height * 0.40) + i * int(topic_size * 1.25)),
                      line, font=topic_font, fill=(155, 39, 35))
        draw.text((int(width * 0.29), int(height * 0.64)),
                  "画面重建 · 经核验讲话音频", font=small_font,
                  fill=(89, 94, 99))

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
    return out_path


def _produce_one(src, work, out, cues, speaker, occasion, api_key,
                 existing_subtitles, W, H, suffix, pick_cache_suffix="", target_sec=None,
                 allow_empty=False, visual_report=None, source_report=None):
    """出一段视频。suffix='' 或 '_2' 等。target_sec 控制时长（短金句 180 / 中视频 420）。
    返回 meta dict；allow_empty=True 且本段没有够格金句时返回 None（不出片）。"""
    picks = pick_highlights(cues, speaker, api_key, work, pick_cache_suffix, target_sec,
                            allow_empty=allow_empty)
    if not picks:
        print(f"[段{suffix or '1'}] 无够格金句，跳过不出片")
        return None
    sel = sorted({i for p in picks for i in range(p["start"], p["end"] + 1)})
    total_sel = sum(cues[i]["end"] - cues[i]["start"] for i in sel)
    print(f"[段{suffix or '1'}] 选 {len(sel)} 条字幕,约 {int(total_sel)//60}:{int(total_sel)%60:02d}")

    # 质检与成片严格复用同一份清理计划，避免门禁验证 A、实际编码却执行 B。
    source_report = source_report or {}
    strategy = source_report.get("clean_strategy", "direct")
    clean_vf = source_report.get("clean_video_filter") or f"crop={W//2*2}:{H//2*2}:0:0"
    clean_resolution = source_report.get("clean_output_resolution") or {}
    crop_w = int(clean_resolution.get("width") or (W // 2 * 2))
    crop_h = int(clean_resolution.get("height") or (H // 2 * 2))
    _logos = source_report.get("detected_corner_logos") or []
    print(f"[干净画面] strategy={strategy} output={crop_w}x{crop_h}")

    brand = brand_watermark_path()
    audio_card = None
    audio_card_portrait = None
    if strategy == "audio_card":
        # 用本条已选内容的第一段做卡片标题，避免所有音频卡都只写泛化场次名。
        # 这对应对标账号“顶部标题直接概括本条观点”的有效做法。
        first_pick = picks[0]
        topic_text = "".join(cues[i]["text"] for i in range(
            first_pick["start"], first_pick["end"] + 1))
        portrait_at = ((visual_report or {}).get("best_cover_time")
                       or (cues[first_pick["start"]]["start"]
                           + cues[first_pick["end"]]["end"]) / 2)
        audio_card_portrait = extract_audio_card_portrait(
            src, portrait_at, work / f"audio_card_portrait{suffix}.png")
        audio_card = make_audio_card(
            work / f"audio_card{suffix}.png", speaker, topic_text,
            portrait_path=audio_card_portrait)
    en_map = {}
    parts = []
    for n, p in enumerate(picks, 1):
        idx = list(range(p["start"], p["end"] + 1))
        s0, s1 = cues[idx[0]]["start"], cues[idx[-1]]["end"]
        entries = [{"start_sec": cues[i]["start"] - s0,
                    "end_sec": cues[i]["end"] - s0,
                    "zh": cues[i]["text"], "en": en_map.get(i, "")} for i in idx]
        ass = work / f"seg{suffix}{n}.ass"
        make_ass(entries, ass, crop_w, crop_h,
                 card_style=(strategy == "audio_card"))
        seg = work / f"seg{suffix}{n}.mp4"
        vertical = H > W
        seg_dur = s1 - s0
        # 片头片尾淡入淡出 0.4s：修「开头结束断帧」的视觉突兀（2026-08-27）
        fade = f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, seg_dur - 0.4):.2f}:d=0.4"
        if strategy == "audio_card":
            vf = f"ass={ass},{fade}"
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-loop", "1", "-framerate", "30", "-i", str(audio_card),
                "-ss", str(s0), "-t", str(seg_dur), "-i", str(src),
                "-filter_complex", f"[0:v]{vf}[outv]",
                "-map", "[outv]", "-map", "1:a:0",
            ]
        else:
            vf = f"{clean_vf},ass={ass},{fade}"
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error", "-ss", str(s0),
                "-t", str(seg_dur), "-i", str(src),
                "-loop", "1", "-framerate", "30", "-i", str(brand),
                "-filter_complex", brand_overlay_filter(vf, crop_w, crop_h),
                "-map", "[outv]", "-map", "0:a:0",
            ]
        cmd += [
            "-af", "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-r", "30",
            "-t", str(seg_dur), "-shortest", str(seg),
        ]
        subprocess.run(cmd, check=True)
        parts.append(seg)

    lst = work / f"concat{suffix}.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    final = out / f"final{suffix}.mp4"
    final_name = final.name          # 真实文件名，跳段后编号会与列表下标脱节，必须回传
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", str(lst), "-c", "copy", str(final)],
                   check=True)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(final)],
        capture_output=True, text=True).stdout.strip() or 0)
    final_w, final_h = ensure_min_short_edge(final, label="裁切后成片")
    remaining_logos = detect_corner_logos(final, frames=6, strict=True)
    external_logos = [box for box in remaining_logos
                      if not _inside_brand_watermark_region(
                          box, final_w, final_h)]
    if external_logos:
        raise VisualQualityError(f"成片清理后仍检出外部角标：{external_logos}")
    transcript_text = "".join(cues[i]["text"] for i in sel)
    fingerprints = build_content_fingerprints(final, transcript_text)

    cw = copywrite(cues, sel, speaker, occasion, api_key, work, pick_cache_suffix)
    cover_name = "cover_16x9.jpg"
    cover = out / (f"cover{suffix}.jpg" if suffix else cover_name)
    try:
        p0 = picks[0]
        if strategy == "audio_card":
            make_audio_card(cover, speaker, cw["title"],
                            width=1280, height=720,
                            portrait_path=audio_card_portrait)
        else:
            make_cover(src, cues[p0["start"]]["start"], cues[p0["end"]]["end"],
                       cw["title"], speaker, cover, video_filter=clean_vf,
                       preferred_time=(visual_report or {}).get("best_cover_time"))
    except Exception as e:
        raise VisualQualityError(f"封面生成/人物/角标复检失败：{e}") from e
    return {
        "final": final_name,
        "title": cw["title"], "desc": cw["desc"], "tags": cw["tags"],
        "cover": cover.name if cover else None,
        "duration_sec": round(dur, 1),
        "resolution": {"width": final_w, "height": final_h,
                       "short_edge": min(final_w, final_h)},
        "fingerprints": fingerprints,
        "watermark_removed": strategy != "direct",
        "watermark_verified": True,
        "clean_strategy": strategy,
        "audio_card_template": (AUDIO_CARD_TEMPLATE
                                if strategy == "audio_card" else None),
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


def main():
    ap = argparse.ArgumentParser()
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
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_file():
        sys.exit(f"找不到源:{src}")
    api_key = load_key()
    if not api_key:
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

    # 长视频按时间切成多段，每段出一条；短视频出 1 条
    chunks = _chunk_by_time(cues)
    # 去重：先字符级（逐字重复兜底），再 LLM 观点去重（语义重复），2026-08-29
    chunks = _dedup_chunks_char(chunks, cues)
    chunks = _dedup_chunks_by_llm(chunks, cues, api_key, work)
    if args.dry_run:
        # dry-run 只看金句，不切分
        p = pick_highlights(cues, args.speaker, api_key, work)
        for pp in p:
            print(f"\n── {pp['reason']} ──")
            for i in range(pp["start"], pp["end"] + 1):
                print(f"  {cues[i]['text']}")
        return 0

    # 长视频拆多条时，选「字幕条数最多」的一段做中视频（7分钟话题片），其余短金句
    # （2026-08-29 对标竞品：中视频是播放最高的档，一段信息量最足的内容做话题展开）
    mid_idx = None
    if len(chunks) > 1:
        mid_idx = max(range(len(chunks)), key=lambda i: chunks[i][1] - chunks[i][0] + 1)

    metas = []
    for ci, (a, b) in enumerate(chunks):
        suffix = "" if len(chunks) == 1 else f"_{ci + 1}"
        seg_cues = cues[a:b + 1]
        target_sec = TARGET_SEC_MID if ci == mid_idx else TARGET_SEC
        if ci == mid_idx:
            print(f"[中视频] 第{ci+1}段做成 {TARGET_SEC_MID//60} 分钟话题片")
        try:
            m = _produce_one(src, work, out, seg_cues, args.speaker, args.occasion,
                             api_key, existing_subtitles, W, H, suffix,
                             pick_cache_suffix=suffix, target_sec=target_sec,
                             allow_empty=len(chunks) > 1,
                             visual_report=visual_report,
                             source_report=source_report)
        except VisualQualityError as e:
            print(json.dumps({"stage": "visual-quality", "reason": str(e),
                              "part": ci + 1}, ensure_ascii=False), file=sys.stderr)
            return 2
        if m is not None:
            metas.append(m)

    # 「去水印」标记必须反映真实裁切结果，不能写死 True（2026-09-01 发现线上
    # 全部标着 ✓去水印，实际台标/字幕原样保留）
    _t_f, _b_f = detect_overlay_bands(src)
    _wm_cropped = any(bool(m.get("watermark_removed")) for m in metas)
    _wm_verified = all(bool(m.get("watermark_verified")) for m in metas)
    print(f"[裁切] 贴片区 顶{_t_f:.0%} 底{_b_f:.0%}；"
          f"removed={_wm_cropped} verified={_wm_verified}")

    if not metas:
        print("❌ 所有段都没有够格金句，本条素材不出片", file=sys.stderr)
        return 1

    # 写 meta.json：单条保持兼容，多条记录列表
    if len(metas) == 1:
        final_meta = {
            "slug": args.slug, "source": str(src), "speaker": args.speaker,
            "occasion": args.occasion, **metas[0],
            "quality_gate_version": QUALITY_GATE_VERSION,
            "source_platform": platform,
            "watermark_cropped": _wm_cropped,
            "watermark_verified": _wm_verified,
            "visual_identity": visual_report,
            "subtitles_burned": not existing_subtitles,
            "has_existing_subtitles": existing_subtitles,
            "raw_has_existing_subtitles": bool(
                source_report.get("raw_has_existing_subtitles")),
            "clean_filter_verified": bool(
                source_report.get("clean_filter_verified")),
            "vertical": vertical,
            "cue_count": sum(1 for _ in cues),
            "asr_model": "faster-whisper large-v3",
            "llm": MODELS[0], "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        (out / "meta.json").write_text(json.dumps(final_meta, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    else:
        # 多条：meta.json 是列表，每个元素含 final 文件名
        final_meta = [
            {"slug": args.slug, "source": str(src), "speaker": args.speaker,
             "occasion": args.occasion, "part": i + 1,
             "quality_gate_version": QUALITY_GATE_VERSION,
             # 文件名取 _produce_one 回传的真实值：某段无金句被跳过后，
             # 列表下标 != 原始段号，按下标拼 final_{i+1}.mp4 会指向不存在的文件
             # （2026-09-02 加「跳过低质段」时发现的隐患）
             **m,
             "source_platform": platform,
             "watermark_cropped": _wm_cropped,
             "watermark_verified": _wm_verified,
             "visual_identity": visual_report,
             "subtitles_burned": not existing_subtitles,
             "has_existing_subtitles": existing_subtitles,
             "raw_has_existing_subtitles": bool(
                 source_report.get("raw_has_existing_subtitles")),
             "clean_filter_verified": bool(
                 source_report.get("clean_filter_verified")),
             "vertical": vertical,
             "asr_model": "faster-whisper large-v3",
             "llm": MODELS[0], "generated_at": datetime.now().isoformat(timespec="seconds"),
            } for i, m in enumerate(metas)
        ]
        (out / "meta.json").write_text(json.dumps(final_meta, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")

    n = len(metas)
    print(f"\n✅ 出片完成: {n} 条")
    for i, m in enumerate(metas):
        print(f"   [{i+1}] {m['title']}  ({m['duration_sec']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
