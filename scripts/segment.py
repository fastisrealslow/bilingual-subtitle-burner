#!/usr/bin/env python3
"""
segment.py — 智能语义分段脚本

流程:
  1. 读取完整英文 SRT 字幕文件
  2. 调用 LLM（硅基流动）按话题/语义将字幕切分成若干 ~10 分钟章节
  3. 输出章节列表 JSON：[{"index": 1, "title_zh": "...", "start": "HH:MM:SS", "end": "HH:MM:SS"}, ...]

环境变量:
  SILICONFLOW_API_KEY   （必填）
  SILICONFLOW_MODEL     （可选，默认 Qwen/Qwen3-8B）
  SILICONFLOW_BASE_URL  （可选）

用法:
  python segment.py --input full.srt --output segments.json [--target-duration 600]
"""

import argparse
import json
import os
import re
import sys
import time
from typing import List, Dict, Any

import requests


# ── SRT 解析 ─────────────────────────────────────────────────────────────────

SRT_BLOCK_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})\s*\n(.*?)(?=\n\n|\Z)",
    re.DOTALL,
)


def srt_time_to_seconds(t: str) -> float:
    """HH:MM:SS 或 HH:MM:SS,mmm → 秒数"""
    t = t.replace(",", ".")
    parts = t.split(":")
    h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    return h * 3600 + m * 60 + s


def seconds_to_hms(s: float) -> str:
    """秒数 → HH:MM:SS"""
    s = max(0.0, s)
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = int(s % 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def parse_srt(path: str) -> List[Dict]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    entries = []
    for m in SRT_BLOCK_RE.finditer(content):
        idx, sh, sms, eh, ems, text = m.groups()
        start_sec = srt_time_to_seconds(f"{sh},{sms}")
        end_sec = srt_time_to_seconds(f"{eh},{ems}")
        text_clean = " ".join(line.strip() for line in text.strip().splitlines())
        entries.append({
            "index": int(idx),
            "start_sec": start_sec,
            "end_sec": end_sec,
            "start": seconds_to_hms(start_sec),
            "end": seconds_to_hms(end_sec),
            "text": text_clean,
        })
    return entries


# ── LLM 调用 ──────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一位专业的内容编辑，擅长分析访谈/演讲视频的字幕，识别话题转折点，将内容划分为结构清晰的章节。

要求：
1. 每个章节时长约 8-12 分钟（可适当浮动，但不要强制切在句子中间）
2. 在话题自然转换处切割，不要在句子中间切断
3. 为每个章节生成一个简洁的中文标题（5-15 字），准确概括该段内容
4. 输出格式必须是合法 JSON 数组，不要有多余文字"""

def strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


def call_llm(messages: List[Dict], api_key: str, model: str, base_url: str,
             max_retries: int = 4) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
        "stream": False,
        "enable_thinking": False,
    }
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
        except requests.RequestException as e:
            print(f"[segment] 请求异常 {e}，{2**attempt}s 后重试", file=sys.stderr)
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 200:
            return strip_think(resp.json()["choices"][0]["message"]["content"])
        elif resp.status_code in (429, 500, 502, 503):
            wait = 2 ** attempt
            print(f"[segment] {resp.status_code} 限流/服务波动，{wait}s 后重试", file=sys.stderr)
            time.sleep(wait)
        else:
            print(f"[segment] 错误 {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
            resp.raise_for_status()
    raise RuntimeError("LLM 多次重试仍失败")


# ── 核心分段逻辑 ──────────────────────────────────────────────────────────────

def build_srt_summary(entries: List[Dict], max_chars: int = 8000) -> str:
    """
    将字幕条目压缩成适合发给 LLM 的摘要格式：
    [00:00:00] 字幕文本
    每隔若干条取一条，保留时间戳，控制总字数在 max_chars 以内
    """
    # 先尝试全量
    lines = []
    for e in entries:
        lines.append(f"[{e['start']}] {e['text']}")
    full = "\n".join(lines)
    if len(full) <= max_chars:
        return full

    # 采样：取约 1/N 的条目
    n = len(entries)
    step = max(1, n * len(full) // max_chars)
    sampled = entries[::step]
    lines = [f"[{e['start']}] {e['text']}" for e in sampled]
    return "\n".join(lines)


def segment_by_llm(entries: List[Dict], total_duration: float,
                   target_duration: int, api_key: str, model: str, base_url: str) -> List[Dict]:
    """
    将字幕交给 LLM，让它按语义分段，返回章节列表。
    """
    srt_summary = build_srt_summary(entries)
    total_hms = seconds_to_hms(total_duration)
    est_chapters = max(2, int(total_duration // target_duration))

    user_prompt = f"""以下是一段英文访谈/演讲的字幕（格式：[时间戳] 字幕文本）：

---
{srt_summary}
---

视频总时长约 {total_hms}，目标章节数约 {est_chapters} 个（每章约 {target_duration // 60} 分钟）。

请按照话题转换点，将内容划分为 {est_chapters} 个左右的章节。

输出一个 JSON 数组，每个元素格式如下：
{{
  "index": 1,
  "title_zh": "章节中文标题",
  "start": "HH:MM:SS",
  "end": "HH:MM:SS"
}}

注意：
- start/end 必须是字幕中实际出现的时间戳附近的值，格式为 HH:MM:SS
- 第一章节的 start 为 "00:00:00"
- 最后一章节的 end 为视频结尾时间
- 相邻章节的 end 和下一章节的 start 应当相同或相近
- 只输出 JSON 数组，不要有其他文字"""

    print(f"[segment] 调用 LLM 进行语义分段（模型={model}）...")
    content = call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": user_prompt}],
        api_key, model, base_url
    )

    # 提取 JSON
    json_match = re.search(r"\[.*\]", content, re.DOTALL)
    if not json_match:
        print(f"[segment] LLM 返回内容无法解析为 JSON:\n{content[:500]}", file=sys.stderr)
        raise ValueError("LLM 未返回有效 JSON")

    chapters = json.loads(json_match.group())
    return chapters


def snap_to_subtitle(chapters: List[Dict], entries: List[Dict], total_duration: float) -> List[Dict]:
    """
    将 LLM 输出的时间戳对齐到最近的字幕边界，避免在字幕句子中间切割。
    同时确保 start/end 合理。
    """
    subtitle_times = sorted(set(e["start_sec"] for e in entries) | {0.0, total_duration})

    def nearest(t: float) -> float:
        return min(subtitle_times, key=lambda x: abs(x - t))

    result = []
    for i, ch in enumerate(chapters):
        try:
            start_sec = srt_time_to_seconds(ch["start"])
            end_sec = srt_time_to_seconds(ch["end"])
        except Exception:
            # 时间格式异常，跳过 snap
            start_sec = 0.0
            end_sec = total_duration

        snapped_start = nearest(start_sec)
        snapped_end = nearest(end_sec)

        # 保证 end > start
        if snapped_end <= snapped_start:
            snapped_end = min(total_duration, snapped_start + 30)

        result.append({
            "index": ch.get("index", i + 1),
            "title_zh": ch.get("title_zh", f"章节 {i + 1}"),
            "start": seconds_to_hms(snapped_start),
            "end": seconds_to_hms(snapped_end),
            "start_sec": snapped_start,
            "end_sec": snapped_end,
            "duration_sec": snapped_end - snapped_start,
        })

    # 修正首尾
    if result:
        result[0]["start"] = "00:00:00"
        result[0]["start_sec"] = 0.0
        result[-1]["end"] = seconds_to_hms(total_duration)
        result[-1]["end_sec"] = total_duration

    # 相邻章节对齐：下一章节的 start = 当前章节的 end
    for i in range(1, len(result)):
        result[i]["start"] = result[i - 1]["end"]
        result[i]["start_sec"] = result[i - 1]["end_sec"]
        result[i]["duration_sec"] = result[i]["end_sec"] - result[i]["start_sec"]

    return result


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="使用 LLM 对字幕进行语义分段，输出章节 JSON")
    parser.add_argument("--input", required=True, help="完整英文 SRT 文件路径")
    parser.add_argument("--output", required=True, help="输出章节 JSON 路径")
    parser.add_argument("--target-duration", type=int, default=600,
                        help="目标章节时长（秒），默认 600（10 分钟）")
    args = parser.parse_args()

    api_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if not api_key:
        print("[segment] 缺少环境变量 SILICONFLOW_API_KEY", file=sys.stderr)
        sys.exit(1)
    model = (os.environ.get("SILICONFLOW_MODEL") or "").strip() or "Qwen/Qwen3-8B"
    base_url = (os.environ.get("SILICONFLOW_BASE_URL") or "").strip() or "https://api.siliconflow.cn/v1"

    print(f"[segment] 读取字幕: {args.input}")
    entries = parse_srt(args.input)
    if not entries:
        print("[segment] 未解析到字幕条目", file=sys.stderr)
        sys.exit(1)

    total_duration = entries[-1]["end_sec"]
    print(f"[segment] 共 {len(entries)} 条字幕，视频时长 {seconds_to_hms(total_duration)}")

    # 若视频太短（< 2 * target），直接作为单章节
    if total_duration < args.target_duration * 1.5:
        print("[segment] 视频较短，输出为单章节")
        result = [{
            "index": 1,
            "title_zh": "完整内容",
            "start": "00:00:00",
            "end": seconds_to_hms(total_duration),
            "start_sec": 0.0,
            "end_sec": total_duration,
            "duration_sec": total_duration,
        }]
    else:
        chapters_raw = segment_by_llm(
            entries, total_duration, args.target_duration, api_key, model, base_url
        )
        print(f"[segment] LLM 返回 {len(chapters_raw)} 个章节，对齐字幕边界...")
        result = snap_to_subtitle(chapters_raw, entries, total_duration)

    # 输出
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n[segment] 分段结果 ({len(result)} 章节):")
    for ch in result:
        dur_min = ch["duration_sec"] / 60
        print(f"  [{ch['index']:02d}] {ch['start']} ~ {ch['end']} ({dur_min:.1f}min) {ch['title_zh']}")
    print(f"\n[segment] 完成 → {args.output}")


if __name__ == "__main__":
    main()
