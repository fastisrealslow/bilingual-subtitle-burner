#!/usr/bin/env python3
"""
transcribe.py — 使用 faster-whisper 从视频中提取英文字幕（带时间轴）。
输出标准 SRT 文件。
"""
import argparse
import os
import re
import sys

from faster_whisper import WhisperModel

# whisper 转写中文时默认几乎不输出标点，整条 SRT 一个句号都没有，
# 下游 highlight.py 的句末标点吸附因此完全失效（切点永远落在半句话上）。
# 用一段本身带标点的中文示例当 initial_prompt 能把标点带出来——纯指令式的
# 提示词（“请使用标准标点符号”）实测无效，必须是示例。
ZH_PUNCT_PROMPT = (
    "这是一段中文财经访谈的转写。他说，通货膨胀是因，经济放缓是果。"
    "市场为什么会这样反应？我认为有三点原因。"
)

_CJK = re.compile(r"[一-鿿]")
_SENT_END = "。！？；!?;"
_CLAUSE_END = "，、：,:"
MAX_CJK_SUB_CHARS = 24   # 一条中文字幕的字数上限（≈854 宽下的两行）


def _cut_after(text: str, seps: str) -> list:
    """按 seps 切分，标点留在前一段末尾。"""
    out, cur = [], ""
    for ch in text:
        cur += ch
        if ch in seps:
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out


def split_segment(text: str, start: float, end: float,
                  max_chars: int = MAX_CJK_SUB_CHARS) -> list:
    """把过长的中文段落按句读拆成多条字幕，时间按字数比例分配。

    带上 ZH_PUNCT_PROMPT 之后 whisper 会吐出整段带标点的长句（实测一条
    字幕 100+ 字），直接烧进画面要占五六行，全糊出安全区。这里按句号、
    再按逗号拆开，并把碎片回并到接近上限的长度，避免切得过碎。

    英文段落本来就是句子级的，原样返回，不动。
    """
    if not _CJK.search(text) or len(text) <= max_chars:
        return [(start, end, text)]

    pieces = []
    for p in _cut_after(text, _SENT_END):
        pieces.extend(_cut_after(p, _CLAUSE_END) if len(p) > max_chars else [p])

    merged = []
    for p in pieces:
        if merged and len(merged[-1]) + len(p) <= max_chars:
            merged[-1] += p
        else:
            merged.append(p)

    total = sum(len(p) for p in merged) or 1
    out, t = [], start
    for p in merged:
        dt = (end - start) * len(p) / total
        out.append((t, min(end, t + dt), p))
        t += dt
    return out


def format_timestamp(seconds: float) -> str:
    """把秒转换成 SRT 时间格式 HH:MM:SS,mmm"""
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000.0))
    hours = ms // 3_600_000
    ms -= hours * 3_600_000
    minutes = ms // 60_000
    ms -= minutes * 60_000
    secs = ms // 1000
    ms -= secs * 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def main():
    parser = argparse.ArgumentParser(description="用 faster-whisper 转写视频/音频为英文 SRT")
    parser.add_argument("--input", required=True, help="输入视频或音频文件路径")
    parser.add_argument("--output", required=True, help="输出 SRT 路径")
    parser.add_argument("--model", default="base", help="模型大小: tiny/base/small/medium/large-v3")
    parser.add_argument("--language", default="zh",
                        help="源语言，默认 zh（中文）。传 auto 或空字符串则自动检测")
    parser.add_argument("--compute-type", default="int8", help="int8 / int8_float16 / float16 / float32")
    parser.add_argument("--device", default="cpu", help="cpu 或 cuda")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[transcribe] 找不到输入文件: {args.input}", file=sys.stderr)
        sys.exit(1)

    print(f"[transcribe] 加载模型 {args.model} (device={args.device}, compute={args.compute_type}) ...")
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)

    # language 为 auto / 空字符串时传 None，让 whisper 自动检测语言
    lang = None if args.language in ("auto", "", None) else args.language
    print(f"[transcribe] 开始转写: {args.input} (language={lang or 'auto-detect'})")

    def run(language):
        return model.transcribe(
            args.input,
            language=language,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            initial_prompt=ZH_PUNCT_PROMPT if (language or "").startswith("zh") else None,
        )

    segments, info = run(lang)
    if lang is None and info.language.startswith("zh"):
        # initial_prompt 必须在调用前定下来，而中文提示词喂给英文音频会把输出
        # 带偏，所以 auto 时先让 transcribe 报一次语言再补提示词重开。
        # transcribe() 的语言探测是即时的、segments 是惰性的，上面那次没有
        # 消费 segments，所以没有白跑解码之外的算力。
        print(f"[transcribe] 自动检测为 {info.language}，补中文标点提示词后重开")
        segments, info = run(info.language)
    print(f"[transcribe] 检测语言={info.language} (置信度={info.language_probability:.2f})")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    count = 0
    with open(args.output, "w", encoding="utf-8") as f:
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue
            for s, e, t in split_segment(text, seg.start, seg.end):
                count += 1
                f.write(f"{count}\n")
                f.write(f"{format_timestamp(s)} --> {format_timestamp(e)}\n")
                f.write(f"{t}\n\n")
            print(f"  [{format_timestamp(seg.start)}] {text}")

    print(f"[transcribe] 完成，共 {count} 条字幕 -> {args.output}")


if __name__ == "__main__":
    main()
