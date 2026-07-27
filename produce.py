#!/usr/bin/env python3
"""一键出片：视频源 → ``deliver/<slug>/{final.mp4, cover_*.jpg, meta.json}``。

    python produce.py --source <URL 或本地路径> --slug <output-slug>

流水线共八步：取源 → 转写 → 挑金句 → 标题 → 封面 → 翻译 → 拼片烧字幕 → manifest。
所有 LLM/VLM 调用都走 SiliconFlow（``scripts/sf_transport``，curl 子进程），
外面包一层 ``scripts/sf_client``（内容寻址缓存 + 分类重试）。

**封面排在翻译之前**是刻意的：翻译是整条流水线的主要开销，而封面选帧是最容易
判死刑的一关。封面放在最后时，一次「挑不出合格封面」意味着翻译的钱已经全花完、
成片也白烧了（CI run 30259127265、30260691746 就是这么各白跑一趟的）。把会拒的
关卡提到花钱的关卡前面，失败就只赔掉几次几何预筛和一次 VLM 打分。

退出码
------
0   成功
1   参数 / 配置错误（含 SiliconFlow 400/401/402/403：改密钥或充值，重跑没用）
2   内容质量不达标（金句或封面达不到阈值，拒绝硬出）
3   外部依赖失败（SiliconFlow 限流/5xx 重试耗尽、yt-dlp 下载失败、ffmpeg 崩了）
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for _sub in ("scripts", "steps"):
    sys.path.insert(0, str(ROOT / _sub))

import hardsub_probe as HP                   # noqa: E402
import highlight as HL                       # noqa: E402
import platform_rules as PR                  # noqa: E402
import sf_client                             # noqa: E402
import step7_cover as COVER                  # noqa: E402
import translate as TR                       # noqa: E402
from clip import make_ass, srt_filter        # noqa: E402

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_QUALITY = 2
EXIT_API = 3

# --translator 的取值 → 真实模型名。DeepSeek-V3 是默认路径；Claude 保留但
# 需要 ANTHROPIC_API_KEY，只在本地手动跑，CI 里不注入。
TRANSLATORS = {
    "deepseek-v3": "deepseek-ai/DeepSeek-V3",
    "claude-sonnet-4.6": "claude-sonnet-4-6",
}
DEFAULT_TRANSLATOR = "deepseek-v3"
VISION_MODEL = "Qwen/Qwen3-VL-8B-Instruct"
HIGHLIGHT_MODEL = "Qwen/Qwen3-8B"
WHISPER_MODEL = "base"          # large-v3 在 CI runner 上太慢
SEGMENTS = 3                    # 成片由三段金句拼成
TITLE_MAX_CHARS = 15

# --cover-crop 的取值格式，与 ffmpeg crop 滤镜一致：W:H:X:Y
COVER_CROP_RE = re.compile(r"^\d+:\d+:\d+:\d+$")

# 阶段顺序。会打进日志、写进 --help，改这里就同步改了两处
STAGE_ORDER = ("input", "transcribe", "highlight", "title", "cover",
               "translate", "assemble", "manifest")
STAGE_ORDER_TEXT = " → ".join(STAGE_ORDER)

# LLM 响应缓存默认落在仓库根，跟 _tmp/ 分开：_tmp/ 是一次跑的中间产物，
# 缓存是跨次复用的资产，CI 上由 actions/cache 单独挂
DEFAULT_LLM_CACHE_DIR = ROOT / ".llm_cache"

# --sub-mode 的取值 → scripts/clip.make_ass 的 sub_mode
SUB_MODES = {"both": "bilingual", "zh-only": "zh_only"}
DEFAULT_SUB_MODE = "both"
# zh-only 时中文行距底边的默认像素。854x480 的芒格源片自带英文硬字幕落在
# y=408~456，上沿距底边 480-408=72px，再留 24px 间隙让中文不贴着它 → 96。
DEFAULT_SUB_MARGIN_V = 96
# --sub-margin-v 的自动挡：逐条 cue 探测源片硬字幕带，各自算摆位。
SUB_MARGIN_V_AUTO = "auto"
DEFAULT_SUB_MARGIN_V_ARG = SUB_MARGIN_V_AUTO
# 中文块底边与源片硬字幕带上沿之间留的间隙。24 就是 96 那个默认值的来源
# （480 - 408 + 24），自动挡沿用同一个手感。
DEFAULT_SUB_AVOID_GAP = 24


# ── 基础设施 ──────────────────────────────────────────────────────────────────

def log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)


def die(code: int, stage: str, reason: str, **fields) -> None:
    """结构化失败退出。质量问题退 2，外部依赖退 3，配置问题退 1。"""
    payload = {"stage": stage, "reason": reason, **fields}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
    sys.exit(code)


def run(cmd: list, stage: str) -> None:
    """跑外部命令；非零退出一律算外部依赖失败（退 3）。"""
    log("run", " ".join(str(c) for c in cmd))
    try:
        subprocess.run([str(c) for c in cmd], check=True)
    except FileNotFoundError:
        die(EXIT_CONFIG, stage, "command_not_found", command=str(cmd[0]))
    except subprocess.CalledProcessError as e:
        die(EXIT_API, stage, "command_failed",
            command=str(cmd[0]), returncode=e.returncode)


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def probe_size(path: Path) -> tuple[int, int]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True)
    m = re.search(r"(\d+)x(\d+)", r.stdout)
    return (int(m.group(1)), int(m.group(2))) if m else (854, 480)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_url(source: str) -> bool:
    return bool(re.match(r"^(https?|ftp)://", source))


# ── 1. 取源 ───────────────────────────────────────────────────────────────────

DEFAULT_DOWNLOAD_RETRIES = 3
DEFAULT_DOWNLOAD_BACKOFF_SEC = 2.0
MAX_DOWNLOAD_BACKOFF_SEC = 60.0

# yt-dlp 自己的重试。分片级的抖动它自己就能吞掉，不必每次都退到外层从头下
YTDLP_RETRIES = 5
YTDLP_FRAGMENT_RETRIES = 5

# URL 本身就不对。重试一百次也还是不对，而且真实原因会被淹在重试日志里
DOWNLOAD_BAD_URL_RE = re.compile(
    r"is not a valid URL|Unsupported URL|Invalid URL|unsupported url",
    re.IGNORECASE)
# 资源确实没了。和「URL 写错了」不同，这是外部资源的问题，但同样重试无益
DOWNLOAD_GONE_RE = re.compile(
    r"HTTP Error 40[0134]\b|Video unavailable|This video is private|"
    r"has been removed|does not exist|members-only|Sign in to confirm",
    re.IGNORECASE)
# 值得重试：5xx、超时、连接被重置。archive.org 的 500 就是这一类
DOWNLOAD_RETRYABLE_RE = re.compile(
    r"HTTP Error 5\d\d\b|timed out|timeout|Connection reset|Connection refused|"
    r"Temporary failure|Remote end closed|IncompleteRead|"
    r"Connection aborted|Read timed out|Unable to connect",
    re.IGNORECASE)

# 打桩点：测试把它换掉，别真睡
_sleep = time.sleep


def classify_download_failure(output: str):
    """把 yt-dlp 的输出分成 ``("config" | "gone" | "retry", reason)``。

    认不出来的一律当抖动重试：yt-dlp 的报错面太广，穷举不现实，而多试两次的
    代价远小于把一次本可以成功的无人值守跑白白丢掉。
    """
    text = output or ""
    if DOWNLOAD_BAD_URL_RE.search(text):
        return "config", "unsupported_url"
    if DOWNLOAD_GONE_RE.search(text):
        return "gone", "source_unavailable"
    if DOWNLOAD_RETRYABLE_RE.search(text):
        return "retry", "transient_download_error"
    return "retry", "download_failed"


def download_source(source: str, out: Path, attempts: int,
                    backoff: float) -> None:
    """yt-dlp 下载，带分类重试。

    CI run 30263087066 就挂在这里：archive.org 返 ``HTTP Error 500``，退 3，
    人手重跑一次就过了 —— 无人值守时这等于白跑一趟。
    """
    attempts = max(1, int(attempts))
    for attempt in range(1, attempts + 1):
        cmd = ["yt-dlp", "-f", "bv*[height<=720]+ba/b[height<=720]/b",
               "--merge-output-format", "mp4",
               "--retries", str(YTDLP_RETRIES),
               "--fragment-retries", str(YTDLP_FRAGMENT_RETRIES),
               "--no-progress",
               "-o", str(out), source]
        log("input", f"yt-dlp 第 {attempt}/{attempts} 次：{' '.join(cmd)}")
        try:
            p = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            die(EXIT_CONFIG, "input", "yt_dlp_not_installed", source=source)

        combined = f"{p.stdout or ''}\n{p.stderr or ''}"
        if p.stdout:
            print(p.stdout, end="", flush=True)
        if p.returncode == 0 and out.is_file():
            return
        if p.stderr:
            print(p.stderr, end="", file=sys.stderr, flush=True)

        if p.returncode == 0:
            # 退 0 却没产出文件，重试也不会凭空变出来
            die(EXIT_API, "input", "download_produced_no_file", source=source,
                attempts=attempt)

        verdict, reason = classify_download_failure(combined)
        if verdict == "config":
            die(EXIT_CONFIG, "input", reason, source=source,
                returncode=p.returncode, attempts=attempt,
                detail="URL 格式不被 yt-dlp 支持，重试无益")
        if verdict == "gone":
            die(EXIT_API, "input", reason, source=source,
                returncode=p.returncode, attempts=attempt,
                detail="片源不存在或不可访问（404/私有/已下架），重试无益")
        if attempt >= attempts:
            die(EXIT_API, "input", reason, source=source,
                returncode=p.returncode, attempts=attempt,
                detail=f"下载重试 {attempts} 次仍失败")

        wait = min(backoff * (2 ** (attempt - 1)), MAX_DOWNLOAD_BACKOFF_SEC)
        log("input", f"下载失败（{reason}，rc={p.returncode}），{wait:.0f}s 后重试")
        _sleep(wait)


def resolve_source(source: str, work: Path,
                   attempts: int = DEFAULT_DOWNLOAD_RETRIES,
                   backoff: float = DEFAULT_DOWNLOAD_BACKOFF_SEC) -> Path:
    """URL 走 yt-dlp，``file://`` 和本地路径直接用。"""
    if source.startswith("file://"):
        source = source[len("file://"):]

    if not is_url(source):
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            die(EXIT_CONFIG, "input", "source_not_found", source=str(path))
        log("input", f"本地片源 {path}")
        return path

    if shutil.which("yt-dlp") is None:
        die(EXIT_CONFIG, "input", "yt_dlp_not_installed", source=source)

    out = work / "source.mp4"
    log("input", f"yt-dlp 下载 {source}")
    download_source(source, out, attempts, backoff)
    if not out.is_file():
        die(EXIT_API, "input", "download_produced_no_file", source=source)
    return out


# ── 2. 转写 ───────────────────────────────────────────────────────────────────

def transcribe(video: Path, work: Path, language: str) -> Path:
    """本地 faster-whisper 转写成 SRT。已存在就复用，方便重跑。"""
    srt = work / "transcript.srt"
    if srt.is_file() and srt.stat().st_size:
        log("transcribe", f"复用已有转写 {srt}")
        return srt
    log("transcribe", f"faster-whisper（{WHISPER_MODEL}）转写中…")
    run([sys.executable, str(ROOT / "scripts" / "transcribe.py"),
         "--input", video, "--output", srt,
         "--model", WHISPER_MODEL, "--language", language], "transcribe")
    if not srt.is_file():
        die(EXIT_API, "transcribe", "no_transcript_produced")
    return srt


# ── 3. 挑金句 ─────────────────────────────────────────────────────────────────

def pick_quotes(srt: Path, work: Path, speaker: str, total_dur: float,
                api_key: str, base_url: str, strict: bool) -> list:
    """LLM 打分挑金句 → 切点对齐 → 过出片门槛。不达标由 highlight 退 2。"""
    entries = HL.parse_srt(str(srt))
    if not entries:
        die(EXIT_QUALITY, "highlight", "empty_transcript", srt=str(srt))

    paragraphs = HL.merge_into_paragraphs(entries, gap_sec=3.0, max_sec=90.0)
    log("highlight", f"{len(entries)} 条字幕 → {len(paragraphs)} 个段落")

    quotes = HL.score_highlights(paragraphs, api_key, HIGHLIGHT_MODEL,
                                 base_url, speaker=speaker, top_n=10)
    quotes = HL.align_clips(quotes, entries, total_dur)
    quotes = HL.enforce_quote_thresholds(quotes, want=SEGMENTS, strict=strict)

    out = work / "quotes.json"
    out.write_text(json.dumps(quotes, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    total = sum(q["clip_duration_sec"] for q in quotes)
    log("highlight", f"选中 {len(quotes)} 段，合计 {total:.0f}s → {out}")
    return quotes


# ── 4. 翻译 ───────────────────────────────────────────────────────────────────

def translate_windows(srt: Path, quotes: list, work: Path, translator: str,
                      api_key: str, base_url: str) -> Path:
    """只翻选中窗口内的字幕，逐条跑中文标点归一化。

    整片全翻在 3 分钟成片里是纯浪费 —— 只有落进金句窗口的那些条目会被烧进画面。
    """
    entries = HL.parse_srt(str(srt))
    windows = [(q["clip_start_sec"], q["clip_end_sec"]) for q in quotes]

    def inside(e: dict) -> bool:
        return any(e["end_sec"] > s and e["start_sec"] < t for s, t in windows)

    picked = [e for e in entries if inside(e)]
    if not picked:
        die(EXIT_QUALITY, "translate", "no_cues_in_selected_windows")

    model = TRANSLATORS[translator]
    log("translate", f"{translator}（{model}）翻译 {len(picked)} 条字幕")

    if translator == "claude-sonnet-4.6":
        zh_list = _translate_claude([e["text"] for e in picked], model)
    else:
        try:
            zh_list = TR.translate_all([e["text"] for e in picked], api_key,
                                       model, base_url, direction="en2zh")
        except sf_client.SFFatalError:
            # 鉴权 / 余额 / 请求本身有问题：交给 main 按 http_status 分类退出，
            # 不要笼统报成「SiliconFlow 不可用」把真实原因盖掉
            raise
        except RuntimeError as e:
            die(EXIT_API, "translate", "siliconflow_unavailable", detail=str(e))

    bilingual = []
    for e, zh in zip(picked, zh_list):
        bilingual.append({
            "index": e["index"],
            "start": e["start"],
            "end": e["end"],
            # 外层『』在这里被归一化成「」—— DeepSeek-V3 的译文会直接产出
            # 「他只说『不行』」，不过这一道就会烧进画面。
            "zh": PR.normalize_cjk_punctuation(PR.to_simplified((zh or "").strip())),
            "en": e["text"],
        })

    out = work / f"quotes_zh.{translator}.json"
    out.write_text(json.dumps(bilingual, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    log("translate", f"→ {out}")
    return out


def _translate_claude(texts: list, model: str) -> list:
    """Claude 路径：保留但不是默认，需要 ANTHROPIC_API_KEY，CI 里不注入。"""
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        die(EXIT_CONFIG, "translate", "missing_anthropic_api_key",
            detail="--translator claude-sonnet-4.6 需要 ANTHROPIC_API_KEY")
    try:
        import anthropic
    except ImportError:
        die(EXIT_CONFIG, "translate", "anthropic_sdk_not_installed",
            detail="pip install anthropic")

    client = anthropic.Anthropic(api_key=key)
    numbered = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
    prompt = ("把下列英文字幕逐条翻译成简体中文，保持条数和编号一致，"
              "每行一条，只输出「编号. 译文」，不要任何解释。\n"
              "中文引号一律用直角引号，外层「」内层『』。\n\n" + numbered)
    try:
        resp = client.messages.create(
            model=model, max_tokens=8000,
            messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        die(EXIT_API, "translate", "anthropic_call_failed", detail=str(e))

    body = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    got = {}
    for line in body.splitlines():
        m = re.match(r"\s*(\d+)[.、)]\s*(.+)", line)
        if m:
            got[int(m.group(1))] = m.group(2).strip()
    return [got.get(i + 1, "") for i in range(len(texts))]


# ── 5. 拼片 ───────────────────────────────────────────────────────────────────

def auto_margin_v(video_height: int, hardsub_top_y: int | None,
                  gap: int = DEFAULT_SUB_AVOID_GAP) -> int:
    """把探到的硬字幕带上沿翻译成 MarginV（距底边像素）。探不到就回落到默认值。"""
    if hardsub_top_y is None:
        return DEFAULT_SUB_MARGIN_V
    return video_height - hardsub_top_y + gap


def plan_cue_placements(video: Path, video_height: int, cues: list,
                        clip_start: float, gap: int, tmp_dir: Path,
                        first_index: int = 1) -> list:
    """逐条 cue 探测源片硬字幕带，算出各自的 MarginV。

    cue 的时间戳是切片内相对时间，探测要回到源片，所以统一加 ``clip_start``。

    抬得过头一律不硬出：中文块底边跑到画面上半部分，说明要么源片硬字幕异常
    高，要么检测把画面主体当成了文字。这时候烧出来的成片只会是字幕骑在讲者
    脸上，按退出码约定退 2 让人来看，比闷头出一条废片强。
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    placements = []
    for offset, c in enumerate(cues):
        idx = first_index + offset
        a = clip_start + c["start_sec"]
        b = clip_start + c["end_sec"]
        top_y = HP.probe_cue_band_top(str(video), a, b, str(tmp_dir),
                                      prefix=f"cue{idx:03d}")
        margin_v = auto_margin_v(video_height, top_y, gap)
        if margin_v > video_height // 2:
            die(EXIT_QUALITY, "assemble", "auto_sub_margin_v_above_midline",
                detail="自动避让算出的 MarginV 会把中文顶到画面上半部分，拒绝硬出。"
                       "改用固定的 --sub-margin-v 指定摆位，或确认源片硬字幕带位置",
                cue_index=idx,
                cue_source_start_sec=round(a, 2),
                cue_source_end_sec=round(b, 2),
                hardsub_top_y=top_y,
                margin_v=margin_v,
                video_height=video_height,
                sub_avoid_gap=gap)
        placements.append({
            "cue_index": idx,
            "source_start_sec": round(a, 2),
            "source_end_sec": round(b, 2),
            "hardsub_top_y": top_y,
            "margin_v": margin_v,
            "fallback": top_y is None,
        })
        if top_y is None:
            log("assemble", f"cue#{idx} {a:.1f}~{b:.1f}s 未探到硬字幕带，"
                            f"回落到默认 MarginV={margin_v}")
        else:
            log("assemble", f"cue#{idx} {a:.1f}~{b:.1f}s 硬字幕带上沿 y={top_y}"
                            f" → MarginV={margin_v}（间隙 {gap}）")
    return placements


def assemble(video: Path, srt: Path, bilingual: Path, quotes: list,
             work: Path, out_path: Path, sub_mode: str = DEFAULT_SUB_MODE,
             sub_margin_v: int | str = DEFAULT_SUB_MARGIN_V,
             sub_avoid_gap: int = DEFAULT_SUB_AVOID_GAP) -> tuple:
    """切三段 → 各自烧字幕 → concat 成 final.mp4。返回 ``(segs, placements)``。

    ``sub_mode='zh-only'`` 时只烧中文，并把它抬到源片硬字幕带上方：源片自带的
    英文硬字幕直接当英文轨用，不再叠自己的一层。

    ``sub_margin_v='auto'`` 逐条 cue 探测硬字幕带位置各自摆位；给整数则全片
    钉死在那个值上。
    """
    w, h = probe_size(video)
    auto = sub_mode == "zh-only" and sub_margin_v == SUB_MARGIN_V_AUTO
    seg_dir = work / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    bi = json.loads(bilingual.read_text(encoding="utf-8"))
    zh_by_index = {e["index"]: e.get("zh", "") for e in bi}

    segs, parts, placements = [], [], []
    for i, q in enumerate(quotes, 1):
        start, end = q["clip_start_sec"], q["clip_end_sec"]
        raw = seg_dir / f"seg{i:02d}_raw.mp4"
        run(["ffmpeg", "-y", "-i", video, "-ss", f"{start:.3f}",
             "-t", f"{end - start:.3f}",
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", "-b:a", "128k", raw], "assemble")

        cues = srt_filter(str(srt), start, end, srt_lang="en")
        for c in cues:
            c["zh"] = zh_by_index.get(c.get("orig_index"), "")

        if auto:
            seg_placements = plan_cue_placements(
                video, h, cues, start, sub_avoid_gap,
                seg_dir / f"seg{i:02d}_probe", first_index=len(placements) + 1)
            for c, p in zip(cues, seg_placements):
                c["zh_margin_v"] = p["margin_v"]
            placements.extend(seg_placements)

        ass = seg_dir / f"seg{i:02d}.ass"
        make_ass(cues, str(ass), video_width=w, video_height=h,
                 sub_mode=SUB_MODES[sub_mode],
                 zh_margin_v=(DEFAULT_SUB_MARGIN_V if auto else
                              sub_margin_v if sub_mode == "zh-only" else None))

        burned = seg_dir / f"seg{i:02d}.mp4"
        run(["ffmpeg", "-y", "-i", raw, "-vf", f"ass={ass}",
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "copy", burned], "assemble")

        parts.append(burned)
        segs.append({
            "index": i,
            "rank": q.get("rank", i),
            "score": q.get("score"),
            "source_start_sec": round(start, 2),
            "source_end_sec": round(end, 2),
            "duration_sec": round(end - start, 2),
            "cues": len(cues),
            "title_suggestion": q.get("title_suggestion", ""),
            "reason": q.get("reason", ""),
        })

    listing = seg_dir / "concat.txt"
    listing.write_text("".join(f"file '{p.resolve()}'\n" for p in parts),
                       encoding="utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing,
         "-c", "copy", out_path], "assemble")
    log("assemble", f"→ {out_path}")
    return segs, placements


# ── 6. 封面 ───────────────────────────────────────────────────────────────────

def make_covers(video: Path, quotes: list, title: str, speaker: str,
                out_dir: Path, work: Path, api_key: str,
                no_vlm: bool, cover_time_sec: float | None = None,
                candidates: int = COVER.DEFAULT_COVER_CANDIDATES,
                cover_crop: str | None = None) -> dict:
    """选帧 + 出 16:9 / 9:16 两张封面。挑不出合格帧时由 cover 侧退 2。

    有些片源天生挑不出合格封面 —— 解说式剪辑用的是原声 + 素材空镜，全片
    没有主讲人正脸，自动选帧只会挑到不相干的素材人物，冒充主讲人属于误导。
    ``cover_time_sec`` 就是给这种源片的人工出口：钉死一个时间点，人脸预筛
    和 VLM 校验全部跳过。

    ``cover_crop`` 切掉源片底部烧死的英文硬字幕，选帧和出图共用同一个裁切。
    """
    report: dict = {"cover_crop": cover_crop}
    tmp = work / "cover_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    q = quotes[0]

    if cover_crop:
        log("cover", f"封面裁切 crop={cover_crop}（切掉源片烧死的硬字幕区）")

    if cover_time_sec is not None:
        frame = str(tmp / "manual_cover.jpg")
        if not COVER.extract_frame(str(video), cover_time_sec, frame, cover_crop):
            die(EXIT_CONFIG, "cover", "manual_frame_extract_failed",
                detail="--cover-time-sec 指定的时间点截不出帧，请确认它在片长范围内",
                cover_time_sec=cover_time_sec)
        log("cover", f"手动指定封面帧 t={cover_time_sec:.1f}s（跳过人脸预筛与 VLM 校验）")
        report["cover_source"] = "manual"
        report["cover_time_sec"] = round(cover_time_sec, 2)
        report["cover_vlm_passed"] = False
    elif no_vlm:
        report["cover_source"] = "auto"
        frame = COVER.pick_best_frame_geometric(
            raw_video=str(video), clip_start_sec=q["clip_start_sec"],
            clip_end_sec=q["clip_end_sec"], tmp_dir=str(tmp),
            candidates=candidates, report=report, crop=cover_crop)
    else:
        report["cover_source"] = "auto"
        if not api_key:
            die(EXIT_CONFIG, "cover", "missing_siliconflow_api_key",
                detail="VLM 校验需要 SILICONFLOW_API_KEY，或改用 --no-vlm")
        try:
            frame = COVER.pick_best_frame_vision(
                raw_video=str(video), clip_start_sec=q["clip_start_sec"],
                clip_end_sec=q["clip_end_sec"], speaker=speaker,
                api_key=api_key, vision_model=VISION_MODEL,
                tmp_dir=str(tmp), candidates=candidates, report=report,
                crop=cover_crop)
        except sf_client.SFFatalError:
            raise
        except RuntimeError as e:
            die(EXIT_API, "cover", "vision_call_failed", detail=str(e))
        if not frame:
            rejections = report.get("cover_vlm_rejections", [])
            die(EXIT_QUALITY, "cover", "no_frame_passed_vlm",
                detail="没有满足条件的封面帧",
                candidates_evaluated=len(rejections),
                best_score=max((r.get("cover_score", 0) for r in rejections),
                               default=0),
                rejections=rejections[:20],
                hint=COVER.NO_COVER_HINT)

    out_dir.mkdir(parents=True, exist_ok=True)
    covers = {}
    for name, size in (("cover_16x9.jpg", (1280, 720)),
                       ("cover_9x16.jpg", (1080, 1920))):
        path = out_dir / name
        COVER.make_cover(frame, title, speaker, str(path), size)
        covers[name] = path
        log("cover", f"→ {path}")

    report["cover_source_frame"] = os.path.basename(frame)
    report.setdefault("cover_vlm_passed", not no_vlm)
    report.setdefault("cover_vlm_rejections", [])
    report["files"] = covers
    return report


# ── 7. 标题 ───────────────────────────────────────────────────────────────────

def make_title(quotes: list, speaker: str, api_key: str, base_url: str,
               override: str) -> str:
    """15 字以内的成片标题。``--title-override`` 优先，其次问模型，最后退到建议标题。"""
    if override:
        return PR.normalize_cjk_punctuation(override.strip())[:TITLE_MAX_CHARS]

    joined = "\n".join(q.get("transcript_zh") or q.get("transcript_en", "")
                       for q in quotes)[:1500]
    prompt = (f"下面是{speaker}的三段发言。起一个中文标题，"
              f"不超过 {TITLE_MAX_CHARS} 个字，不要书名号，不要引号，"
              f"只输出标题本身。\n\n{joined}")
    try:
        raw = HL.call_llm([{"role": "user", "content": prompt}],
                          api_key, HIGHLIGHT_MODEL, base_url)
    except sf_client.SFFatalError:
        # 密钥错了 / 余额不够不能悄悄退到建议标题：那会让整条片子带着一个
        # 凑合的标题出厂，而真正的问题一声不响
        raise
    except RuntimeError as e:
        log("title", f"模型不可用（{e}），退到金句建议标题")
        raw = quotes[0].get("title_suggestion", "") or speaker

    title = PR.normalize_cjk_punctuation(
        PR.to_simplified(raw.strip().splitlines()[0] if raw.strip() else speaker))
    title = re.sub(r'^[「『"\']+|[」』"\']+$', "", title).strip()
    return title[:TITLE_MAX_CHARS] or speaker


# ── 8. manifest ───────────────────────────────────────────────────────────────

def write_meta(out_dir: Path, slug: str, title: str, source: str,
               final: Path, segs: list, covers: dict, translator: str,
               speaker: str, sub_mode: str = DEFAULT_SUB_MODE,
               sub_margin_v: int | str = DEFAULT_SUB_MARGIN_V,
               sub_avoid_gap: int = DEFAULT_SUB_AVOID_GAP,
               sub_placements: list | None = None) -> Path:
    files = {"final.mp4": final, **covers.get("files", {})}
    meta = {
        "slug": slug,
        "title": title,
        "source_url": source,
        "speaker": speaker,
        "duration_sec": round(probe_duration(final), 2),
        "resolution": "x".join(str(v) for v in probe_size(final)),
        "segments": segs,
        "segment_count": len(segs),
        "sub_mode": sub_mode,
        "sub_margin_v": sub_margin_v,
        "sub_avoid_gap": sub_avoid_gap,
        # auto 挡逐条 cue 的摆位依据，事后核对叠字问题时直接看这里，
        # 不用回去重跑探测
        "sub_placements": sub_placements or [],
        "models": {
            "transcribe": f"faster-whisper/{WHISPER_MODEL}",
            "highlight": HIGHLIGHT_MODEL,
            "translate": TRANSLATORS[translator],
            "translator": translator,
            "vision": None if covers.get("cover_vlm_passed") is False else VISION_MODEL,
        },
        "cover_vlm_passed": bool(covers.get("cover_vlm_passed")),
        "cover_vlm_rejections": covers.get("cover_vlm_rejections", []),
        "cover_source_frame": covers.get("cover_source_frame"),
        "cover_source": covers.get("cover_source", "auto"),
        "cover_time_sec": covers.get("cover_time_sec"),
        "cover_crop": covers.get("cover_crop"),
        "commit": os.environ.get("GITHUB_SHA", "local"),
        "sha256": {name: sha256(p) for name, p in files.items() if Path(p).is_file()},
    }
    path = out_dir / "meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    log("manifest", f"→ {path}")
    return path


def build_compare_grid(finals: dict, out_path: Path) -> None:
    """--dual：把两个译本的同一时刻各截一帧，上下拼成对比图。"""
    from PIL import Image
    shots = []
    for name, path in finals.items():
        shot = out_path.parent / f"_cmp_{name}.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", "3", "-i", str(path),
                        "-vframes", "1", str(shot)],
                       check=False, capture_output=True)
        if shot.is_file():
            shots.append(Image.open(shot))
    if len(shots) < 2:
        log("dual", "截帧不足，跳过对比图")
        return
    w = min(s.width for s in shots)
    resized = [s.resize((w, int(s.height * w / s.width))) for s in shots]
    grid = Image.new("RGB", (w, sum(s.height for s in resized)))
    y = 0
    for s in resized:
        grid.paste(s, (0, y))
        y += s.height
    grid.save(out_path, quality=90)
    log("dual", f"→ {out_path}")


# ── 入口 ──────────────────────────────────────────────────────────────────────

def sub_margin_v_arg(raw: str) -> int | str:
    """``--sub-margin-v`` 接 ``auto`` 或非负整数像素。"""
    s = raw.strip()
    if s == SUB_MARGIN_V_AUTO:
        return SUB_MARGIN_V_AUTO
    if re.fullmatch(r"\d+", s):
        return int(s)
    raise argparse.ArgumentTypeError(
        f"{raw!r} 无效，应为 {SUB_MARGIN_V_AUTO} 或非负整数像素")


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="produce.py",
        description="一键出片：视频源 → deliver/<slug>/{final.mp4, cover_*.jpg, meta.json}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"阶段顺序：{STAGE_ORDER_TEXT}\n"
               f"封面（选帧 + 阈值判定）刻意排在 translate 之前：翻译是主要开销，"
               f"封面不达标要在花钱之前就拒，别等翻译完再白跑一趟。\n"
               f"退出码：0 成功 / 1 配置错误 / 2 内容质量不达标 / 3 外部依赖失败")
    p.add_argument("--source", required=True, help="视频 URL 或本地路径（支持 file://）")
    p.add_argument("--slug", required=True, help="产物目录名")
    p.add_argument("--title-override", default="", help="跳过模型起标题，直接用这个")
    p.add_argument("--translator", default=DEFAULT_TRANSLATOR,
                   choices=sorted(TRANSLATORS), help="翻译模型（默认 deepseek-v3）")
    p.add_argument("--dual", action="store_true",
                   help="两个翻译都跑，额外产出对比拼图")
    p.add_argument("--out", default="deliver", help="产物根目录（默认 deliver/）")
    p.add_argument("--no-vlm", action="store_true",
                   help="封面跳过 VLM 校验，只按几何规则选帧")
    p.add_argument("--cover-candidates", type=int,
                   default=COVER.DEFAULT_COVER_CANDIDATES,
                   help=f"封面候选帧数量，在金句时长上均匀取样，避开首尾各 "
                        f"{COVER.EDGE_MARGIN_SEC:.0f}s"
                        f"（默认 {COVER.DEFAULT_COVER_CANDIDATES}）")
    p.add_argument("--cover-time-sec", type=float, default=None,
                   help="手动指定封面帧时间点（秒），跳过人脸预筛和 VLM 校验。"
                        "用于全片没有主讲人正脸的解说式剪辑/空镜素材片")
    p.add_argument("--cover-crop", default=None, metavar="W:H:X:Y",
                   help="封面选帧时先裁切，格式同 ffmpeg 的 crop 滤镜（W:H:X:Y）。"
                        "用于裁掉源片底部烧死的英文硬字幕等干扰区域，"
                        "例如 854x480 的源片用 854:396:0:0 只保留上方 396px")
    p.add_argument("--sub-mode", default=DEFAULT_SUB_MODE, choices=sorted(SUB_MODES),
                   help="烧字幕的语种：both 中英双语（默认）；zh-only 只烧中文，"
                        "把源片自带的英文硬字幕当英文轨用，避免三层叠字")
    p.add_argument("--sub-margin-v", type=sub_margin_v_arg,
                   default=DEFAULT_SUB_MARGIN_V_ARG, metavar="auto|N",
                   help=f"zh-only 时中文行距底边的像素，用来坐在源片硬字幕带上方。"
                        f"auto（默认）逐条 cue 探测源片该时段硬字幕带的上沿，各自"
                        f"抬到它上方 —— 源片的硬字幕高度并非恒定，大字号引言板明显"
                        f"更高，全片一个固定值必然撞上其中一种。给整数则全片钉死在"
                        f"该值（{DEFAULT_SUB_MARGIN_V} 是 854x480 源片对白带的实测"
                        f"值：上沿 y=408，距底边 72px，再留 24px 间隙）。"
                        f"both 模式下不生效")
    p.add_argument("--sub-avoid-gap", type=int, default=DEFAULT_SUB_AVOID_GAP,
                   metavar="N",
                   help=f"--sub-margin-v auto 时，中文块底边与源片硬字幕带上沿之间"
                        f"留的像素间隙（默认 {DEFAULT_SUB_AVOID_GAP}，即 854x480 "
                        f"源片上 480-408+24=96 那个实测默认值的来源）。"
                        f"给大了中文会往画面中段爬，给小了两层字会贴在一起")
    p.add_argument("--llm-cache-dir", default=str(DEFAULT_LLM_CACHE_DIR),
                   metavar="DIR",
                   help=f"LLM/VLM 响应缓存目录（默认 {DEFAULT_LLM_CACHE_DIR.name}/）。"
                        f"键是 sha256(模型名 + 端点 + 请求体)，命中就不发请求，"
                        f"晚期失败后重跑不用重复付费")
    p.add_argument("--no-llm-cache", action="store_true",
                   help="关掉 LLM/VLM 响应缓存，每次都真发请求")
    p.add_argument("--llm-max-retries", type=int,
                   default=sf_client.DEFAULT_MAX_RETRIES, metavar="N",
                   help=f"SiliconFlow 可重试错误（429/5xx/连接失败/响应非 JSON）的"
                        f"重试次数（默认 {sf_client.DEFAULT_MAX_RETRIES}）。"
                        f"400/401/402/403 一次都不重试；超时最多重试 "
                        f"{sf_client.MAX_TIMEOUT_ATTEMPTS - 1} 次（服务端可能已计费）；"
                        f"单次调用退避总时长上限 "
                        f"{sf_client.MAX_TOTAL_BACKOFF_SEC:.0f}s")
    p.add_argument("--download-max-retries", type=int,
                   default=DEFAULT_DOWNLOAD_RETRIES, metavar="N",
                   help=f"yt-dlp 下载的外层重试次数（默认 {DEFAULT_DOWNLOAD_RETRIES}）。"
                        f"5xx/超时/连接重置才重试，404 和非法 URL 立即失败")
    p.add_argument("--download-backoff-sec", type=float,
                   default=DEFAULT_DOWNLOAD_BACKOFF_SEC, metavar="SEC",
                   help=f"下载重试的指数退避基数秒（默认 "
                        f"{DEFAULT_DOWNLOAD_BACKOFF_SEC:.0f}，单次上限 "
                        f"{MAX_DOWNLOAD_BACKOFF_SEC:.0f}s）")
    p.add_argument("--strict-highlights", action="store_true",
                   help="金句门槛忽略环境变量放宽，只认代码里的下限")
    p.add_argument("--speaker", default="演讲者", help="说话人名字，用于打分和封面")
    p.add_argument("--language", default="en", help="片源语言（默认 en）")
    p.add_argument("--work", default="_tmp", help="中间产物目录（默认 _tmp/）")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.slug):
        die(EXIT_CONFIG, "config", "invalid_slug",
            detail="slug 只允许字母、数字、点、下划线和短横线", slug=args.slug)

    if args.cover_crop and not COVER_CROP_RE.fullmatch(args.cover_crop):
        die(EXIT_CONFIG, "config", "invalid_cover_crop",
            detail="--cover-crop 格式应为 W:H:X:Y（四个非负整数，同 ffmpeg crop 滤镜），"
                   "例如 854:396:0:0",
            cover_crop=args.cover_crop)

    api_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
    base_url = ((os.environ.get("SILICONFLOW_BASE_URL") or "").strip()
                or "https://api.siliconflow.cn/v1")
    if not api_key:
        die(EXIT_CONFIG, "config", "missing_siliconflow_api_key",
            detail="请设置环境变量 SILICONFLOW_API_KEY")

    sf_client.configure(
        cache_dir=None if args.no_llm_cache
        else Path(args.llm_cache_dir).expanduser().resolve(),
        cache_enabled=not args.no_llm_cache,
        max_retries=args.llm_max_retries)

    try:
        return run_pipeline(args, api_key, base_url)
    except sf_client.SFFatalError as e:
        # 400/401/402/403：请求本身、密钥或余额有问题，重跑改不了任何事
        die(EXIT_CONFIG, e.stage, e.reason, http_status=e.http_status,
            detail=str(e))
    except sf_client.SFRetryExhausted as e:
        die(EXIT_API, e.stage, e.reason, http_status=e.http_status,
            detail=str(e))
    finally:
        sf_client.log_cache_summary()


def run_pipeline(args, api_key: str, base_url: str) -> int:
    """按 ``STAGE_ORDER`` 跑完八步。"""
    log("pipeline", f"阶段顺序：{STAGE_ORDER_TEXT}")
    log("pipeline", "封面在 translate 之前判定：不达标就在花翻译的钱之前退出")

    work = Path(args.work).resolve() / args.slug
    work.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out).resolve() / args.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    video = resolve_source(args.source, work, args.download_max_retries,
                           args.download_backoff_sec)
    srt = transcribe(video, work, args.language)
    quotes = pick_quotes(srt, work, args.speaker, probe_duration(video),
                         api_key, base_url, args.strict_highlights)

    # 标题只依赖金句（transcript_zh / transcript_en 都来自 highlight），
    # 跟译文没有关系 —— 所以它和封面可以整体挪到 translate 前面
    title = make_title(quotes, args.speaker, api_key, base_url,
                       args.title_override)
    log("title", title)

    # 会拒的关卡排在花钱的关卡前面。封面挑不出合格帧就在这里退 2，
    # 此时翻译一个字都还没翻
    covers = make_covers(video, quotes, title, args.speaker, out_dir, work,
                         api_key, args.no_vlm, args.cover_time_sec,
                         args.cover_candidates, args.cover_crop)

    translators = ([args.translator] if not args.dual
                   else sorted(TRANSLATORS, key=lambda t: t != args.translator))

    finals: dict = {}
    for t in translators:
        bilingual = translate_windows(srt, quotes, work, t, api_key, base_url)
        name = "final.mp4" if t == args.translator else f"final_{t}.mp4"
        segs, placements = assemble(video, srt, bilingual, quotes, work,
                                    out_dir / name, args.sub_mode,
                                    args.sub_margin_v, args.sub_avoid_gap)
        finals[t] = out_dir / name
        if t == args.translator:
            primary_segs, primary_placements = segs, placements

    if args.dual and len(finals) > 1:
        build_compare_grid(finals, out_dir / "compare_grid.jpg")

    write_meta(out_dir, args.slug, title, args.source,
               out_dir / "final.mp4", primary_segs, covers,
               args.translator, args.speaker, args.sub_mode, args.sub_margin_v,
               args.sub_avoid_gap, primary_placements)

    log("done", f"产物 → {out_dir}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
