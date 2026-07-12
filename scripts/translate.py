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
import time
from typing import List, Dict

import requests

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
    "4) 保留人名、专有名词的通用译法。"
)


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


def chat(messages, api_key, model, base_url, temperature=0.3, max_retries=4):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": temperature,
               "stream": False, "enable_thinking": False}
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
        except requests.RequestException as e:
            print(f"[translate] 请求异常 {e}，{2**attempt}s 后重试", file=sys.stderr)
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 200:
            return strip_think(resp.json()["choices"][0]["message"]["content"])
        elif resp.status_code in (429, 500, 502, 503):
            wait = 2 ** attempt
            print(f"[translate] {resp.status_code} 限流/波动，{wait}s 后重试", file=sys.stderr)
            time.sleep(wait)
        else:
            print(f"[translate] 错误 {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
            resp.raise_for_status()
    raise RuntimeError("硅基流动多次重试仍失败")


def translate_batch(texts: List[str], api_key, model, base_url, direction="zh2en") -> List[str]:
    numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(texts))
    if direction == "zh2en":
        system = SYSTEM_ZH2EN
        user = (
            "Translate each Chinese subtitle line below into English. "
            "Keep the numbering, one line per item, format: 'N. translation'. "
            "Output only the translated lines, nothing else.\n\n" + numbered
        )
    else:
        system = SYSTEM_EN2ZH
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
                system = SYSTEM_ZH2EN if direction == "zh2en" else SYSTEM_EN2ZH
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
    model = (os.environ.get("SILICONFLOW_MODEL") or "").strip() or "Qwen/Qwen3-8B"
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
