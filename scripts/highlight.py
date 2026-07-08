#!/usr/bin/env python3
"""
highlight.py — 从字幕中自动识别"金句/高能片段"并打分排序

金句识别标准：
  - 有具体数字/比例（投资回报、时间跨度等）
  - 反常识/颠覆认知（"大多数人认为X，但实际上Y"）
  - 预言/前瞻性观点（"未来XX年..."）
  - 强情绪/强观点（"从不"、"绝对"、"必须"）
  - 故事性/案例（第一人称"我当时..."）

输出：highlights.json
  [
    {
      "rank": 1,
      "score": 9.2,
      "start": "00:03:42",
      "end": "00:04:15",
      "start_sec": 222.0,
      "end_sec": 255.0,
      "duration_sec": 33.0,
      "transcript_en": "...",
      "transcript_zh": "...",
      "reason": "包含具体数字，反常识观点",
      "title_suggestion": "帕伯莱：..."
    },
    ...
  ]

环境变量：
  SILICONFLOW_API_KEY  （必填）
  SILICONFLOW_MODEL    （可选，默认 Qwen/Qwen3-32B）
  SPEAKER_NAME         （可选，说话人名字，用于标题生成，默认"演讲者"）
"""

import argparse
import json
import os
import re
import sys
import time
from typing import List, Dict

import requests

# ── 常量 ──────────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "Qwen/Qwen2.5-72B-Instruct"
MIN_CLIP_SEC = 20       # 金句片段最短时长
MAX_CLIP_SEC = 180      # 金句片段最长时长
CONTEXT_PAD_SEC = 2.0   # 前后各加多少秒 padding


# ── LLM 调用 ──────────────────────────────────────────────────────────────────

def strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


def call_llm(messages, api_key, model, base_url, max_retries=4):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.3,
                "stream": False, "enable_thinking": False}
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
        except requests.RequestException as e:
            print(f"[highlight] 请求异常 {e}，{2**attempt}s 后重试", file=sys.stderr)
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 200:
            return strip_think(resp.json()["choices"][0]["message"]["content"])
        elif resp.status_code in (429, 500, 502, 503):
            wait = 2 ** attempt
            print(f"[highlight] {resp.status_code} 限流，{wait}s 后重试", file=sys.stderr)
            time.sleep(wait)
        else:
            print(f"[highlight] 错误 {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
            resp.raise_for_status()
    raise RuntimeError("LLM 多次重试失败")


# ── SRT 解析 ──────────────────────────────────────────────────────────────────

SRT_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})\s*\n(.*?)(?=\n\n|\Z)",
    re.DOTALL,
)


def ts2sec(h: str, ms: str) -> float:
    hh, mm, ss = h.split(":")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def sec2hms(s: float) -> str:
    s = max(0.0, s)
    h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def parse_srt(path: str) -> List[Dict]:
    with open(path, encoding="utf-8") as f:
        content = f.read()
    entries = []
    for m in SRT_RE.finditer(content):
        idx, sh, sms, eh, ems, text = m.groups()
        entries.append({
            "index": int(idx),
            "start_sec": ts2sec(sh, sms),
            "end_sec": ts2sec(eh, ems),
            "start": sec2hms(ts2sec(sh, sms)),
            "end": sec2hms(ts2sec(eh, ems)),
            "text": " ".join(line.strip() for line in text.strip().splitlines()),
        })
    return entries


def parse_bilingual_json(path: str) -> List[Dict]:
    """从 translate.py 输出的双语 JSON 加载"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 合并字幕为段落 ────────────────────────────────────────────────────────────

def merge_into_paragraphs(entries: List[Dict], gap_sec: float = 3.0,
                           max_sec: float = 60.0) -> List[Dict]:
    """
    将连续字幕合并成段落（说话停顿 > gap_sec 或段落 > max_sec 时分段）
    返回段落列表，每个段落包含合并后的文本和起止时间
    """
    if not entries:
        return []

    paragraphs = []
    cur = [entries[0]]

    for e in entries[1:]:
        last = cur[-1]
        gap = e["start_sec"] - last["end_sec"]
        dur = e["end_sec"] - cur[0]["start_sec"]
        if gap > gap_sec or dur > max_sec:
            paragraphs.append(cur)
            cur = [e]
        else:
            cur.append(e)
    if cur:
        paragraphs.append(cur)

    result = []
    for para in paragraphs:
        text_en = " ".join(e.get("text") or e.get("en", "") for e in para)
        text_zh = " ".join(e.get("zh", "") for e in para if e.get("zh"))
        result.append({
            "start_sec": para[0]["start_sec"],
            "end_sec": para[-1]["end_sec"],
            "start": sec2hms(para[0]["start_sec"]),
            "end": sec2hms(para[-1]["end_sec"]),
            "duration_sec": para[-1]["end_sec"] - para[0]["start_sec"],
            "text_en": text_en,
            "text_zh": text_zh,
        })
    return result


# ── LLM 金句识别 ──────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是一位专业的短视频内容策划，擅长从价值投资演讲/访谈中识别最有传播价值的片段。

金句/高能片段的特征（满足1条即可，越多越好）：
1. 含具体数字或比例（投资回报、时间跨度、概率等）
2. 反常识/颠覆认知（挑战普通人的固有观念）
3. 前瞻性预言（对未来的大胆判断）
4. 强烈观点（"从不"、"绝对"、"必须"、"最重要的"）
5. 有趣故事或具体案例（第一人称经历）
6. 简洁有力的人生/投资哲学总结

评分标准（1-10分）：
- 传播潜力（能让人忍不住转发）
- 信息密度（短时间内信息量大）
- 情绪强度（能触动情绪）"""


def score_highlights(paragraphs: List[Dict], api_key: str, model: str,
                     base_url: str, speaker: str, top_n: int = 10) -> List[Dict]:
    """分批发给 LLM 打分，返回 top_n 高分片段"""

    # 构建带编号的段落摘要
    BATCH = 15  # 每批处理段落数
    all_scored = []

    for batch_start in range(0, len(paragraphs), BATCH):
        batch = paragraphs[batch_start:batch_start + BATCH]

        numbered = ""
        for i, p in enumerate(batch):
            global_i = batch_start + i + 1
            en = p["text_en"][:300]  # 截断避免 token 过多
            zh = p["text_zh"][:150] if p["text_zh"] else ""
            numbered += f"\n[{global_i}] ({p['start']}~{p['end']}, {p['duration_sec']:.0f}秒)\n英文: {en}\n中文: {zh}\n"

        lines_msg = [
            f"以下是访谈视频的字幕段落（共{len(batch)}段），主讲嘉宾是【{speaker}】。",
            "",
            numbered,
            "注意：访谈中有两类人发言：",
            f"- 主讲嘉宾（{speaker}）：说自己的经历、观点、故事，用第一人称，是视频的主角",
            "- 主持人/采访者：提问、过渡、背景介绍，常出现'您'、'请问'、'我想问您'等词",
            "",
            "重要规则：",
            f"1. 只对主讲嘉宾（{speaker}）说话为主的段落打高分",
            "2. 如果一个段落主要是主持人在说话（提问、铺垫），最高只能给5分",
            f"3. 优先选{speaker}讲述自己亲身经历、发表观点、说故事的段落",
            "",
            "请对每个段落评分，返回所有段落的评分结果。",
            "输出 JSON 数组，每个元素格式：",
            "{",
            '  "paragraph_index": <全局编号，整数>,',
            '  "score": <1-10的浮点数>,',
            f'  "speaker_ratio": <估算{speaker}发言占该段比例，0.0~1.0>,',
            '  "reason": "<为什么这段有价值，20字以内>",',
            f'  "title_suggestion": "<{speaker}：开头的标题，适合B站，15-25字>"',
            "}",
            "",
            "只输出 JSON 数组，不要其他文字。每个段落必须输出，不得省略。",
        ]
        user_msg = "\n".join(lines_msg)
        print(f"[highlight] 评分第 {batch_start//BATCH + 1} 批（段落 {batch_start+1}~{batch_start+len(batch)}）...", flush=True)
        content = call_llm(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": user_msg}],
            api_key, model, base_url
        )

        # 解析 JSON
        json_match = re.search(r"\[.*\]", content, re.DOTALL)
        if json_match:
            try:
                scored = json.loads(json_match.group())
                for item in scored:
                    idx = item.get("paragraph_index", 0) - 1  # 转为0-based
                    if 0 <= idx < len(paragraphs):
                        para = paragraphs[idx]
                        all_scored.append({
                            "score": float(item.get("score", 0)),
                            "speaker_ratio": float(item.get("speaker_ratio", 0.5)),
                            "reason": item.get("reason", ""),
                            "title_suggestion": item.get("title_suggestion", ""),
                            "start": para["start"],
                            "end": para["end"],
                            "start_sec": para["start_sec"],
                            "end_sec": para["end_sec"],
                            "duration_sec": para["duration_sec"],
                            "transcript_en": para["text_en"],
                            "transcript_zh": para["text_zh"],
                        })
            except json.JSONDecodeError:
                print(f"[highlight] 解析失败: {content[:200]}", file=sys.stderr)

    # 过滤主持人为主的段落（speaker_ratio < 0.5 说明主讲人发言不足一半）
    filtered = [x for x in all_scored if x.get("speaker_ratio", 1.0) >= 0.5]
    if not filtered:
        print("[highlight] ⚠️  所有段落 speaker_ratio < 0.5，放宽阈值使用全部", file=sys.stderr)
        filtered = all_scored

    # ── 规则二：文本规则过滤主持人语句（零费用）──
    # 主持人特征词：大量「您」（对嘉宾称呼）、「请问」、「我想问」、「感谢您」等
    HOST_STARTERS = ["您", "请问", "想问", "感谢", "谢谢", "能否", "可以请"]
    HOST_STRONG = ["请问您", "我想问您", "能请您", "感谢您", "谢谢您的"]
    SPEAKER_STARTERS = ["我", "咱们", "其实", "那个时候", "当时", "所以", "然后",
                        "坦白", "说实话", "我觉得", "我们", "这个", "对于"]

    def is_host_dominated(text: str) -> bool:
        if not text:
            return False
        # 包含明显主持人套语（高精度）
        for pat in HOST_STRONG:
            if pat in text:
                return True
        # 段落前30字内出现「您」，且不含第一人称「我」开头
        prefix = text[:30]
        if "您" in prefix:
            # 如果段落本身也有大量"我"字，说明是对话混合段落，不过滤
            if text.count("我") < text.count("您"):
                return True
        return False

    rule_filtered = []
    for x in filtered:
        text = x.get("transcript_en", "") or x.get("transcript_zh", "")
        if is_host_dominated(text):
            print(f"[highlight] 规则过滤（主持人为主）: {x['start']}~{x['end']} | {text[:40]}", file=sys.stderr)
        else:
            rule_filtered.append(x)

    if rule_filtered:
        filtered = rule_filtered
    else:
        print("[highlight] ⚠️  规则过滤后无结果，保留全部", file=sys.stderr)

    # 按分数排序，取 top_n
    filtered.sort(key=lambda x: x["score"], reverse=True)
    top = filtered[:top_n]

    # 加 rank
    for i, item in enumerate(top):
        item["rank"] = i + 1

    return top


# ── 时间戳扩展（padding）─────────────────────────────────────────────────────

def expand_clip(item: Dict, total_duration: float,
                pad_sec: float = CONTEXT_PAD_SEC,
                min_sec: float = MIN_CLIP_SEC,
                max_sec: float = MAX_CLIP_SEC) -> Dict:
    """给金句前后加 padding，保证时长在合理范围内"""
    start = max(0.0, item["start_sec"] - pad_sec)
    end = min(total_duration, item["end_sec"] + pad_sec)

    # 若太短，往后延伸
    if end - start < min_sec:
        end = min(total_duration, start + min_sec)

    # 若太长，从后截
    if end - start > max_sec:
        end = start + max_sec

    return {
        **item,
        "clip_start": sec2hms(start),
        "clip_end": sec2hms(end),
        "clip_start_sec": start,
        "clip_end_sec": end,
        "clip_duration_sec": end - start,
    }


# ── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="从字幕自动识别金句片段并打分")
    parser.add_argument("--srt", required=True, help="英文 SRT 文件路径（全片）")
    parser.add_argument("--bilingual", default=None,
                        help="双语 JSON 路径（translate.py 输出），有则合并中文")
    parser.add_argument("--output", required=True, help="输出 highlights.json 路径")
    parser.add_argument("--speaker", default="演讲者", help="说话人名字，如'帕伯莱'")
    parser.add_argument("--top-n", type=int, default=10, help="输出前N个金句，默认10")
    parser.add_argument("--total-duration", type=float, default=0,
                        help="视频总时长（秒），用于 padding 计算")
    args = parser.parse_args()

    api_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
    if not api_key:
        print("[highlight] 缺少 SILICONFLOW_API_KEY", file=sys.stderr); sys.exit(1)
    model = (os.environ.get("SILICONFLOW_MODEL") or "").strip() or DEFAULT_MODEL
    base_url = (os.environ.get("SILICONFLOW_BASE_URL") or "").strip() or "https://api.siliconflow.cn/v1"
    speaker = os.environ.get("SPEAKER_NAME", args.speaker)

    print(f"[highlight] 读取字幕: {args.srt}", flush=True)
    srt_entries = parse_srt(args.srt)
    print(f"[highlight] 共 {len(srt_entries)} 条字幕", flush=True)

    # 合并中文（若有双语 JSON）
    zh_map = {}
    if args.bilingual and os.path.exists(args.bilingual):
        bi = parse_bilingual_json(args.bilingual)
        for e in bi:
            zh_map[e.get("index", 0)] = e.get("zh", "")
        for e in srt_entries:
            e["zh"] = zh_map.get(e["index"], "")

    # 合并为段落
    paragraphs = merge_into_paragraphs(srt_entries, gap_sec=3.0, max_sec=90.0)
    print(f"[highlight] 合并为 {len(paragraphs)} 个段落", flush=True)

    # 总时长
    total_dur = args.total_duration or (srt_entries[-1]["end_sec"] if srt_entries else 0)

    # LLM 打分
    highlights = score_highlights(paragraphs, api_key, model, base_url,
                                  speaker=speaker, top_n=args.top_n)

    # 加 padding
    highlights = [expand_clip(h, total_dur) for h in highlights]

    # 输出
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(highlights, f, ensure_ascii=False, indent=2)

    print(f"\n[highlight] Top {len(highlights)} 金句片段:", flush=True)
    for h in highlights:
        print(f"  [{h['rank']:02d}] 分数={h['score']:.1f} | {h['clip_start']}~{h['clip_end']} ({h['clip_duration_sec']:.0f}s)", flush=True)
        print(f"       {h['title_suggestion']}", flush=True)
        print(f"       原因: {h['reason']}", flush=True)

    print(f"\n[highlight] 完成 → {args.output}", flush=True)


if __name__ == "__main__":
    main()
