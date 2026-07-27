#!/usr/bin/env python3
"""
clip.py — 按 manifest.json 把长片切成多条短视频，并烧录双语字幕

字幕规则（中文视频）：
  - SRT 里是中文原文，bilingual JSON 里有英文翻译
  - 英文字幕在上（较小字号），中文字幕在下（较大字号）
  - 视频分辨率 640×346，字号按此适配

字幕规则（英文视频，--srt-lang en）：
  - SRT 里是英文原文，bilingual JSON 里有中文翻译
  - 同样英文在上，中文在下
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


SRT_RE = re.compile(
    r"(\d+)\s*\n(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})\s*\n(.*?)(?=\n\n|\Z)",
    re.DOTALL,
)


def ts2sec(h: str, ms: str) -> float:
    hh, mm, ss = h.split(":")
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000


def sec2srt(s: float) -> str:
    s = max(0.0, s)
    h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60)
    ms = int(round((s - int(s)) * 1000))
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def sec2ass(s: float) -> str:
    h = int(s // 3600); m = int((s % 3600) // 60); sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def detect_srt_lang(srt_path: str) -> str:
    """
    从 SRT 内容反推语种。

    srt_lang 填反了不会报错：英文字幕会被同时当成中文行渲染，
    成片上两行都是英文，下面那行还按中文宽度硬切出 “ot / hers.”
    这种断词。实际踩过，所以在源头做一次校验。
    """
    try:
        with open(srt_path, encoding="utf-8-sig") as f:
            raw = f.read(20000)
    except OSError:
        return ""
    # 去掉序号行和时间轴行，只看正文
    body = "\n".join(
        ln for ln in raw.splitlines()
        if ln.strip() and not ln.strip().isdigit() and "-->" not in ln
    )
    if not body.strip():
        return ""
    cjk = sum(1 for ch in body if "\u4e00" <= ch <= "\u9fff")
    return "zh" if cjk >= max(4, len(body) * 0.05) else "en"


def srt_filter(srt_path: str, start_sec: float, end_sec: float, srt_lang: str = "zh") -> list:
    """
    从全片 SRT 提取时间段内字幕，时间戳相对化。
    srt_lang='zh'：SRT 里是中文，存到 zh 字段；en 字段留空待填
    srt_lang='en'：SRT 里是英文，存到 en 字段；zh 字段留空待填
    """
    with open(srt_path, encoding="utf-8") as f:
        content = f.read()
    result = []
    new_idx = 1
    for m in SRT_RE.finditer(content):
        orig_idx, sh, sms, eh, ems, text = m.groups()
        s = ts2sec(sh, sms); e = ts2sec(eh, ems)
        if e <= start_sec or s >= end_sec:
            continue
        ns = max(0.0, s - start_sec)
        ne = min(end_sec - start_sec, e - start_sec)
        src_text = " ".join(line.strip() for line in text.strip().splitlines())
        entry = {
            "index": new_idx,               # 切片内重新编号（仅用于 ASS 顺序）
            "orig_index": int(orig_idx),    # 全片 SRT 原始编号（用于与 bilingual 对齐）
            "start_sec": ns, "end_sec": ne,
            "start": sec2srt(ns), "end": sec2srt(ne),
            "zh": src_text if srt_lang == "zh" else "",
            "en": src_text if srt_lang == "en" else "",
        }
        result.append(entry)
        new_idx += 1
    return result


def merge_translation(entries: list, bilingual_path: str, full_start_sec: float,
                       srt_lang: str = "zh") -> list:
    """
    把双语 JSON 的翻译填进字幕条目。
    srt_lang='zh'：bilingual 有 en 字段，填到 entries 的 en
    srt_lang='en'：bilingual 有 zh 字段，填到 entries 的 zh
    """
    if not bilingual_path or not os.path.exists(bilingual_path):
        return entries
    with open(bilingual_path, encoding="utf-8") as f:
        bi = json.load(f)

    fill_field = "en" if srt_lang == "zh" else "zh"

    # 主方案：按原始 SRT 编号 (orig_index ↔ bilingual.index) 精确对齐，不依赖时间戳
    idx_map = {}
    for b in bi:
        if "index" in b:
            idx_map[int(b["index"])] = b.get(fill_field, "")

    # 兜底方案：时间戳映射（仅当编号对不上时使用）
    def bi_ts2sec(ts: str) -> float:
        ts = ts.replace(",", ".")
        p = ts.split(":")
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])

    ts_map = {}
    for b in bi:
        abs_start = bi_ts2sec(b["start"])
        rel_start = abs_start - full_start_sec
        ts_map[round(rel_start, 1)] = b.get(fill_field, "")

    for e in entries:
        val = ""
        # 1) 优先用原始编号精确匹配
        oi = e.get("orig_index")
        if oi is not None and oi in idx_map:
            val = idx_map[oi]
        # 2) 编号未命中 → 时间戳精确匹配
        if not val:
            val = ts_map.get(round(e["start_sec"], 1), "")
        # 3) 再不行 → 2 秒内最近的时间戳
        if not val and ts_map:
            closest = min(ts_map.keys(), key=lambda k: abs(k - e["start_sec"]))
            if abs(closest - e["start_sec"]) < 2.0:
                val = ts_map[closest]
        e[fill_field] = val
    return entries


# ── ASS 字幕生成 ──────────────────────────────────────────────────────────────
# 视频实际分辨率 640×346，PlayRes 按实际设置，字号按比例

# 中文行首禁则：这些标点不能出现在行首，必须跟着上一行走
LINE_START_FORBIDDEN = "。，、！？；：）」』】》”’%…·"


def _cjk_units(t: str) -> list:
    """把一行中文切成不该被拆开的最小单元。

    jieba 在就按词切，不在就退化成「CJK 逐字可断、英文单词不拆」，
    与 step7_cover._segment 同一套降级策略，不让分词库成为硬依赖。
    """
    try:
        import jieba
        units = [u for u in jieba.lcut(t) if u]
    except ImportError:
        units, buf = [], ""
        for ch in t:
            if ord(ch) > 127:
                if buf:
                    units.append(buf)
                    buf = ""
                units.append(ch)
            elif ch == " ":
                if buf:
                    units.append(buf)
                    buf = ""
            else:
                buf += ch
        if buf:
            units.append(buf)

    # 禁则标点粘回前一个单元，免得被抛到下一行行首
    merged = []
    for u in units:
        if merged and all(c in LINE_START_FORBIDDEN for c in u):
            merged[-1] += u
        else:
            merged.append(u)
    return merged


def _wrap_cjk(t: str, max_chars: int) -> list:
    """中文折行：按词边界断开，行数取最少，再把各行长度拉均。

    定长硬切有三个毛病：把 22 字的句子切成 21 + 1，末行只剩一个「。」；
    从词中间劈开（实测烧出过「…是因为把价 / 格波动当成了…」）；中英混排
    时连英文单词也照劈（「Empire S / tate Manufacturin / g Survey」）。
    step7_cover.wrap_title 早就为封面标题解决过同一个问题，字幕这边套同
    一套规则。
    """
    units = _cjk_units(t)
    if not units:
        return [t]

    def greedy(limit: float) -> list:
        lines, cur = [], ""
        for u in units:
            if cur and len(cur) + len(u) > limit:
                lines.append(cur)
                cur = u
            else:
                cur += u
        if cur:
            lines.append(cur)
        return lines

    lines = greedy(max_chars)
    n = len(lines)
    if n <= 1:
        return lines

    # 贪心会填出「满满一行 + 小尾巴」。在 [总长/n, max_chars] 里搜出仍能排成
    # n 行的最小行宽，据此重排。理论值 总长/n 通常落在某个词中间，直接拿它当
    # 目标会多出一行、均衡整个失效，所以只能逐格试。上限仍是 max_chars，不能
    # 为了均衡冒溢出风险。
    best = lines
    for limit in range(max(-(-len(t) // n), max(len(u) for u in units)), max_chars + 1):
        cand = greedy(limit)
        if len(cand) <= n:
            best = cand
            break
    return best


def normalize_zh(t: str) -> str:
    """中文字幕在折行前过一遍标点规范化。

    step8 的 clean_title 只管标题，字幕这条路径从来没规范化过：模型译文里
    的半角引号会原样烧进画面，成片上出现 `"这是我听过的最烂的主意"` 这种
    中文里夹半角引号的排版。platform_rules 已经有现成规则，直接复用。
    缺依赖时原样返回，不能让字幕烧录因为一个标点模块挂掉。
    """
    if not t:
        return t
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from platform_rules import normalize_cjk_punctuation
    except ImportError:
        return t
    return normalize_cjk_punctuation(t)


def wrap_text(t: str, max_chars: int, is_cjk: bool = False) -> str:
    """自动折行"""
    if not t:
        return ""
    if is_cjk:
        if len(t) <= max_chars:
            return t
        return r"\N".join(_wrap_cjk(t, max_chars))
    else:
        # 英文：按词折行
        if len(t) <= max_chars:
            return t
        words = t.split()
        lines, cur = [], []
        for w in words:
            if sum(len(x) + 1 for x in cur) + len(w) > max_chars:
                lines.append(" ".join(cur))
                cur = [w]
            else:
                cur.append(w)
        if cur:
            lines.append(" ".join(cur))
        return r"\N".join(lines)


def make_ass(entries: list, ass_path: str, video_width: int = 640, video_height: int = 346,
             sub_mode: str = "bilingual", avoid_top_ratio: float | None = None,
             zh_margin_v: int | None = None):
    """
    生成 ASS 字幕：英文在上，中文在下。
    按实际分辨率设置 PlayRes，字号按比例适配。

    sub_mode:
        bilingual —— 中英双语（原片无硬字幕时）
        zh_only   —— 只烧中文（原片已有英文硬字幕，避免英文重复堆叠）
        en_only   —— 只烧英文（原片已有中文硬字幕，补一层英文即成双语）

    avoid_top_ratio:
        原片硬字幕顶部在画面高度的占比（0~1）。传入后，新字幕会被
        抬到该位置之上，不去遮挡原字幕。

    zh_margin_v:
        中文行距底边的绝对像素。调用方已经量过源片硬字幕带的位置时用它钉死
        摆位，比例推算出的安全区和 0.55 高度封顶都不再参与。
    """
    # 参考 1920×1080：EN=36, ZH=46 → 640×346 对应 ~12, ~15
    # 实测感觉太小，用 20 / 26
    en_size = max(14, int(video_width * 20 / 640))
    zh_size = max(18, int(video_width * 26 / 640))
    en_outline = max(2, round(en_size / 12))
    zh_outline = max(2, round(zh_size / 12))
    en_shadow = max(1, round(en_size / 26))
    zh_shadow = max(1, round(zh_size / 26))
    # 底边距按画面高度按比例计算，不能写死绝对像素：
    #   竖屏(9:16)上传到抖音/B站后，底部约 15% 会被文案、账号、
    #   右侧按钮栏等平台 UI 遮挡，字幕必须抬高到安全区以上。
    is_vertical = video_height > video_width
    safe_ratio = 0.16 if is_vertical else 0.06
    margin_bottom_zh = max(12, int(video_height * safe_ratio))   # 中文距底边

    # 原片已有硬字幕时，把我们的字幕整体抬到它上面。
    # MarginV 是“距底边”，所以需要的余量 = 画面高 × (1 - 字幕顶部占比) + 间隙。
    if avoid_top_ratio is not None and 0 < avoid_top_ratio < 1:
        gap = max(8, int(video_height * 0.02))
        needed = int(video_height * (1.0 - avoid_top_ratio)) + gap
        if needed > margin_bottom_zh:
            margin_bottom_zh = needed

    if zh_margin_v is not None:
        margin_bottom_zh = max(0, zh_margin_v)

    # 中文一行的实际占高（含行距），用来给英文行让位
    zh_line_h = int(zh_size * 1.25)
    margin_bottom_en = margin_bottom_zh + zh_line_h * 2 + 8       # 英文在中文上方

    # 抬得太高会顶到画面中心甚至遮住人脸，封顶在 55% 高度处
    cap = int(video_height * 0.55)
    if margin_bottom_en > cap:
        margin_bottom_en = cap
        if zh_margin_v is None:
            margin_bottom_zh = min(margin_bottom_zh, max(12, cap - zh_line_h * 2 - 8))

    # Dialogue 的 MarginV=0 表示沿用样式值。钉死摆位时逐条写出来，抽帧核对
    # 叠字问题时能直接从 ASS 上读出每一条落在哪，不用回去反推样式。
    zh_dialogue_margin = margin_bottom_zh if zh_margin_v is not None else 0

    # 英文折行最大字符数（字体约 en_size/2 px 宽）
    en_wrap = max(20, int(video_width * 38 / 640))
    zh_wrap = max(10, int(video_width * 16 / 640))

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {video_width}",
        f"PlayResY: {video_height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Alignment=2：底部居中；MarginV 控制距底边距离
        # 描边和阴影必须跟着字号缩放。原来写死 Outline=1、Shadow=0，在 1080
        # 宽的竖屏画布上只有 1 像素——抽帧核对时发现，遇到白色 PPT/图表这类
        # 亮背景，白字配 1px 黑边直接糊成一片，整句读不出来。现在描边按字号
        # 的 1/12 走并补一层投影，亮底暗底都能压住。
        f"Style: EN,Arial,{en_size},&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,"
        f"-1,0,0,0,100,100,0,0,1,{en_outline},{en_shadow},2,10,10,{margin_bottom_en},1",
        f"Style: ZH,Noto Sans CJK SC,{zh_size},&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,"
        f"-1,0,0,0,100,100,0,0,1,{zh_outline},{zh_shadow},2,10,10,{margin_bottom_zh},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for e in entries:
        s = e["start_sec"]
        end = e["end_sec"]
        en_t = wrap_text(e.get("en", "").strip(), en_wrap, is_cjk=False)
        zh_t = wrap_text(normalize_zh(e.get("zh", "").strip()), zh_wrap, is_cjk=True)
        # 英文行按这一条中文实际折了几行来让位。样式里的 MarginV 只能按固定
        # 行数算，两头都出问题：折到 3 行时（长句里没有可断的标点就会发生，
        # 实测 56 字一条）中文块往上顶穿英文行，「英文在上中文在下」的版式
        # 反过来；只有 1 行时又白留一行的空，英文被顶到画面中段骑在讲者
        # 下巴上。ASS 的 Dialogue 支持逐条覆盖 MarginV，按实际行数算。
        if sub_mode == "en_only":
            zh_t = ""
        zh_lines = zh_t.count(r"\N") + 1 if zh_t else 0
        en_margin = margin_bottom_en
        if zh_lines:
            en_margin = min(cap, margin_bottom_zh + zh_line_h * zh_lines + 8)
        elif sub_mode == "en_only":
            # 没有中文行时英文就该占最底下那一排。仍按 margin_bottom_en 摆
            # 会在字幕与画面底边之间空出两行高的洞，英文孤零零悬在讲者胸口。
            en_margin = margin_bottom_zh
        # zh_only：原片已有英文硬字幕，再烧一遍英文只会重复且拥挤
        if en_t and sub_mode != "zh_only":
            lines.append(
                f"Dialogue: 0,{sec2ass(s)},{sec2ass(end)},EN,,0,0,{en_margin},,{en_t}")
        if zh_t:
            lines.append(
                f"Dialogue: 0,{sec2ass(s)},{sec2ass(end)},ZH,,0,0,{zh_dialogue_margin},,{zh_t}")

    with open(ass_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))


# ── 主流程 ────────────────────────────────────────────────────────────────────

def run(cmd: list):
    print(f"[run] {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run(cmd, check=True)


def get_video_size(video: str):
    """用 ffmpeg 获取视频分辨率"""
    r = subprocess.run(
        ["ffmpeg", "-i", video],
        capture_output=True, text=True
    )
    m = re.search(r"(\d{3,5})x(\d{3,5})", r.stderr + r.stdout)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 640, 346


def main():
    parser = argparse.ArgumentParser(description="按 manifest 切片并烧录双语字幕")
    parser.add_argument("--video", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--srt", required=True, help="完整 SRT（与视频语言一致）")
    parser.add_argument("--bilingual", default=None, help="完整双语 JSON（translate.py 输出）")
    parser.add_argument("--output-dir", default="./clips")
    parser.add_argument("--srt-lang", default="zh", choices=["zh", "en"],
                        help="SRT 里的语言：zh（中文视频）或 en（英文视频）")
    parser.add_argument("--no-subtitle", action="store_true")
    parser.add_argument("--sub-mode", default="bilingual",
                        choices=["bilingual", "zh_only"],
                        help="bilingual=烧中英双语；zh_only=只烧中文（原片已有英文硬字幕）")
    parser.add_argument("--avoid-top-ratio", type=float, default=None,
                        help="原片硬字幕顶部在画面高度的占比，传入后新字幕会抬到其上方避让")
    parser.add_argument("--auto-lang", action="store_true", default=True,
                        help="根据 SRT 实际内容校正 --srt-lang（默认开启）")
    parser.add_argument("--vertical", action="store_true",
                        help="输出竖屏 9:16（1080×1920，适配手机端短视频）；原视频居中，上下模糊背景填充")
    parser.add_argument("--vertical-size", default="1080x1920",
                        help="竖屏画布尺寸，默认 1080x1920")
    args = parser.parse_args()

    if args.auto_lang:
        detected = detect_srt_lang(args.srt)
        if detected and detected != args.srt_lang:
            print(f"[clip] ⚠️  --srt-lang 声明为 {args.srt_lang}，但 SRT 内容看起来是 "
                  f"{detected}，已自动改按 {detected} 处理（否则两行字幕会都是同一种语言）",
                  file=sys.stderr)
            args.srt_lang = detected

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "_tmp"
    tmp_dir.mkdir(exist_ok=True)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    # 获取视频分辨率
    vw, vh = get_video_size(args.video)
    print(f"[clip] 视频分辨率: {vw}×{vh}", flush=True)

    # 竖屏画布尺寸（字幕按此尺寸适配）
    if args.vertical:
        cw, ch = map(int, args.vertical_size.split("x"))
        print(f"[clip] 竖屏模式，画布: {cw}×{ch}", flush=True)
    else:
        cw, ch = vw, vh
    print(f"[clip] 共 {len(manifest)} 条待切片", flush=True)

    results = []
    for item in manifest:
        rank = item["rank"]
        title = item.get("title", f"clip_{rank}")
        safe = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40]
        start = item["clip_start_sec"]
        end = item["clip_end_sec"]
        start_hms = item["clip_start"]
        end_hms = item["clip_end"]

        print(f"\n--- [{rank:02d}] {title} ({start_hms}~{end_hms}) ---", flush=True)

        clip_video = str(tmp_dir / f"{rank:02d}_raw.mp4")
        out_mp4 = str(out_dir / f"{rank:02d}_{safe}.mp4")

        # 切割片段（用秒数传给 ffmpeg，避免 SRT 的逗号时间戳格式不兼容）
        run(["ffmpeg", "-y", "-i", args.video,
             "-ss", f"{float(start):.3f}", "-to", f"{float(end):.3f}",
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", clip_video])

        # 竖屏转换滤镜：原视频等比缩放到画布宽，居中；背景用放大模糊的自身填充
        def vertical_vf(sub_filter: str = "") -> str:
            bg = f"scale={cw}:{ch}:force_original_aspect_ratio=increase,crop={cw}:{ch},boxblur=20:5"
            fg = f"scale={cw}:-2:force_original_aspect_ratio=decrease"
            base = (f"[0:v]{bg}[bg];"
                    f"[0:v]{fg}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
            if sub_filter:
                return base + f";[v]{sub_filter}[vout]"
            return base + ";[v]null[vout]"

        if args.no_subtitle:
            if args.vertical:
                run(["ffmpeg", "-y", "-i", clip_video,
                     "-filter_complex", vertical_vf(),
                     "-map", "[vout]", "-map", "0:a?",
                     "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                     "-c:a", "aac", "-b:a", "128k", out_mp4])
            else:
                import shutil
                shutil.copy(clip_video, out_mp4)
        else:
            entries = srt_filter(args.srt, start, end, args.srt_lang)
            if args.bilingual:
                entries = merge_translation(entries, args.bilingual, start, args.srt_lang)

            ass_path = str(tmp_dir / f"{rank:02d}.ass")
            # 字幕按最终画布尺寸生成（竖屏时用 cw×ch）
            make_ass(entries, ass_path, cw, ch,
                     sub_mode=args.sub_mode, avoid_top_ratio=args.avoid_top_ratio)
            ass_esc = ass_path.replace("\\", "/").replace(":", "\\:")

            if args.vertical:
                run(["ffmpeg", "-y", "-i", clip_video,
                     "-filter_complex", vertical_vf(f"ass={ass_esc}"),
                     "-map", "[vout]", "-map", "0:a?",
                     "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                     "-c:a", "aac", "-b:a", "128k", out_mp4])
            else:
                run(["ffmpeg", "-y", "-i", clip_video,
                     "-vf", f"ass={ass_path}",
                     "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                     "-c:a", "aac", "-b:a", "128k", out_mp4])

        size = Path(out_mp4).stat().st_size / 1024 / 1024
        print(f"✅ [{rank:02d}] → {out_mp4} ({size:.1f}MB)", flush=True)
        results.append({**item, "output_file": str(out_mp4), "size_mb": round(size, 1)})

    print(f"\n=== 全部切片完成 ===", flush=True)
    for r in results:
        print(f"  [{r['rank']:02d}] {r['title']} ({r['size_mb']}MB)", flush=True)


if __name__ == "__main__":
    main()
