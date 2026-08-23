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
SF_URL = "https://api.siliconflow.cn/v1/chat/completions"

# 免费额度可用的模型,按质量排序;限流时逐个降级
MODELS = ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen3-8B"]

TARGET_SEC = 180          # 成片目标时长
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


def transcribe(src, work):
    """large-v3 + 词级时间戳,按标点和字数重新组句。"""
    from faster_whisper import WhisperModel
    cache = work / "cues_raw.json"
    if cache.exists():
        print("[asr] 命中缓存")
        return json.loads(cache.read_text(encoding="utf-8"))

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

    cues = _group_tokens_to_cues(words)

    # 合并间距太小的帧(防止字幕闪烁);拼接时补分隔符避免文字粘连。
    # 关键约束：合并后长度不超过 MAX_CHARS+2，否则连续说话时硬切的长段
    # 会被合并回超长条，导致「一页十几行字幕」(2026-08-23 事故根因)。
    merged = []
    for c in cues:
        if merged and c["start"] - merged[-1]["end"] < MIN_GAP and \
           len(merged[-1]["text"]) + len(c["text"]) <= MAX_CHARS + 2:
            merged[-1]["end"] = c["end"]
            sep = "" if (not merged[-1]["text"] or merged[-1]["text"][-1] in BREAK) else ","
            merged[-1]["text"] += sep + c["text"]
        else:
            merged.append(dict(c))
    cues = merged
    cues = [c for c in cues if c["text"]]
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[asr] {len(words)} 词 → {len(cues)} 条字幕")
    return cues


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

    CI 实证踩过的坑:裸键、单引号、尾随逗号、引号错位("end:29")。
    逐级降级:标准 JSON → Python 字面量 → 正则修复 → 宽松键值抽取。
    """
    import ast
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        raise RuntimeError(f"金句返回无法解析(无数组):{out[:300]}")
    raw = m.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        pass
    fixed = re.sub(r'"(\w+):(\d+)"', r'"\1":\2', raw)  # "end:29"→"end":29(CI 实证)
    fixed = re.sub(r"([{,]\s*)(\w+)(\s*:)", r'\1"\2"\3', fixed)  # 裸键
    fixed = fixed.replace("'", '"')  # 单引号
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)  # 尾随逗号
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # 究极兜底:逐对象宽松键值抽取,容忍上述错位任意组合
    objs = []
    for block in re.findall(r"\{[^{}]*\}", raw):
        pairs = re.findall(r'"?(\w+)"?\s*:\s*"?([^",}]+)"?', block)
        obj = {k: (int(v) if v.strip().isdigit() else v.strip()) for k, v in pairs}
        if "start" in obj and "end" in obj:
            objs.append(obj)
    if objs:
        return objs
    raise RuntimeError(f"金句 JSON 所有修复策略均失败:{raw[:300]}")


def pick_highlights(cues, speaker, api_key, work):
    """让 LLM 挑金句段落。返回 [(起cue索引, 止cue索引), ...]。"""
    cache = work / "highlights.json"
    if cache.exists():
        print("[金句] 命中缓存")
        return json.loads(cache.read_text(encoding="utf-8"))

    numbered = "\n".join(
        f"{i}|{int(c['start'])//60}:{int(c['start'])%60:02d}|{c['text']}"
        for i, c in enumerate(cues))
    prompt = f"""下面是{speaker}一段讲话的字幕,格式为「序号|时间|文本」(注意:序号从 0 开始计数)。

请挑出 2-4 个最有价值的**连续段落**,用于剪成约 {TARGET_SEC} 秒的短视频。若素材本身较短(不到 2 分钟),选 2 段即可,宁缺毋滥。

要求:
1. 每段必须是连续的序号区间,且本身语义完整(有观点、有论证或有具体案例)
2. 优先选:具体数字预测、可验证的投资逻辑、生动的真实案例
3. 避开:语义残缺、明显是语音识别错乱、纯客套或过渡的段落
4. 各段时长加起来接近 {TARGET_SEC} 秒

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
        # 降级兜底：金句选不出时不废掉整条，取前 TARGET_SEC 秒的连续字幕
        end_idx = 0
        total = 0.0
        for i, c in enumerate(cues):
            total += c["end"] - c["start"]
            if total >= TARGET_SEC:
                end_idx = i
                break
        else:
            end_idx = len(cues) - 1
        valid = [{"start": 0, "end": max(1, end_idx), "reason": "降级取前段"}]
        print(f"[金句] LLM 未返回有效区间，降级取前段(至第 {end_idx} 条)")
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
        # 竖屏：按高度比例 + 抬高避开底部 UI（现状保持，用户未反馈竖屏问题）
        zh = max(30, int(H * 46 / 1280))
        en = max(22, int(H * 32 / 1280))
        mv = int(H * 0.09)
        zw = max(12, int(W * 15 / 720))
        ew = max(24, int(W * 34 / 720))
    else:
        # 横屏：字号占高 5%、底边距占高 6.5%、行宽按字号自洽。
        # 旧版按宽度 W*26/640 算 → 占高 7.2% 偏大，且 MarginV 固定 12px 贴底。
        zh = max(28, int(H * 0.05))
        en = max(20, int(H * 0.04))
        mv = int(H * 0.065)
        zw = max(12, int(W * 0.85 / zh))
        ew = max(22, int(W * 0.85 / en))

    def wrap(t, n, cjk):
        t = (t or "").strip()
        if not t:
            return ""
        if cjk:
            # 按宽度断行:每行尽量接近 n 字,标点是「优先断点」而非「必断点」。
            # 达到 n 字时优先回溯到最近的标点处断(标点留在上一行尾),
            # 无标点则硬断。这样一句最多 2-3 行,不会每遇逗号就断开。
            lines, cur = [], ""
            for ch in t:
                cur += ch
                if len(cur) >= n:
                    cut = -1
                    # 在 [n-8, n] 范围内找最后一个标点作为断点
                    for j in range(len(cur) - 1, max(0, len(cur) - 9), -1):
                        if cur[j] in "，。！？；：、":
                            cut = j + 1
                            break
                    if cut <= 0:
                        cut = len(cur)  # 无标点,整行断
                    lines.append(cur[:cut])
                    cur = cur[cut:]
            if cur or not lines:
                lines.append(cur)
            return "\\N".join(lines)
        words, lines, cur = t.split(), [], ""
        for w in words:
            if len(cur) + len(w) + 1 > n and cur:
                lines.append(cur); cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            lines.append(cur)
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


def copywrite(cues, sel, speaker, occasion, api_key, work):
    """LLM 生成 B站标题/简介/标签(参考原库 scripts/copywrite.py)。

    标题党检测器就是prompt本身:要求「有信息量、不夸张」。结果落 meta.json,
    投稿脚本优先读这里,不再用 occasion 硬拼。
    """
    cache = work / "copywrite.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    sample = "\n".join(cues[i]["text"] for i in sel[:20])
    prompt = f"""这是{speaker}在「{occasion}」发言的字幕节选:

{sample}

为它生成 B站投稿文案,只输出 JSON:
{{{{"title":"标题,25字以内,必须有具体信息量(数字/观点/场合),不许标题党",
 "desc":"简介,100字以内,第一人称视角陈述内容要点,末尾注明来源场合",
 "tags":["标签", "最多5个", "含主讲人姓名"]}}}}"""
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
    # 抽 3 帧(段落前1/4、正中、偏后),偏前帧字幕较少
    frames = []
    for offset_pct in [-0.25, 0, 0.15]:
        t = max(0, mid + offset_pct * (seg_end - seg_start))
        fp = tmp.with_suffix(f".{int(offset_pct*100)}.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.1f}",
                        "-i", str(src), "-frames:v", "1", str(fp)],
                       check=True, capture_output=True)
        if fp.exists():
            frames.append(fp)

    # 选人脸最大的帧(小帧先放大再检测,360p 源 haar 直接检不出)
    best_frame = frames[0] if frames else tmp
    best_face = None
    try:
        import cv2
        import numpy as np
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        for fp in frames:
            img_cv = cv2.imread(str(fp))
            if img_cv is None:
                continue
            fh, fw = img_cv.shape[:2]
            scale = 2.0 if max(fh, fw) < 720 else 1.0  # 低清帧放大 2 倍再检测
            if scale > 1.0:
                img_cv = cv2.resize(img_cv, (fw*2, fh*2), interpolation=cv2.INTER_CUBIC)
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3,
                                             minSize=(120, 120) if scale > 1.0 else (80, 80))
            if len(faces) > 0:
                biggest = max(faces, key=lambda f: f[2] * f[3])
                if best_face is None or biggest[2] * biggest[3] > best_face[2] * best_face[3]:
                    # 人脸坐标除回 scale,映射到原图坐标系
                    best_face = (int(biggest[0]/scale), int(biggest[1]/scale),
                                 int(biggest[2]/scale), int(biggest[3]/scale))
                    best_frame = fp
    except Exception:
        pass  # cv2 不可用/缺级联文件(如 opencv-headless 无 CascadeClassifier)→ 退居中裁切

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
        # 竖屏主体缩放到高度 720 居中
        fg_h = 720
        fg_w = max(1, int(img.width * fg_h / img.height))
        fg = img.resize((fg_w, fg_h), Image.LANCZOS)
        canvas = bg.copy()
        canvas.paste(fg, ((1280 - fg_w) // 2, 0))
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
    # 字号按画布宽自适应(竖屏 720 → 标题 44px,横屏 1280 → 64px)
    title_size = 44 if W < 1000 else 64
    tag_size = 30 if W < 1000 else 36
    f_title = ImageFont.truetype(font_path, title_size, index=idx) if font_path else ImageFont.load_default()
    f_tag = ImageFont.truetype(font_path, tag_size, index=idx) if font_path else ImageFont.load_default()

    d = ImageDraw.Draw(img)
    # 主讲人标签(左上角黄底黑字),尺寸自适应
    tag_w = int(len(speaker) * tag_size * 1.15) + 28
    d.rounded_rectangle([36, 32, 36 + tag_w, 32 + int(tag_size * 1.7)], 8, fill=(255, 196, 0))
    d.text((50, 40), speaker, font=f_tag, fill=(20, 20, 20))
    # 标题:行宽按画布自适应,行高按字号
    # 竖屏每行约 W/字号*0.95 字,横屏约 17 字;最多 3 行(竖屏窄)
    if W < 1000:
        chars_per_line = max(10, int(W * 0.92 / title_size))
        max_lines = 3
        line_h = int(title_size * 1.25)
        margin_bottom = 48
    else:
        chars_per_line = 17
        max_lines = 2
        line_h = 76
        margin_bottom = 60
    lines = [title[i:i + chars_per_line] for i in range(0, min(len(title), chars_per_line * max_lines), chars_per_line)]
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
        for pct in (0.25, 0.5, 0.75):
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
            bright = (gray > 200).astype(np.uint8)
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
        # 至少 3 帧里 2 帧有明确文字条带
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

    cues = transcribe(src, work)

    # 检测已有字幕(如果视频已有硬字幕,跳过字幕烧录)
    existing_subtitles = has_existing_subtitles(src)
    if existing_subtitles:
        print("[检测] 视频已有硬字幕,跳过字幕烧录")

    # 中间水印不再检测跳过（用户 2026-08-23 决定：带中间水印也照常出片）
    picks = pick_highlights(cues, args.speaker, api_key, work)

    sel = []
    for p in picks:
        sel.extend(range(p["start"], p["end"] + 1))
    sel = sorted(set(sel))
    total = sum(cues[i]["end"] - cues[i]["start"] for i in sel)
    print(f"\n选中 {len(sel)} 条字幕,约 {int(total)//60}:{int(total)%60:02d}")

    if args.dry_run:
        for p in picks:
            print(f"\n── {p['reason']} ──")
            for i in range(p["start"], p["end"] + 1):
                print(f"  {cues[i]['text']}")
        return 0

    zh_texts = [cues[i]["text"] for i in sel]
    # 林园视频是中文源,不需要英文翻译
    # en_map = dict(zip(sel, translate(zh_texts, api_key, work)))
    en_map = {}

    size = probe(src, "stream=width,height")
    W, H = (int(x) for x in size.split("x"))

    parts = []
    for n, p in enumerate(picks, 1):
        idx = list(range(p["start"], p["end"] + 1))
        s0, s1 = cues[idx[0]]["start"], cues[idx[-1]]["end"]
        entries = [{"start_sec": cues[i]["start"] - s0,
                    "end_sec": cues[i]["end"] - s0,
                    "zh": cues[i]["text"], "en": en_map.get(i, "")} for i in idx]
        ass = work / f"seg{n}.ass"
        make_ass(entries, ass, W, H)
        seg = work / f"seg{n}.mp4"
        print(f"[烧录] 段{n} {s0:.0f}s→{s1:.0f}s ({s1-s0:.0f}s)")
        # 裁掉顶部 100px，保持原始宽高比（横屏 16:9 / 竖屏 9:16）
        vertical = H > W
        if vertical:
            crop_h = H - 100
            crop_w = min(int(crop_h * 9 / 16), W)
        else:
            crop_h = H - 100
            crop_w = min(int(crop_h * 16 / 9), W)
        crop_x = (W - crop_w) // 2

        if existing_subtitles:
            # 已有字幕 → 不烧录字幕
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:100"
        else:
            # 正常:裁掉顶部 + 烧录字幕
            vf = f"crop={crop_w}:{crop_h}:{crop_x}:100,ass={ass}"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s0),
             "-t", str(s1 - s0), "-i", str(src),
             "-vf", vf,
             "-af", "highpass=f=80,loudnorm=I=-16:TP=-1.5:LRA=11",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20",
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-r", "30",
             str(seg)], check=True)
        parts.append(seg)

    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    final = out / "final.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat",
                    "-safe", "0", "-i", str(lst), "-c", "copy", str(final)],
                   check=True)

    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(final)],
        capture_output=True, text=True).stdout.strip() or 0)

    # 文案 + 封面(投稿三件套:标题/简介/标签 + 封面图)
    cw = copywrite(cues, sel, args.speaker, args.occasion, api_key, work)
    # 封面统一 16:9（B站封面信息流是横屏，竖屏视频也输出 16:9 横屏封面）
    cover_name = "cover_16x9.jpg"
    cover = out / cover_name
    try:
        p0 = picks[0]
        make_cover(src, cues[p0["start"]]["start"], cues[p0["end"]]["end"],
                   cw["title"], args.speaker, cover)
    except Exception as e:
        print(f"[封面] 生成失败(不阻断出片):{e}", file=sys.stderr)
        cover = None

    # 推断来源平台(优先用命令行传入的 --source-platform)
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

    (out / "meta.json").write_text(json.dumps({
        "slug": args.slug, "source": str(src), "speaker": args.speaker,
        "occasion": args.occasion, "duration_sec": round(dur, 1),
        "title": cw["title"], "desc": cw["desc"], "tags": cw["tags"],
        "cover": cover_name if cover else None,
        "source_platform": platform,
        "watermark_cropped": True,  # 统一裁掉顶部 100px 水印
        "subtitles_burned": not existing_subtitles,  # 有原字幕就不烧录
        "has_existing_subtitles": existing_subtitles,
        "vertical": vertical,
        "segments": [{"start": cues[p["start"]]["start"],
                      "end": cues[p["end"]]["end"], "reason": p["reason"]}
                     for p in picks],
        "cue_count": len(sel), "asr_model": "faster-whisper large-v3",
        "llm": MODELS[0], "generated_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✅ {final}  {int(dur)//60}:{int(dur)%60:02d}  "
          f"{final.stat().st_size/1048576:.1f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
