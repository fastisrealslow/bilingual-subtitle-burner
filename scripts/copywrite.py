#!/usr/bin/env python3
"""
copywrite.py — 自动生成 B站标题、简介、标签

输入：highlights.json（highlight.py 输出）
输出：manifest.json，每条包含：
  - title: B站标题
  - desc: 简介文案（150字以内）
  - tags: 标签列表
  - clip_start / clip_end / clip_duration_sec
  - video_file: 对应切片文件名（由 clip.py 生成）

环境变量：
  SILICONFLOW_API_KEY
  SILICONFLOW_MODEL
  SPEAKER_NAME   说话人（如"帕伯莱"）
  CHANNEL_NAME   频道名（如"价值投资讲堂"）
"""

import argparse
import json
import os
import re
import sys
import time
from typing import List, Dict

import requests


DEFAULT_MODEL = "Qwen/Qwen3-8B"


def strip_think(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


def call_llm(messages, api_key, model, base_url, max_retries=4):
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "temperature": 0.5,
                "stream": False, "enable_thinking": False}
    for attempt in range(max_retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
        except requests.RequestException as e:
            print(f"[copywrite] 请求异常 {e}，重试", file=sys.stderr)
            time.sleep(2 ** attempt); continue
        if resp.status_code == 200:
            return strip_think(resp.json()["choices"][0]["message"]["content"])
        elif resp.status_code in (429, 500, 502, 503):
            time.sleep(2 ** attempt)
        else:
            resp.raise_for_status()
    raise RuntimeError("LLM 多次重试失败")


SYSTEM_PROMPT = """\
你是一位专注于价值投资内容的 B站 UP主助理，擅长写吸引眼球、引发思考的标题和简介。

标题风格：
- 【人名】开头或放重要位置
- 包含数字、反问、或颠覆性观点
- 15-28 字，不超过 30 字
- 适当加感叹号或问号
- 禁用：标题党、夸大其词、虚假承诺

简介风格：
- 100-150 字
- 第一句是核心观点的总结（吸引继续看）
- 中间展开观点的逻辑与依据
- 结尾引导关注/点赞

【事实准确性红线】
- 只能写原文里真实出现过的内容。绝对不要编造演讲场合、活动名称、
  时间、地点、职位或数据。
- 给你的“频道”是**我们自己的搜索标签**，不是讲话人所在的场合。
  不得写成“某某在【频道名】上说”这类句式。不确定场合就不提场合。

标签：选 5-8 个，包含：演讲者名、价值投资、相关话题关键词。
标题、简介、标签一律用**简体中文**，不要用繁体。"""


def _normalize_copy(d: Dict, protect=None) -> Dict:
    """把模型返回的文案强制转成简体，并规范化中文标点。

    提示词里已经写了“一律用简体中文”，但实测不管用：处理台湾财经
    视频时，模型会跟着原文语言走，输出“美國經濟衰退的兩個指標”
    “價值投資”这类繁体。提示词是软约束，这里做硬约束。

    演讲者名要放进保护词表——实测频道名「股乾爹」会被转成「股干爹」。

    标点规范化必须在这里做，不能只留给 step8。manifest.json 的 title
    同时被 step6（切片文件名）和 step7（烧进封面图）直接读走，两处都
    绕过了 step8 的 clean_title，模型返回的弯引号和半角标点会原样出现
    在封面上。
    """
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from platform_rules import to_simplified, normalize_cjk_punctuation
    except ImportError:
        return d

    def fix(s: str) -> str:
        return normalize_cjk_punctuation(to_simplified(s, protect))

    d["title"] = fix(str(d.get("title", "")))
    d["desc"] = fix(str(d.get("desc", "")))
    d["tags"] = [to_simplified(str(t), protect) for t in (d.get("tags") or [])]
    return d


def generate_copy(highlight: Dict, speaker: str, channel: str,
                  api_key: str, model: str, base_url: str) -> Dict:
    en = highlight.get("transcript_en", "")[:500]
    zh = highlight.get("transcript_zh", "")[:300]
    suggested = highlight.get("title_suggestion", "")
    reason = highlight.get("reason", "")

    user_msg = f"""\
说话人：{speaker}
频道：{channel}
片段原因：{reason}
LLM建议标题（参考）：{suggested}

片段英文原文（前500字）：
{en}

片段中文翻译（前300字）：
{zh}

请生成：
1. B站标题（15-28字）
2. 简介文案（100-150字）
3. 标签列表（5-8个）

输出 JSON：
{{
  "title": "...",
  "desc": "...",
  "tags": ["...", "..."]
}}
只输出 JSON，不要其他文字。"""

    content = call_llm(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": user_msg}],
        api_key, model, base_url
    )
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if json_match:
        try:
            return _normalize_copy(json.loads(json_match.group()), [speaker])
        except json.JSONDecodeError:
            pass
    # 降级：返回建议标题
    return _normalize_copy(
        {"title": suggested, "desc": zh[:150], "tags": [speaker, "价值投资"]}, [speaker])


def main():
    parser = argparse.ArgumentParser(description="生成 B站标题/简介/标签")
    parser.add_argument("--highlights", required=True, help="highlights.json 路径")
    parser.add_argument("--output", required=True, help="输出 manifest.json 路径")
    parser.add_argument("--speaker", default="演讲者")
    parser.add_argument("--channel", default="价值投资讲堂")
    parser.add_argument("--concurrency", type=int, default=4,
                        help="文案生成并发数（默认 4）")
    args = parser.parse_args()

    api_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
    if not api_key:
        print("[copywrite] 缺少 SILICONFLOW_API_KEY", file=sys.stderr); sys.exit(1)
    model = (os.environ.get("SILICONFLOW_MODEL") or "").strip() or DEFAULT_MODEL
    base_url = (os.environ.get("SILICONFLOW_BASE_URL") or "").strip() or "https://api.siliconflow.cn/v1"
    speaker = os.environ.get("SPEAKER_NAME", args.speaker)
    channel = os.environ.get("CHANNEL_NAME", args.channel)

    with open(args.highlights, encoding="utf-8") as f:
        highlights = json.load(f)

    def build_item(h: Dict) -> Dict:
        print(f"[copywrite] 生成第 {h['rank']} 条文案...", flush=True)
        copy = generate_copy(h, speaker, channel, api_key, model, base_url)
        safe_title = re.sub(r'[\\/:*?"<>|]', '_', copy.get("title", f"clip_{h['rank']}")).strip()[:40]
        print(f"  [{h['rank']}] 标题：{copy.get('title', '')} | 标签：{copy.get('tags', [])}", flush=True)
        return {
            "rank": h["rank"],
            "score": h["score"],
            "title": copy.get("title", ""),
            "desc": copy.get("desc", ""),
            "tags": copy.get("tags", []),
            "clip_start": h.get("clip_start", h["start"]),
            "clip_end": h.get("clip_end", h["end"]),
            "clip_start_sec": h.get("clip_start_sec", h["start_sec"]),
            "clip_end_sec": h.get("clip_end_sec", h["end_sec"]),
            "clip_duration_sec": h.get("clip_duration_sec", h["duration_sec"]),
            "video_filename": f"{h['rank']:02d}_{safe_title}.mp4",
            "transcript_zh": h.get("transcript_zh", ""),
            "reason": h.get("reason", ""),
        }

    # 并发生成（call_llm 内部已有指数退避重试，无需 sleep 限速），最后按 rank 保序
    workers = max(1, min(args.concurrency, len(highlights) or 1))
    if workers > 1 and len(highlights) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            manifest = list(ex.map(build_item, highlights))
    else:
        manifest = [build_item(h) for h in highlights]
    manifest.sort(key=lambda x: x["rank"])

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"\n[copywrite] 完成，共 {len(manifest)} 条 → {args.output}", flush=True)


if __name__ == "__main__":
    main()
