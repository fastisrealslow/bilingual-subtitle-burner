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
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import time
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

# 免费额度可用的模型,按质量排序;限流时逐个降级
MODELS = ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen3-8B"]

TARGET_SEC = 180          # 成片目标时长（短金句）
TARGET_SEC_MID = 420     # 中视频目标时长（7分钟话题片，2026-08-29 对标竞品中视频）
MAX_CHARS = 18            # 单条字幕上限
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
}


def load_key():
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SILICONFLOW_API_KEY="):
                return line.split("=", 1)[1].strip()
    return (os.environ.get("SILICONFLOW_API_KEY") or "").strip()


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




def transcribe(src, work, api_key=None):
    """转写入口：按 ASR_BACKEND 分发到 Fun-ASR-Nano 或 whisper large-v3。"""
    cache = work / "cues_raw.json"
    if cache.exists():
        print("[asr] 命中缓存")
        return json.loads(cache.read_text(encoding="utf-8"))
    if ASR_BACKEND == "sensevoice":
        return _transcribe_sensevoice(src, work, api_key)
    if ASR_BACKEND == "funasr":
        return _transcribe_funasr(src, work)
    return _transcribe_whisper(src, work, api_key)


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
        if merged and c["start"] - merged[-1]["end"] < MIN_GAP and \
           len(merged[-1]["text"]) + len(c["text"]) <= MAX_CHARS + 2:
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
    # LLM 通顺化：去口水磕巴（付费 DeepSeek-V3 + 相似度防篡改，2026-08-29）
    cues = _llm_smooth_cues(cues, api_key)
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[asr] {len(cues)} 条字幕")
    return cues


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
        if tok in "。！？!?":
            buf_text += tok
            _flush()
        elif tok in "，、；：,;:":
            buf_text += tok
            if len(buf_text) >= MAX_CHARS - 4:
                _flush()
        else:
            buf.append((tok, st, en))
            buf_text += tok
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
        if v:
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
def pick_highlights(cues, speaker, api_key, work, suffix="", target_sec=None):
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

请挑出 2-4 个**最有传播力的金句段落**,剪成约 {target} 秒的视频。素材短(不到 2 分钟)选 2 段即可,宁缺毋滥。

什么样的段落算「有传播力」(按优先级):
1. 开头有「钩子」:第一句就是具体数字(如"8000块做到20亿"、"股价加两个零")、反常识观点、或强烈判断,能一把抓住划手机的人
2. 有具体信息增量:具体的数字预测、可验证的投资逻辑、生动的真实案例(带具体公司/年份/金额)
3. 观点锋利、敢下结论,而不是含糊其辞的套话

严格避开:
- 纯客套、口头禅、口水话("是吧"、"对不对"、"嗯"、"这个这个")
- 语义残缺、明显识别错乱的段落
- 没有信息量的过渡、寒暄、自问自答铺垫

要求:
1. 每段语义完整(有观点、有论证或有具体案例)
2. 各段时长加起来接近 {target} 秒

只输出 JSON 数组,不要任何解释:
[{{"start":起始序号,"end":结束序号,"reason":"选它的理由(10字内)"}}]

字幕:
{numbered}"""

    out = llm([{"role": "user", "content": prompt}], api_key, temperature=0.2)
    picks = parse_llm_json_array(out)

    valid = []
    for p in picks:
        try:
            a, b = int(p["start"]), int(p["end"])
        except (ValueError, KeyError, TypeError):
            continue
        if 0 <= a <= b < len(cues):
            valid.append({"start": a, "end": b, "reason": p.get("reason", "")})
            continue
        # 容错：LLM 误用 1-based 序号（把第一条当序号 1），统一减 1
        if 1 <= a <= b <= len(cues):
            valid.append({"start": a - 1, "end": b - 1, "reason": p.get("reason", "")})
    if not valid:
        # 降级兜底：金句选不出时不废掉整条，取前 target 秒的连续字幕
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
    cache.write_text(json.dumps(valid, ensure_ascii=False, indent=1), encoding="utf-8")
    for v in valid:
        d = cues[v["end"]]["end"] - cues[v["start"]]["start"]
        print(f"[金句] {v['start']}-{v['end']} ({d:.0f}s) {v['reason']}")
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


def make_ass(entries, path, W, H):
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

    L = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
         "", "[V4+ Styles]",
         "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
         "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
         "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
         "MarginL, MarginR, MarginV, Encoding",
         f"Style: EN,{font_en},{en},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
         f"-1,0,0,0,100,100,0,0,1,2,1,2,20,20,{mv + zh*2 + 10},1",
         f"Style: ZH,{font_zh},{zh},&H00FFFFFF,&H000000FF,&H00000000,"
         f"&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,20,20,{mv},1",
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

为它生成 B站投稿文案。标题要有「钩子」——对标短视频平台高播放标题的写法（2026-08-29 竞品分析：同样内容，带钩子标题播放量是平淡标题的 10 倍）。

标题要求（核心）:
1. 25 字以内，必须含「{speaker}」姓名
2. 必须带至少一个「钩子」：反常识观点 / 具体数字 / 冲突 / 带感细节，例如:
   - 反常识：「赚钱是学不了的，连{speaker}儿子也学不会」
   - 具体数字：「8000块做到20亿」
   - 冲突细节：「儿子亏8000万，他说像吃了个死苍蝇」
3. 钩子必须来自字幕真实内容，可突出原意，但严禁编造、严禁夸大数字、严禁说字幕里没有的事
4. 一句话戳人：让刷到的人想点进来，而不是「XX谈XX」的平铺直叙

简介:
1. 100字以内，第一人称视角陈述内容要点，末尾注明来源场合
2. 可以补一句「看点」提示

只输出 JSON:
{{{{"title":"标题","desc":"简介","tags":["标签","最多5个","含主讲人姓名"]}}}}"""
    out = llm([{"role": "user", "content": prompt}], api_key, temperature=0.4)
    m = re.search(r"\{.*\}", out, re.S)
    try:
        d = json.loads(m.group(0))
        assert d.get("title")
    except (ValueError, AssertionError, AttributeError):
        d = {"title": f"{occasion}|{speaker}".strip("|"),
             "desc": f"{speaker}在{occasion}的发言精选。",
             "tags": [speaker, "价值投资"]}
    d.setdefault("tags", [speaker])
    cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"[文案] 标题:{d['title']}")
    return d


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


def make_cover(src, seg_start, seg_end, title, speaker, out_path):
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
    # 抽 5 帧（主讲人持续出镜，多帧投票更准）
    frames = []
    for offset_pct in [-0.30, -0.20, -0.10, 0, 0.10, 0.20, 0.30]:
        t = max(0, mid + offset_pct * (seg_end - seg_start))
        fp = tmp.with_suffix(f".{int(offset_pct*100)}.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.1f}",
                        "-i", str(src), "-frames:v", "1", str(fp)],
                       check=True, capture_output=True)
        if fp.exists():
            frames.append(fp)

    # 多帧人脸聚类，选「跨帧持续出镜」的主讲人（林园），而非单帧「大且居中」的主持人。
    # （2026-08-29 修复：专访里女主持居中脸大，旧评分误选主持人；现统计多帧出现次数）
    best_frame = frames[0] if frames else tmp
    best_face = None
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
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
    except Exception:
        pass  # cv2 不可用/缺级联文件 → 退居中裁切

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
        max_lines = 2
        line_h = int(title_size * 1.2)
        margin_bottom = 56
    # 标题分行：用 jieba 分词按词边界断，避免「公司」被硬切成「公」+「司」
    # （2026-08-27 实拍封面断句问题）。jieba 失败则退回均匀字符切分。
    if len(title) <= chars_per_line:
        lines = [title]
    else:
        try:
            import jieba as _jieba
            import logging as _lg
            _jieba.setLogLevel(_lg.ERROR)
            words = list(_jieba.cut(title))
            lines, cur = [], ""
            for w in words:
                if len(cur) + len(w) > chars_per_line and cur:
                    lines.append(cur)
                    cur = w
                else:
                    cur += w
            if cur:
                lines.append(cur)
            lines = lines[:max_lines]
        except ImportError:
            n_lines = min(max_lines, (len(title) + chars_per_line - 1) // chars_per_line)
            per = (len(title) + n_lines - 1) // n_lines
            lines = [title[i:i + per] for i in range(0, len(title), per)][:n_lines]
    y = H - margin_bottom - line_h * len(lines)
    for ln in lines:
        # 白字黑边(描边厚度自适应)
        stroke = 3 if W < 1000 else 2
        d.text((40, y), ln, font=f_title, fill=(255, 255, 255),
               stroke_width=stroke, stroke_fill=(0, 0, 0))
        y += line_h
    img.save(out_path, quality=92)
    print(f"[封面] {out_path.name} {W}x{H} 「{title[:20]}」")


def has_existing_subtitles(src):
    """检测视频是否已有硬字幕(烧录在画面上的字幕)。

    2026-08-21 修复:旧版亮度阈值法把「画面偏亮」误判成「有字幕」
    (白色衣服/亮背景即可触发),导致无字幕视频跳过烧录,成片裸奔。

    新版检测字幕的结构特征(同时满足才算字幕帧):
    1. 底部存在横向窄条带(高度 3%~15% 屏高)
    2. 条带内白色(高亮)像素 ≥ 20%(文字覆盖)
    3. 条带上下边界与背景有明显对比(不是整片亮背景)
    """
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
        # 至少 5 帧里 2 帧有明确文字条带（字幕可能断断续续，放宽帧数要求）
        return checked >= 2 and subtitle_hits >= 2
    except Exception:
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
    except Exception as e:
        print(f"[去重] LLM 不可用，跳过: {e}")
        return chunks
    nums = parse_llm_json_array(out)
    keep0 = sorted({n - 1 for n in nums if isinstance(n, int) and 1 <= n <= len(chunks)})
    if len(keep0) < 2:
        return chunks  # 结果异常（只剩 0/1 段）→ 保守全保留
    cache.write_text(json.dumps(keep0, ensure_ascii=False), encoding="utf-8")
    print(f"[去重] {len(chunks)} 段 → 保留 {len(keep0)} 段（LLM 观点去重）")
    return [chunks[i] for i in keep0]


def _produce_one(src, work, out, cues, speaker, occasion, api_key,
                 existing_subtitles, W, H, suffix, pick_cache_suffix="", target_sec=None):
    """出一段视频。suffix='' 或 '_2' 等。target_sec 控制时长（短金句 180 / 中视频 420）。返回 meta dict。"""
    picks = pick_highlights(cues, speaker, api_key, work, pick_cache_suffix, target_sec)
    sel = sorted({i for p in picks for i in range(p["start"], p["end"] + 1)})
    total_sel = sum(cues[i]["end"] - cues[i]["start"] for i in sel)
    print(f"[段{suffix or '1'}] 选 {len(sel)} 条字幕,约 {int(total_sel)//60}:{int(total_sel)%60:02d}")

    en_map = {}
    parts = []
    for n, p in enumerate(picks, 1):
        idx = list(range(p["start"], p["end"] + 1))
        s0, s1 = cues[idx[0]]["start"], cues[idx[-1]]["end"]
        entries = [{"start_sec": cues[i]["start"] - s0,
                    "end_sec": cues[i]["end"] - s0,
                    "zh": cues[i]["text"], "en": en_map.get(i, "")} for i in idx]
        ass = work / f"seg{suffix}{n}.ass"
        make_ass(entries, ass, W, H)
        seg = work / f"seg{suffix}{n}.mp4"
        vertical = H > W
        if vertical:
            crop_h = H - 100
            crop_w = min(int(crop_h * 9 / 16), W)
        else:
            crop_h = H - 100
            crop_w = min(int(crop_h * 16 / 9), W)
        crop_x = (W - crop_w) // 2
        seg_dur = s1 - s0
        # 片头片尾淡入淡出 0.4s：修「开头结束断帧」的视觉突兀（2026-08-27）
        fade = f"fade=t=in:st=0:d=0.4,fade=t=out:st={max(0, seg_dur - 0.4):.2f}:d=0.4"
        if existing_subtitles:
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:100,{fade}"
        else:
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:100,ass={ass},{fade}"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s0),
             "-t", str(seg_dur), "-i", str(src),
             "-vf", vf,
             "-af", "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11",
             "-c:v", "libx264", "-preset", "slow", "-crf", "18",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-r", "30",
             str(seg)], check=True)
        parts.append(seg)

    lst = work / f"concat{suffix}.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    final = out / f"final{suffix}.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", str(lst), "-c", "copy", str(final)],
                   check=True)
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(final)],
        capture_output=True, text=True).stdout.strip() or 0)

    cw = copywrite(cues, sel, speaker, occasion, api_key, work, pick_cache_suffix)
    cover_name = "cover_16x9.jpg"
    cover = out / (f"cover{suffix}.jpg" if suffix else cover_name)
    try:
        p0 = picks[0]
        make_cover(src, cues[p0["start"]]["start"], cues[p0["end"]]["end"],
                   cw["title"], speaker, cover)
    except Exception as e:
        print(f"[封面] 生成失败(不阻断出片):{e}", file=sys.stderr)
        cover = None
    return {
        "title": cw["title"], "desc": cw["desc"], "tags": cw["tags"],
        "cover": cover.name if cover else None,
        "duration_sec": round(dur, 1),
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

    cues = transcribe(src, work, api_key)

    existing_subtitles = has_existing_subtitles(src)
    if existing_subtitles:
        print("[检测] 视频已有硬字幕,跳过字幕烧录")

    size = probe(src, "stream=width,height")
    W, H = (int(x) for x in size.split("x"))
    vertical = H > W

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
        m = _produce_one(src, work, out, seg_cues, args.speaker, args.occasion,
                         api_key, existing_subtitles, W, H, suffix,
                         pick_cache_suffix=suffix, target_sec=target_sec)
        metas.append(m)

    # 写 meta.json：单条保持兼容，多条记录列表
    if len(metas) == 1:
        final_meta = {
            "slug": args.slug, "source": str(src), "speaker": args.speaker,
            "occasion": args.occasion, **metas[0],
            "source_platform": platform,
            "watermark_cropped": True,
            "subtitles_burned": not existing_subtitles,
            "has_existing_subtitles": existing_subtitles,
            "vertical": vertical,
            "cue_count": sum(1 for _ in cues),
            "asr_model": "faster-whisper large-v3",
            "llm": MODELS[0], "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        (out / "meta.json").write_text(json.dumps(final_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        # 多条：meta.json 是列表，每个元素含 final 文件名
        final_meta = [
            {"slug": args.slug, "source": str(src), "speaker": args.speaker,
             "occasion": args.occasion, "part": i + 1,
             "final": f"final_{i + 1}.mp4", **m,
             "source_platform": platform,
             "watermark_cropped": True,
             "subtitles_burned": not existing_subtitles,
             "has_existing_subtitles": existing_subtitles,
             "vertical": vertical,
             "asr_model": "faster-whisper large-v3",
             "llm": MODELS[0], "generated_at": datetime.now().isoformat(timespec="seconds"),
            } for i, m in enumerate(metas)
        ]
        (out / "meta.json").write_text(json.dumps(final_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(metas)
    print(f"\n✅ 出片完成: {n} 条")
    for i, m in enumerate(metas):
        print(f"   [{i+1}] {m['title']}  ({m['duration_sec']}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
