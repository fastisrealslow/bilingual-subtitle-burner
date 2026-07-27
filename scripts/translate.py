#!/usr/bin/env python3
"""
translate.py — 通用字幕翻译，支持中→英 / 英→中两个方向。

中文视频（默认，--direction zh2en）：
  SRT 里是中文 → 翻译成英文
  输出双语 JSON：[{"index":1, "start":"...", "end":"...", "zh":"中文原文", "en":"英文译文"}, ...]

英文视频（--direction en2zh）：
  SRT 里是英文 → 翻译成中文
  输出双语 JSON：[{"index":1, "start":"...", "end":"...", "en":"英文原文", "zh":"中文译文"}, ...]

环境变量：
  SILICONFLOW_API_KEY  （必填）
  SILICONFLOW_MODEL    （可选，默认 Qwen/Qwen3-8B）
  SILICONFLOW_BASE_URL （可选，默认 https://api.siliconflow.cn/v1）
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Dict

import sf_client

SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n(.*?)(?=\n\n|\Z)",
    re.DOTALL,
)

SYSTEM_ZH2EN = (
    "You are a professional subtitle translator. Translate Chinese spoken interview subtitles "
    "into natural, fluent, colloquial English. Requirements: "
    "1) Stay faithful to the meaning; use natural spoken English; "
    "2) Keep each subtitle line independent, do not merge or split; "
    "3) Output only the translation, no explanations or original text; "
    "4) Keep proper nouns, names and brand names in their standard English form."
)

SYSTEM_EN2ZH = (
    "你是专业的字幕翻译，把英文口语访谈翻译成自然、地道、口语化的简体中文。"
    "要求：1) 忠实原意，语气自然，符合中文表达习惯；"
    "2) 保持每条字幕独立，不要合并或拆分；3) 不要添加解释、注释或原文；"
    "4) 保留人名、专有名词的通用译法；"
    "5) 【最重要】字幕是按时间轴切开的，一句话常常被拆到相邻几条里，"
    "单条看起来可能不完整。你只能翻译该条实际出现的文字，"
    "严禁根据常识、名人名句或上下文自行补全句子、补充原文没有的信息，"
    "也不要把下一条的内容提前翻译到这一条。原文残缺就译成对应的残缺中文。"
)


# ── 投资领域术语表 ──────────────────────────────────────────────────────────
# 通用模型对投资圈的人名靠猜：实测 DeepSeek-V3 把
# "I LEAVE FOR MR. BUFFETT" 译成了「我留给巴特勒先生」。频道做的是价值投资
# 内容，把巴菲特的名字译错是硬伤，所以把对照表直接钉进提示词。

GLOSSARY_PATH = Path(__file__).resolve().parent.parent / "glossary.json"

_glossary_cache: Dict[str, str] | None = None


def load_glossary(path=None) -> Dict[str, str]:
    """读 ``glossary.json``，把分组拍平成 ``{英文: 中文}``。

    表读不到不该让整条流水线停下来 —— 没有术语表的翻译只是可能译错人名，
    比直接不出片好。但必须留痕，否则「怎么又译成巴特勒了」查不出原因。
    """
    global _glossary_cache
    if path is None and _glossary_cache is not None:
        return _glossary_cache

    p = Path(path) if path is not None else GLOSSARY_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"[translate] ⚠️ 术语表 {p} 读取失败（{e}），本次翻译不注入术语约束",
              file=sys.stderr)
        raw = {}

    flat: Dict[str, str] = {}
    for group in (raw.get("terms") or {}).values():
        if isinstance(group, dict):
            flat.update(group)

    if path is None:
        _glossary_cache = flat
    return flat


def glossary_block(direction: str, terms: Dict[str, str] | None = None) -> str:
    """把术语表渲染成注入提示词的那段文本；表为空时返回空串。

    长词排在前面，模型照着从上往下看时先撞上 "Warren Buffett" 这种更具体的
    条目，不会先被 "Buffett" 截胡。
    """
    terms = load_glossary() if terms is None else terms
    if not terms:
        return ""
    items = sorted(terms.items(), key=lambda kv: (-len(kv[0]), kv[0]))
    if direction == "zh2en":
        lines = "\n".join(f"- {zh} → {en}" for en, zh in items)
        return ("\nMandatory terminology (investment domain). Render these "
                "exactly as given, do not invent alternatives:\n" + lines)
    lines = "\n".join(f"- {en} → {zh}" for en, zh in items)
    return ("\n投资领域专有名词对照表（强制）：下列词一律按此译法输出，"
            "不得音译成别的名字，也不得自创译名。\n" + lines)


# ── 语音识别专名纠错说明 ────────────────────────────────────────────────────
# 「巴特勒先生」的根因在**语音识别**，不在翻译：faster-whisper 把音频听成了
# "Mr. Butler"，翻译只是忠实地把错的输入译对了。实测换模型无效（base→Butler、
# small→Bob、medium→Bowman，没一个听对），加 initial_prompt / hotwords 也无效
# （只影响开头，长音频走到 154s 已被前文上下文冲掉，且会把分段变粗）。
# 只有在翻译提示词里明说「原文来自 ASR、专名可能被听错」才有效：用真实生产
# ASR 文本实测，仅术语表时 5/5 仍输出「巴特勒」，补上这段后 5/5 修正为「巴菲特」。
# 措辞对效果敏感 —— 收紧后实测 0/5 失效，改动前先复测。

ASR_NOTE_EN2ZH = (
    "\n注意：英文原文来自语音识别，专有名词可能被听错（例如把 Buffett 听成 "
    "Butler、Bob、Buffet）。当某个人名/机构名在语境上明显应当是对照表里的词时，"
    "按对照表的正确译名输出，不要照着听错的拼写音译。"
)

ASR_NOTE_ZH2EN = (
    "\nNote: the Chinese source text comes from automatic speech recognition, "
    "so proper nouns may have been misheard (for example 巴菲特 transcribed as "
    "巴特勒, 巴布 or 巴菲). When a person or organisation name is clearly, from "
    "the context, one of the entries in the terminology table above, output the "
    "correct form from that table instead of transliterating the misheard spelling."
)


def asr_note(direction: str) -> str:
    """语音识别专名纠错说明；术语表为空时不注入（说明本身要引用对照表）。"""
    if not load_glossary():
        return ""
    return ASR_NOTE_ZH2EN if direction == "zh2en" else ASR_NOTE_EN2ZH


def system_prompt(direction: str) -> str:
    base = SYSTEM_ZH2EN if direction == "zh2en" else SYSTEM_EN2ZH
    return base + glossary_block(direction) + asr_note(direction)


def parse_srt(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    entries = []
    for m in SRT_BLOCK_RE.finditer(content):
        idx, start, end, text = m.groups()
        entries.append({
            "index": int(idx),
            "start": start.strip(),
            "end": end.strip(),
            "src": " ".join(line.strip() for line in text.strip().splitlines()),
        })
    return entries


def strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


# 翻译降级链。实测发现硅基流动的限流是**分模型**的：DeepSeek-V3 持续返回
# 429（System is too busy）的同一时刻，Qwen3-8B 完全正常。热门免费模型拥堵
# 是常态，而定时任务是无人值守的，只做退避不换模型等于把整条流水线
# 绑在一个拥堵端点上。顺序尝试，哪个能用用哪个。
FALLBACK_MODELS = [
    "Qwen/Qwen3-8B",
    "Qwen/Qwen2.5-7B-Instruct",
    "THUDM/glm-4-9b-chat",
]


def _once(messages, api_key, model, base_url, temperature):
    """对单一模型发一次（``sf_client`` 内部按分类退避重试）。

    重试到上限仍不行就返回 None，交由上层换模型；鉴权 / 余额 / 请求体这类
    换模型也救不了的失败直接往上抛。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "stream": False, "enable_thinking": False}
    try:
        resp = sf_client.post(url, headers=headers, json=payload, timeout=120,
                              tag="translate")
    except sf_client.RetriesExhausted as e:
        print(f"[translate] {model} 重试 {e.attempts} 次仍失败：{e.detail}",
              file=sys.stderr)
        return None
    return strip_think(resp.json()["choices"][0]["message"]["content"])


def chat(messages, api_key, model, base_url, temperature=0.3):
    chain = [model] + [m for m in FALLBACK_MODELS if m != model]
    for i, m in enumerate(chain):
        out = _once(messages, api_key, m, base_url, temperature)
        if out is not None:
            if i > 0:
                print(f"[translate] ✓ 已降级到 {m}", file=sys.stderr)
            return out
        if i + 1 < len(chain):
            print(f"[translate] {m} 持续不可用，换 {chain[i+1]}", file=sys.stderr)
    raise RuntimeError(f"硅基流动全部模型均不可用（已试：{', '.join(chain)}）")


def translate_batch(texts: List[str], api_key, model, base_url, direction="zh2en") -> List[str]:
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    system = system_prompt(direction)
    if direction == "zh2en":
        user = (
            "Translate each Chinese subtitle line below into English. "
            "Keep the numbering, one line per item, format: 'N. translation'. "
            "Output only the translated lines, nothing else.\n\n" + numbered
        )
    else:
        user = (
            "把下面每一条英文字幕翻译成中文。严格保持编号，每条一行，"
            "格式为「序号. 译文」，只输出译文行，不要输出英文原文或其它内容。\n\n" + numbered
        )
    content = chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        api_key, model, base_url,
    )
    result = {}
    for line in content.splitlines():
        m = re.match(r"\s*(\d+)[.、\)]\s*(.+)", line)
        if m:
            result[int(m.group(1))] = m.group(2).strip()
    return [result.get(i + 1, "") for i in range(len(texts))]


def translate_all(texts: List[str], api_key, model, base_url, batch_size=20, direction="zh2en") -> List[str]:
    out: List[str] = []
    i = 0
    while i < len(texts):
        batch = texts[i: i + batch_size]
        translated = translate_batch(batch, api_key, model, base_url, direction)
        if any(not t for t in translated):
            if len(batch) > 1:
                half = max(1, len(batch) // 2)
                translated = (
                    translate_all(batch[:half], api_key, model, base_url, half, direction) +
                    translate_all(batch[half:], api_key, model, base_url, half, direction)
                )
            else:
                system = system_prompt(direction)
                prompt = ("Translate to English, output only the translation:\n"
                          if direction == "zh2en"
                          else "只把这句英文翻成中文，只输出译文：\n")
                single = chat(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": prompt + batch[0]}],
                    api_key, model, base_url,
                ).strip()
                translated = [single or batch[0]]
        out.extend(translated)
        print(f"[translate] 已翻译 {min(i + batch_size, len(texts))}/{len(texts)} 条", flush=True)
        i += batch_size
    return out


def main():
    parser = argparse.ArgumentParser(description="用硅基流动 LLM 翻译字幕，输出双语 JSON")
    parser.add_argument("--input", required=True, help="SRT 路径（中文或英文）")
    parser.add_argument("--output", required=True, help="输出双语 JSON 路径")
    parser.add_argument("--direction", default="zh2en",
                        choices=["zh2en", "en2zh"],
                        help="翻译方向：zh2en（中→英，默认）或 en2zh（英→中）")
    parser.add_argument("--batch-size", type=int, default=20, help="每批翻译条数")
    args = parser.parse_args()

    api_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
    if not api_key:
        print("[translate] 缺少 SILICONFLOW_API_KEY", file=sys.stderr)
        sys.exit(1)
    # 翻译对准确性要求最高，允许单独指定模型。
    # 实测：免费的 Qwen/Qwen3-8B 在“一句话被切成多条字幕”时会自行补全名人名句
    # 而产生幻觉，且会把 patient 误译为“患者”；DeepSeek-V3 能正确处理。
    # 优先级：SILICONFLOW_TRANSLATE_MODEL > SILICONFLOW_MODEL > 默认值
    model = (
        (os.environ.get("SILICONFLOW_TRANSLATE_MODEL") or "").strip()
        or (os.environ.get("SILICONFLOW_MODEL") or "").strip()
        or "deepseek-ai/DeepSeek-V3"
    )
    base_url = (os.environ.get("SILICONFLOW_BASE_URL") or "").strip() or "https://api.siliconflow.cn/v1"
    print(f"[translate] 模型={model}  方向={args.direction}  接口={base_url}", flush=True)

    entries = parse_srt(args.input)
    if not entries:
        print("[translate] 未解析到任何字幕条目", file=sys.stderr)
        sys.exit(1)
    print(f"[translate] 解析到 {len(entries)} 条字幕", flush=True)

    src_texts = [e["src"] for e in entries]
    translated = translate_all(src_texts, api_key, model, base_url, args.batch_size, args.direction)

    result = []
    for e, t in zip(entries, translated):
        if args.direction == "zh2en":
            result.append({"index": e["index"], "start": e["start"], "end": e["end"],
                           "zh": e["src"], "en": t})
        else:
            result.append({"index": e["index"], "start": e["start"], "end": e["end"],
                           "en": e["src"], "zh": t})

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"[translate] 完成 -> {args.output}", flush=True)


if __name__ == "__main__":
    main()
