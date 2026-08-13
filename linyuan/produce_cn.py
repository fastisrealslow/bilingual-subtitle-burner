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
        if start is None:
            start = w.start
        buf.append(tok)
        t = "".join(buf)
        if (tok[-1] in BREAK and len(t) >= MIN_CHARS) or len(t) >= MAX_CHARS:
            cues.append({"start": start, "end": w.end,
                         "text": fix_terms(t.strip(BREAK + " "))})
            buf, start = [], None
    if buf:
        cues.append({"start": start, "end": words[-1].end,
                     "text": fix_terms("".join(buf).strip(BREAK + " "))})
    cues = [c for c in cues if c["text"]]
    cache.write_text(json.dumps(cues, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[asr] {len(words)} 词 → {len(cues)} 条字幕")
    return cues


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
    m = re.search(r"\[.*\]", out, re.S)
    if not m:
        raise RuntimeError(f"金句返回无法解析：{out[:200]}")
    picks = json.loads(m.group(0))

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
            return "\\N".join(t[i:i + n] for i in range(0, len(t), n))
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--speaker", default="林园")
    ap.add_argument("--occasion", default="")
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
    en_texts = translate(zh_texts, api_key, work)
    en_map = dict(zip(sel, en_texts))

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
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", str(s0),
             "-t", str(s1 - s0), "-i", str(src), "-vf", f"ass={ass}",
             # 现场录音普遍 -36dB，不做响度标准化基本听不见
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

    (out / "meta.json").write_text(json.dumps({
        "slug": args.slug, "source": str(src), "speaker": args.speaker,
        "occasion": args.occasion, "duration_sec": round(dur, 1),
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
