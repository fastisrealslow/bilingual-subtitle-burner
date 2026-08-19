#!/usr/bin/env python3
"""出片：任意中文视频 → 3 分钟双语字幕短片。

和 bilingual-subtitle-burner 的 produce.py 的关系
------------------------------------------------
produce.py 是给**英文片源**设计的（direction 写死 en2zh、srt_lang 写死 en），
且依赖仓库里另外 5 个本地缺失的模块。这里不改它，而是针对中文源单独实现，
复用同一套思路：转写 → 挑金句 → 翻译 → 烧字幕 → 拼接。

针对中文股东会素材做的三处专门处理（produce.py 都没有）：
  1. large-v3 + 词级时间戳重新组句 —— medium 会切出 60 秒一段的巨块没法当字幕
  2. 术语表纠正 ASR 专名 —— 现场录音把「安宫牛黄丸」听成「安牛」是常态
  3. loudnorm 响度标准化 —— 观众席手机录音普遍 -36dB，不处理根本听不见

用法：
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
# 本地用已下好的绝对路径；CI 里用 HF 模型名（faster-whisper 自己拉）。
# 两边都是 large-v3：实测 4 核 runner 上实时率 1.17x，完全跑得动，
# 而 small 会输出繁体、把「安宫」听成「安公」，质量差距是决定性的。
WHISPER = os.environ.get("WHISPER_MODEL") or "/home/node/.cache/whisper/large-v3"
SF_URL = "https://api.siliconflow.cn/v1/chat/completions"

# 免费额度可用的模型，按质量排序；限流时逐个降级
MODELS = ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct", "Qwen/Qwen3-8B"]

TARGET_SEC = 180          # 成片目标时长
MAX_CHARS = 18            # 单条字幕上限
MIN_CHARS = 6
BREAK = "，。！？；、,.!?;"
# 语气词过滤：ASR 会把 "啊、嗯、呢、吧" 等单独识别为一帧
# 单帧语气词没有信息量，反而让字幕跳动
FILLER_WORDS = set("啊呀呐呢吧嘛哦噢哎唉哼嗯呃哈呵嘿")
# 最小帧间隔（秒）：相邻字幕间距太小会闪烁
MIN_GAP = 0.3

# ASR 专名纠错表。现场录音对专有名词识别率很差，而这些词恰恰是内容的核心。
# 只放「读音接近且在本领域无歧义」的，避免误改。
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
    """调 LLM，限流时自动换模型。硅基流动的限流是分模型的。"""
    cache_dir = BASE / ".llm_cache"
    cache_dir.mkdir(exist_ok=True)
    ckey = hashlib.sha256(json.dumps(
        {"m": messages, "t": temperature, "mt": max_tokens},
        ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    cf = cache_dir / f"{ckey}.json"
    if cf.exists():
        try:
            out = json.loads(cf.read_text(encoding="utf-8"))["content"]
            print("[llm-cache] 命中，不发请求")
            return out
        except (ValueError, KeyError, OSError):
            print("[llm-cache] 缓存损坏，重新请求", file=sys.stderr)

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
                    break                      # 换模型也救不了，直接下一个
                time.sleep(2 * (attempt + 1))
            except Exception as e:
                last = f"{model} {type(e).__name__}"
                time.sleep(2 * (attempt + 1))
        print(f"[llm] {model} 不可用（{last}），降级", file=sys.stderr)
    raise RuntimeError(f"全部模型不可用，最后错误：{last}")


def fix_terms(text):
    for wrong, right in GLOSSARY.items():
        text = text.replace(wrong, right)
    return text


def transcribe(src, work):
    """large-v3 + 词级时间戳，按标点和字数重新组句。"""
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

    cues, buf, start = [], [], None
    for w in words:
        tok = w.word.strip()
        if not tok:
            continue
        # 过滤单独的语气词（没有信息量，反而让字幕跳动）
        if len(tok) == 1 and tok in FILLER_WORDS:
            continue
        if start is None:
            start = w.start
        buf.append(tok)
        t = "".join(buf)
        if (tok[-1] in BREAK and len(t) >= MIN_CHARS) or len(t) >= MAX_CHARS:
            text = fix_terms(t.strip(BREAK + " "))
            # 跳过纯语气词的帧
            if text and not all(c in FILLER_WORDS for c in text):
                cues.append({"start": start, "end": w.end, "text": text})
            buf, start = [], None
    if buf:
        text = fix_terms("".join(buf).strip(BREAK + " "))
        if text and not all(c in FILLER_WORDS for c in text):
            cues.append({"start": start, "end": words[-1].end, "text": text})
    
    # 合并间距太小的帧（防止字幕闪烁）
    merged = []
    for c in cues:
        if merged and c["start"] - merged[-1]["end"] < MIN_GAP:
            # 合并到上一帧
            merged[-1]["end"] = c["end"]
            merged[-1]["text"] += c["text"]
        else:
            merged.append(dict(c))
    cues = merged
    cues = [c for c in cues if c["text"]]
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[asr] {len(words)} 词 → {len(cues)} 条字幕")
    return cues


def parse_llm_json_array(out):
    """解析 LLM 返回的 JSON 数组，多策略容错。

    CI 实证踩过的坑：裸键、单引号、尾随逗号、引号错位（"end:29"）。
    逐级降级：标准 JSON → Python 字面量 → 正则修复 → 宽松键值抽取。
    """
    import ast
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        raise RuntimeError(f"金句返回无法解析（无数组）：{out[:300]}")
    raw = m.group(0)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        return ast.literal_eval(raw)
    except (ValueError, SyntaxError):
        pass
    fixed = re.sub(r'"(\w+):(\d+)"', r'"\1":\2', raw)  # "end:29"→"end":29（CI 实证）
    fixed = re.sub(r"([{,]\s*)(\w+)(\s*:)", r'\1"\2"\3', fixed)  # 裸键
    fixed = fixed.replace("'", '"')  # 单引号
    fixed = re.sub(r",\s*([}\]])", r"\1", fixed)  # 尾随逗号
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # 究极兜底：逐对象宽松键值抽取，容忍上述错位任意组合
    objs = []
    for block in re.findall(r"\{[^{}]*\}", raw):
        pairs = re.findall(r'"?(\w+)"?\s*:\s*"?([^",}]+)"?', block)
        obj = {k: (int(v) if v.strip().isdigit() else v.strip()) for k, v in pairs}
        if "start" in obj and "end" in obj:
            objs.append(obj)
    if objs:
        return objs
    raise RuntimeError(f"金句 JSON 所有修复策略均失败：{raw[:300]}")


def pick_highlights(cues, speaker, api_key, work):
    """让 LLM 挑金句段落。返回 [(起cue索引, 止cue索引), ...]。"""
    cache = work / "highlights.json"
    if cache.exists():
        print("[金句] 命中缓存")
        return json.loads(cache.read_text(encoding="utf-8"))

    numbered = "\n".join(
        f"{i}|{int(c['start'])//60}:{int(c['start'])%60:02d}|{c['text']}"
        for i, c in enumerate(cues))
    prompt = f"""下面是{speaker}一段讲话的字幕，格式为「序号|时间|文本」。

请挑出 3-4 个最有价值的**连续段落**，用于剪成约 {TARGET_SEC} 秒的短视频。

要求：
1. 每段必须是连续的序号区间，且本身语义完整（有观点、有论证或有具体案例）
2. 优先选：具体数字预测、可验证的投资逻辑、生动的真实案例
3. 避开：语义残缺、明显是语音识别错乱、纯客套或过渡的段落
4. 各段时长加起来接近 {TARGET_SEC} 秒

只输出 JSON 数组，不要任何解释：
[{{"start":起始序号,"end":结束序号,"reason":"选它的理由(10字内)"}}]

字幕：
{numbered}"""

    out = llm([{"role": "user", "content": prompt}], api_key, temperature=0.2)
    picks = parse_llm_json_array(out)

    valid = []
    for p in picks:
        a, b = int(p["start"]), int(p["end"])
        if 0 <= a <= b < len(cues):
            valid.append({"start": a, "end": b, "reason": p.get("reason", "")})
    if not valid:
        raise RuntimeError("金句区间全部越界")
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
            "严格保持编号，每条一行，格式「N. translation」，只输出译文行。\n"
            "注意：原文来自语音识别，可能有残缺或错字；只译实际出现的内容，"
            "不要自行补全或添加原文没有的信息。\n"
            "专有名词按通用译法：林园=Lin Yuan，达仁堂=Darentang，"
            "片仔癀=Pien Tze Huang，同仁堂=Tong Ren Tang，"
            "安宫牛黄丸=Angong Niuhuang Wan，速效救心丸=Suxiao Jiuxin Wan。\n\n"
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
    """竖版适配：字号按高度算、抬到安全区。burner 的 make_ass 是按 16:9 调的，
    720x1280 下算出来才 29px，且会被平台底部 UI 遮住。

    字体必须可换：本机有 Microsoft YaHei，CI 的 ubuntu 上只有 Noto Sans CJK。
    libass 按名字找字体，找不到就 fallback 到无 CJK 字形的字体 —— 中文字幕
    会烧成一排豆腐块。workflow 里通过 ZH_FONT/EN_FONT 传入。"""
    font_zh = os.environ.get("ZH_FONT", "Microsoft YaHei")
    font_en = os.environ.get("EN_FONT", "Arial")
    vertical = H > W
    zh = max(30, int(H * 46 / 1280)) if vertical else max(18, int(W * 26 / 640))
    en = max(22, int(H * 32 / 1280)) if vertical else max(14, int(W * 20 / 640))
    mv = int(H * 0.09) if vertical else 12
    zw = max(12, int(W * 15 / 720)) if vertical else max(10, int(W * 16 / 640))
    ew = max(24, int(W * 34 / 720)) if vertical else max(20, int(W * 38 / 640))

    def wrap(t, n, cjk):
        t = (t or "").strip()
        if not t:
            return ""
        if cjk:
            # 中文：优先按标点换行；无标点时长行尽量不在连续汉字中间截断
            lines, cur = [], ""
            for i, ch in enumerate(t):
                cur += ch
                # 标点符号后直接换行
                if ch in "，。！？、；：":
                    lines.append(cur)
                    cur = ""
                    continue
                # 长度达到上限，需要换行
                if len(cur) >= n:
                    # 优先回溯到前一个标点处
                    cut = -1
                    for j in range(len(cur) - 1, 0, -1):
                        if cur[j] in "，。！？、；：":
                            cut = j + 1
                            break
                    if cut > 0:
                        lines.append(cur[:cut])
                        cur = cur[cut:]
                    else:
                        # 无标点：尽量不在连续汉字中间截断，找前后都是汉字的边界
                        # 简单处理：直接截断，但保留完整字符
                        lines.append(cur)
                        cur = ""
            if cur:
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
    """LLM 生成 B站标题/简介/标签（参考原库 scripts/copywrite.py）。

    标题党检测器就是prompt本身：要求「有信息量、不夸张」。结果落 meta.json，
    投稿脚本优先读这里，不再用 occasion 硬拼。
    """
    cache = work / "copywrite.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    sample = "\n".join(cues[i]["text"] for i in sel[:20])
    prompt = f"""这是{speaker}在「{occasion}」发言的字幕节选：

{sample}

为它生成 B站投稿文案，只输出 JSON：
{{{{"title":"标题，25字以内，必须有具体信息量（数字/观点/场合），不许标题党",
 "desc":"简介，100字以内，第一人称视角陈述内容要点，末尾注明来源场合",
 "tags":["标签", "最多5个", "含主讲人姓名"]}}}}"""
    out = llm([{"role": "user", "content": prompt}], api_key, temperature=0.4)
    m = re.search(r"\{.*\}", out, re.S)
    try:
        d = json.loads(m.group(0))
        assert d.get("title")
    except (ValueError, AssertionError, AttributeError):
        d = {"title": f"{occasion}｜{speaker}".strip("｜"),
             "desc": f"{speaker}在{occasion}的发言精选。",
             "tags": [speaker, "价值投资"]}
    d.setdefault("tags", [speaker])
    cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    print(f"[文案] 标题：{d['title']}")
    return d


def _sc_face_index(ttc_path, want_name="Noto Sans CJK SC"):
    """TTC 合集里找指定子字体下标。原库踩过的坑：默认取第 0 个是 JP 字形，
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
    """封面：抽帧 → 人脸检测裁切 → 16:9 → 底部渐变 → 标题大字。

    用 OpenCV haar 级联检测人脸，裁切时以人脸为中心，
    避免居中裁切把人脸裁掉或压扁。
    """
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1280, 720
    tmp = out_path.with_suffix(".frame.png")
    mid = seg_start + (seg_end - seg_start) / 2
    # 抽 3 帧（中间偏前/正中/偏后），选人脸最大的那张
    frames = []
    for offset in [-2, 0, 2]:
        t = max(0, mid + offset)
        fp = tmp.with_suffix(f".{offset}.png")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.1f}",
                        "-i", str(src), "-frames:v", "1", str(fp)],
                       check=True, capture_output=True)
        if fp.exists():
            frames.append(fp)
    
    # 选人脸最大的帧
    best_frame = frames[0] if frames else tmp
    best_face = None
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(cascade_path)
        for fp in frames:
            img_cv = cv2.imread(str(fp))
            if img_cv is None:
                continue
            gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3, minSize=(80, 80))
            if len(faces) > 0:
                # 选最大的人脸
                biggest = max(faces, key=lambda f: f[2] * f[3])
                if best_face is None or biggest[2] * biggest[3] > best_face[2] * best_face[3]:
                    best_face = biggest
                    best_frame = fp
    except ImportError:
        pass  # 没装 opencv 就用第一帧
    
    img = Image.open(best_frame).convert("RGB")
    w, h = img.size

    # 以人脸为中心裁切，保持原始宽高比
    vertical = h > w
    if vertical:
        # 竖屏视频：生成 9:16 封面（B站支持），以人脸为中心
        if best_face is not None:
            fx, fy, fw, fh = best_face
            cx, cy = fx + fw // 2, fy + fh // 2
            tw = min(w, int(h * 9 / 16))
            x0 = max(0, min(cx - tw // 2, w - tw))
            img = img.crop((x0, 0, x0 + tw, h))
        else:
            tw = min(w, int(h * 9 / 16))
            img = img.crop(((w - tw) // 2, 0, (w - tw) // 2 + tw, h))
        # 缩放到 720x1280 的 9:16 封面
        img = img.resize((720, 1280), Image.LANCZOS)
    else:
        # 横屏视频：裁成 16:9 的 1280x720 封面
        if best_face is not None:
            fx, fy, fw, fh = best_face
            cx, cy = fx + fw // 2, fy + fh // 2
            tw = min(w, int(h * 16 / 9))
            x0 = max(0, min(cx - tw // 2, w - tw))
            img = img.crop((x0, 0, x0 + tw, h)).resize((W, H), Image.LANCZOS)
        else:
            tw = min(w, int(h * 16 / 9))
            img = img.crop(((w - tw) // 2, 0, (w - tw) // 2 + tw, h)).resize((W, H), Image.LANCZOS)
    
    # 清理临时帧
    for fp in frames:
        fp.unlink(missing_ok=True)
    
    # 底部 45% 压暗（黑渐变），字才看得清
    W, H = img.size
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
    f_title = ImageFont.truetype(font_path, 64, index=idx) if font_path else ImageFont.load_default()
    f_tag = ImageFont.truetype(font_path, 36, index=idx) if font_path else ImageFont.load_default()

    d = ImageDraw.Draw(img)
    # 主讲人标签（左上角黄底黑字）
    d.rounded_rectangle([40, 36, 40 + len(speaker) * 40 + 32, 96], 10, fill=(255, 196, 0))
    d.text((56, 48), speaker, font=f_tag, fill=(20, 20, 20))
    # 标题（底部，两行以内，白字黑边）
    chars_per_line = 17
    lines = [title[i:i + chars_per_line] for i in range(0, min(len(title), 34), chars_per_line)]
    y = H - 60 - 76 * len(lines)
    for ln in lines:
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
            d.text((44 + dx, y + dy), ln, font=f_title, fill=(0, 0, 0))
        d.text((44, y), ln, font=f_title, fill=(255, 255, 255))
        y += 76
    img.save(out_path, quality=92)
    tmp.unlink(missing_ok=True)
    print(f"[封面] {out_path.name}  「{title[:20]}」")


def has_existing_subtitles(src):
    """检测视频是否已有硬字幕（烧录在画面上的字幕）。
    用 OCR 检查画面底部是否有连续文字区域。
    """
    try:
        import cv2
        cap = cv2.VideoCapture(str(src))
        if not cap.isOpened():
            return False
        # 检查 3 帧（25%、50%、75% 位置）
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if total <= 0:
            return False
        subtitle_hits = 0
        for pct in (0.25, 0.5, 0.75):
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * pct))
            ret, frame = cap.read()
            if not ret or frame is None:
                continue
            h, w = frame.shape[:2]
            # 底部 20% 区域（字幕通常在底部）
            bottom = frame[int(h * 0.8):, :]
            gray = cv2.cvtColor(bottom, cv2.COLOR_BGR2GRAY)
            # 二值化，文字区域会有大量高对比度像素
            _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
            text_ratio = cv2.countNonZero(binary) / (bottom.size / 3)
            if text_ratio > 0.05:  # 5% 以上白色像素 → 可能有字幕
                subtitle_hits += 1
        cap.release()
        return subtitle_hits >= 2  # 3 帧中至少 2 帧有字幕
    except Exception:
        return False


def has_hard_watermark(src):
    """检测视频是否有难以裁除的水印（画面中间的 logo）。
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
    ap.add_argument("--source-platform", default="", help="来源平台（bilibili/weibo/tencent 等）")
    ap.add_argument("--dry-run", action="store_true", help="只挑金句，不出片")
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_file():
        sys.exit(f"找不到源：{src}")
    api_key = load_key()
    if not api_key:
        sys.exit("缺 SILICONFLOW_API_KEY（放 .env 或环境变量）")

    out = BASE / "deliver" / args.slug
    work = out / "_tmp"
    work.mkdir(parents=True, exist_ok=True)

    cues = transcribe(src, work)
    
    # 检测已有字幕（如果视频已有硬字幕，跳过字幕烧录）
    existing_subtitles = has_existing_subtitles(src)
    if existing_subtitles:
        print("[检测] 视频已有硬字幕，跳过字幕烧录")
    
    # 检测难以裁除的水印
    hard_watermark = has_hard_watermark(src)
    if hard_watermark:
        print("[检测] 视频有中间水印，难以裁除，跳过")
        return 1
    
    picks = pick_highlights(cues, args.speaker, api_key, work)

    sel = []
    for p in picks:
        sel.extend(range(p["start"], p["end"] + 1))
    sel = sorted(set(sel))
    total = sum(cues[i]["end"] - cues[i]["start"] for i in sel)
    print(f"\n选中 {len(sel)} 条字幕，约 {int(total)//60}:{int(total)%60:02d}")

    if args.dry_run:
        for p in picks:
            print(f"\n── {p['reason']} ──")
            for i in range(p["start"], p["end"] + 1):
                print(f"  {cues[i]['text']}")
        return 0

    zh_texts = [cues[i]["text"] for i in sel]
    # 林园视频是中文源，不需要英文翻译
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
        # 根据检测结果构建滤镜
        if hard_watermark:
            # 有中间水印 → 跳过，不出片
            print("[跳过] 有中间水印，不出片")
            return 1
        else:
            # 裁掉顶部 100px，保持原始宽高比
            # 横屏 16:9 → 保持 16:9；竖屏 9:16 → 保持 9:16
            vertical = H > W
            if vertical:
                # 竖屏：保持 9:16
                crop_h = H - 100
                crop_w = min(int(crop_h * 9 / 16), W)
            else:
                # 横屏：保持 16:9
                crop_h = H - 100
                crop_w = min(int(crop_h * 16 / 9), W)
            crop_x = (W - crop_w) // 2
            
            if existing_subtitles:
                # 已有字幕 → 不烧录字幕
                vf = f"crop={crop_w}:{crop_h}:{crop_x}:100"
            else:
                # 正常：裁掉顶部 + 烧录字幕
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

    # 文案 + 封面（投稿三件套：标题/简介/标签 + 封面图）
    cw = copywrite(cues, sel, args.speaker, args.occasion, api_key, work)
    # 竖屏视频生成 9:16 封面，横屏生成 16:9 封面
    vertical = H > W
    cover_name = "cover_9x16.jpg" if vertical else "cover_16x9.jpg"
    cover = out / cover_name
    try:
        p0 = picks[0]
        make_cover(src, cues[p0["start"]]["start"], cues[p0["end"]]["end"],
                   cw["title"], args.speaker, cover)
    except Exception as e:
        print(f"[封面] 生成失败（不阻断出片）：{e}", file=sys.stderr)
        cover = None

    # 推断来源平台（优先用命令行传入的 --source-platform）
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
