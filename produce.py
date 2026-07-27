#!/usr/bin/env python3
"""一键出片：视频源 → ``deliver/<slug>/{final.mp4, cover_*.jpg, meta.json}``。

    python produce.py --source <URL 或本地路径> --slug <output-slug>

流水线共九步：取源 → 转写 → 挑金句 → **封面选帧** → 翻译 → 拼片烧字幕 →
标题 → 封面出图 → manifest。

封面选帧排在翻译**之前**：它是唯一一个会把整条片子否掉的后置闸门，留在最后
就意味着每次退 2 都先把翻译的钱花光。烧标题那步依赖标题，仍留在 title 之后。

所有 LLM/VLM 调用都走 SiliconFlow，经 ``scripts/sf_client``（磁盘缓存 + 分类
重试）落到 ``scripts/sf_transport``（curl 子进程）。

退出码
------
0   成功
1   参数 / 配置错误
2   内容质量不达标（金句或封面达不到阈值，拒绝硬出）
3   外部依赖失败（SiliconFlow 5xx、yt-dlp 下载失败、ffmpeg 崩了）
"""

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
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

# ── 多集 ─────────────────────────────────────────────────────────────────────
# --episodes N 时下载和转写只做一次，highlight 挑 N 组互不重叠的片段，
# 每集各自走 选帧 → 翻译 → 烧字幕 → 标题 → 封面。
DEFAULT_EPISODES = 1
QUEUE_SCHEMA = 1
QUEUE_NAME = "queue.json"
RELEASE_TAG_PREFIX = "clips-"
# queue.json 里的 urls 要能直接点开下载，仓库名在 CI 里由 GITHUB_REPOSITORY 给，
# 本地跑没有这个变量时回落到本仓库。
DEFAULT_REPO = "fastisrealslow/bilingual-subtitle-burner"
DEFAULT_SERVER_URL = "https://github.com"
# 投稿标签：说话人 + 两个固定的领域标签。不额外问模型 —— 多一次调用换三个
# 词不划算，用户在投稿框里改一个字的成本更低。
BASE_TAGS = ("价值投资", "投资思维")

# --cover-crop 的取值格式，与 ffmpeg crop 滤镜一致：W:H:X:Y
COVER_CROP_RE = re.compile(r"^\d+:\d+:\d+:\d+$")

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


# yt-dlp 外层重试。archive.org 的 HTTP 500 是间歇性的（CI run 30263087066 就是
# 这么退 3 的，原样重跑就过），无人值守时不重试等于白跑一趟。
DEFAULT_LLM_CACHE_DIR = ROOT / sf_client.DEFAULT_CACHE_DIRNAME

DEFAULT_DOWNLOAD_RETRIES = 5
DEFAULT_DOWNLOAD_BACKOFF_SEC = 10.0
DOWNLOAD_BACKOFF_CAP_SEC = 60.0
# yt-dlp 默认 20s：实测 archive.org 首字节能到 13.8s、整体只有 60~156 KB/s，
# 20s 必然偶发误杀（CI run 30274189811、30279507775 都是 read timeout=20.0s）。
DEFAULT_DOWNLOAD_SOCKET_TIMEOUT_SEC = 120.0
# 重试也变不出来的：源不存在、URL 根本不是 yt-dlp 认得的东西
# 408/429 不在里面：那两个是「稍后再来」，值得重试
DOWNLOAD_FATAL_RE = re.compile(
    r"HTTP Error (400|401|403|404|410)\b|404: Not Found"
    r"|Unsupported URL|is not a valid URL"
    r"|Video unavailable|This video is private|Incomplete YouTube ID",
    re.IGNORECASE)

# 流水线阶段顺序。封面选帧夹在 highlight 和 translate 之间是这次改动的重点，
# 所以整张表在开跑时就打进日志，跑到哪一步一眼可见。
STAGE_ORDER = [
    ("input", "取源（yt-dlp / 本地文件）"),
    ("transcribe", "faster-whisper 转写"),
    ("highlight", "挑金句（LLM）"),
    ("cover-select", "封面选帧 + 阈值判定（VLM）— 故意排在翻译之前"),
    ("translate", "翻译（LLM，主要开销）"),
    ("assemble", "切片烧字幕 + concat"),
    ("title", "起标题（LLM）"),
    ("cover-render", "封面烧标题出图"),
    ("manifest", "汇总 meta.json"),
]


# ── 基础设施 ──────────────────────────────────────────────────────────────────

def log(stage: str, msg: str) -> None:
    print(f"[{stage}] {msg}", flush=True)


def log_stage_plan() -> None:
    names = " → ".join(name for name, _ in STAGE_ORDER)
    log("stage", f"阶段顺序：{names}")


def stage(name: str) -> None:
    order = [n for n, _ in STAGE_ORDER]
    log("stage", f"{order.index(name) + 1}/{len(order)} {name} —— "
                 f"{dict(STAGE_ORDER)[name]}")


def die(code: int, stage: str, reason: str, **fields) -> None:
    """结构化失败退出。质量问题退 2，外部依赖退 3，配置问题退 1。"""
    payload = {"stage": stage, "reason": reason, **fields}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)
    sys.exit(code)


def die_fatal_http(stage: str, e) -> None:
    """SiliconFlow 的不可重试失败：退出码由 sf_client 按语义定，原样带上状态码。"""
    die(e.exit_code, stage, e.reason, http_status=e.status_code, detail=e.detail)


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

def download_is_fatal(output: str) -> bool:
    """区分「重试也没用」和「值得重试」。

    404 / 非法 URL 重试多少次都是同一个结果，纯属浪费无人值守的那 40 分钟；
    5xx、超时、连接重置则大概率是对端抖动，原样重跑就过。判不出来的按可重试
    处理 —— 多试两次的代价远小于白跑一趟。
    """
    return bool(DOWNLOAD_FATAL_RE.search(output or ""))


def download_source(
        source: str, out: Path, retries: int, backoff_sec: float,
        socket_timeout_sec: float = DEFAULT_DOWNLOAD_SOCKET_TIMEOUT_SEC,
) -> None:
    """带外层退避重试的 yt-dlp 下载。

    yt-dlp 自己的 ``--retries`` / ``--fragment-retries`` 只覆盖单个 HTTP 请求
    和分片，整次调用被 archive.org 的 500 顶回来时它就直接退了，所以外面还要
    再包一层。
    """
    cmd = ["yt-dlp", "-f", "bv*[height<=720]+ba/b[height<=720]/b",
           "--merge-output-format", "mp4",
           "--retries", "5", "--fragment-retries", "5",
           "--socket-timeout", f"{socket_timeout_sec:g}",
           "-o", str(out), source]
    last = ""
    for attempt in range(1, retries + 1):
        log("input", f"yt-dlp 下载 {source}（第 {attempt}/{retries} 次）")
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
        if p.returncode == 0:
            return
        last = ((p.stderr or "") + (p.stdout or "")).strip()
        print(last[-2000:], file=sys.stderr, flush=True)
        if download_is_fatal(last):
            die(EXIT_CONFIG, "input", "source_unavailable",
                detail="片源不存在或 URL 不受支持，重试没有意义",
                source=source, returncode=p.returncode, output=last[-500:])
        if attempt == retries:
            break
        wait = min(backoff_sec * (2 ** (attempt - 1)), DOWNLOAD_BACKOFF_CAP_SEC)
        wait += random.uniform(0, wait * 0.25)
        log("input", f"下载失败（rc={p.returncode}），{wait:.1f}s 后重试")
        time.sleep(wait)
    die(EXIT_API, "input", "download_failed", source=source,
        attempts=retries, output=last[-500:])


def resolve_source(source: str, work: Path,
                   retries: int = DEFAULT_DOWNLOAD_RETRIES,
                   backoff_sec: float = DEFAULT_DOWNLOAD_BACKOFF_SEC,
                   socket_timeout_sec: float =
                   DEFAULT_DOWNLOAD_SOCKET_TIMEOUT_SEC) -> Path:
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
    download_source(source, out, retries, backoff_sec, socket_timeout_sec)
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

def clip_duration(q: dict) -> float:
    return float(q.get("clip_duration_sec") or q.get("duration_sec") or 0)


def overlaps_any(q: dict, spans: list) -> bool:
    s, e = q["clip_start_sec"], q["clip_end_sec"]
    return any(e > a and s < b for a, b in spans)


def group_episodes(quotes: list, episodes: int = DEFAULT_EPISODES,
                   strict: bool = False) -> list:
    """把候选金句切成 N 组互不重叠的片段组，每组仍要过现有的出片门槛。

    第一组直接交给 ``HL.enforce_quote_thresholds`` —— 单集路径必须逐字沿用
    原来的判定和拒绝理由，不达标照旧退 2。后续每组按 rank 顺序贪心取满
    ``SEGMENTS`` 段，跳过与已选片段重叠的候选。

    源不够时不凑数：填不满一组、或这一组总时长不够，就在这里停下，能出几组
    算几组。候选是按 rank 排的，第一组填不满往后只会更差，所以停在第一个
    不合格的组上。
    """
    first = HL.enforce_quote_thresholds(quotes, want=SEGMENTS, strict=strict)
    groups = [first]
    if episodes <= 1:
        return groups

    min_quote_sec = HL.threshold("HIGHLIGHT_MIN_QUOTE_SEC", HL.MIN_QUOTE_SEC,
                                 strict)
    min_quotes = int(HL.threshold("HIGHLIGHT_MIN_QUOTES", HL.MIN_QUOTES, strict))
    min_total_sec = HL.threshold("HIGHLIGHT_MIN_TOTAL_SEC", HL.MIN_TOTAL_SEC,
                                 strict)

    used = [(q["clip_start_sec"], q["clip_end_sec"]) for q in first]
    pool = [q for q in sorted(quotes, key=lambda h: h.get("rank", 0))
            if all(q is not f for f in first) and clip_duration(q) >= min_quote_sec]

    while len(groups) < episodes:
        group, spans = [], list(used)
        for q in pool:
            if len(group) >= SEGMENTS:
                break
            if overlaps_any(q, spans):
                continue
            group.append(q)
            spans.append((q["clip_start_sec"], q["clip_end_sec"]))

        if len(group) < max(SEGMENTS, min_quotes):
            log("highlight", f"第 {len(groups) + 1} 集只凑得出 {len(group)} 段"
                             f"（需要 {max(SEGMENTS, min_quotes)} 段），源不够，不凑数")
            break
        total = sum(clip_duration(q) for q in group)
        if total < min_total_sec:
            log("highlight", f"第 {len(groups) + 1} 集合计 {total:.0f}s "
                             f"不足 {min_total_sec:.0f}s，源不够，不凑数")
            break

        groups.append(group)
        used = spans
        pool = [q for q in pool if all(q is not g for g in group)]
    return groups


def pick_quote_groups(srt: Path, work: Path, speaker: str, total_dur: float,
                      api_key: str, base_url: str, strict: bool,
                      episodes: int = DEFAULT_EPISODES) -> list:
    """LLM 打分挑金句 → 切点对齐 → 过出片门槛 → 切成 N 组。

    不达标由 highlight 退 2。返回片段组列表，长度 ≤ ``episodes``。
    """
    entries = HL.parse_srt(str(srt))
    if not entries:
        die(EXIT_QUALITY, "highlight", "empty_transcript", srt=str(srt))

    paragraphs = HL.merge_into_paragraphs(entries, gap_sec=3.0, max_sec=90.0)
    log("highlight", f"{len(entries)} 条字幕 → {len(paragraphs)} 个段落")

    try:
        quotes = HL.score_highlights(paragraphs, api_key, HIGHLIGHT_MODEL,
                                     base_url, speaker=speaker,
                                     top_n=max(10, SEGMENTS * episodes))
    except sf_client.FatalHTTPError as e:
        die_fatal_http("highlight", e)
    except RuntimeError as e:
        die(EXIT_API, "highlight", "siliconflow_unavailable", detail=str(e))
    quotes = HL.align_clips(quotes, entries, total_dur)
    groups = group_episodes(quotes, episodes, strict)

    for i, group in enumerate(groups, 1):
        out = work / ("quotes.json" if i == 1 else f"quotes_{episode_id(i)}.json")
        out.write_text(json.dumps(group, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        total = sum(q["clip_duration_sec"] for q in group)
        log("highlight", f"第 {i} 集选中 {len(group)} 段，合计 {total:.0f}s → {out}")
    if episodes > 1:
        log("highlight", f"要 {episodes} 集，实际能出 {len(groups)} 集")
    return groups


def pick_quotes(srt: Path, work: Path, speaker: str, total_dur: float,
                api_key: str, base_url: str, strict: bool) -> list:
    """单集路径：只取第一组片段。"""
    return pick_quote_groups(srt, work, speaker, total_dur, api_key, base_url,
                             strict)[0]


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
        except sf_client.FatalHTTPError as e:
            die_fatal_http("translate", e)
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

    探测器自己把不可信的结果（不像文字、带太厚、上沿越过中线）报成「没探到」，
    这里就回落到固定默认值，并把没过哪道闸、实测值多少写进日志 —— 回落可以，
    闷声回落不行。

    真探到了一条可信的带、避让却仍会把中文顶到画面上半部分，才退 2：那说明
    源片硬字幕的位置本身就没法躲，烧出来只会是字幕骑在讲者脸上，按退出码约定
    让人来看，比闷头出一条废片强。CI run 30269220766 走的不是这条路 —— 那次是
    把 y=240 的 B-roll 亮画面当成了字幕带，现在会被文字感闸门挡在探测阶段。
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    placements = []
    for offset, c in enumerate(cues):
        idx = first_index + offset
        a = clip_start + c["start_sec"]
        b = clip_start + c["end_sec"]
        top_y, note = HP.probe_cue_band(str(video), a, b, str(tmp_dir),
                                        prefix=f"cue{idx:03d}")
        margin_v = auto_margin_v(video_height, top_y, gap)
        if top_y is not None and margin_v > video_height // 2:
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
            log("assemble", f"cue#{idx} {a:.1f}~{b:.1f}s 未探到可信硬字幕带，"
                            f"回落到默认 MarginV={margin_v}；理由：{note}")
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

UNVERIFIED_COVER_WARNING = (
    "⚠️ 已开启 --cover-allow-unverified：封面帧未经 VLM 人物核验，"
    "产出的封面可能不是 --speaker 指定的人物，请人工确认后再发布。"
)


def verify_pinned_frame(frame: str, cover_time_sec: float, speaker: str,
                        api_key: str, no_vlm: bool, allow_unverified: bool,
                        report: dict) -> None:
    """把 ``--cover-time-sec`` 钉下的帧送 VLM 复核人物与封面分，不过就退 2。

    走的是自动选帧同一套 ``call_vision_llm`` + ``frame_passes_vlm``，
    阈值只有 ``MIN_VLM_PASS_SCORE`` 一处定义。
    """
    if allow_unverified or no_vlm:
        why = "--cover-allow-unverified" if allow_unverified else "--no-vlm"
        log("cover", UNVERIFIED_COVER_WARNING)
        print(f"[cover] {UNVERIFIED_COVER_WARNING}（触发开关：{why}）",
              file=sys.stderr, flush=True)
        report["cover_verification"] = "skipped"
        report["cover_unverified_reason"] = why
        return

    if not api_key:
        die(EXIT_CONFIG, "cover", "missing_siliconflow_api_key",
            detail="钉帧同样要过 VLM 人物校验，需要 SILICONFLOW_API_KEY；"
                   "确实要跳过校验请显式加 --cover-allow-unverified")
    try:
        verdict = COVER.verify_frame_person(api_key, VISION_MODEL, frame, speaker)
    except sf_client.FatalHTTPError as e:
        die_fatal_http("cover", e)
    except RuntimeError as e:
        die(EXIT_API, "cover", "vision_call_failed", detail=str(e))

    if verdict is None:
        # 校验不了不等于校验通过：这是外部依赖故障，不能顺势出片。
        die(EXIT_API, "cover", "pinned_frame_verification_unavailable",
            detail="VLM 未返回可用的人物判定结果，无法核验钉下的封面帧",
            cover_time_sec=round(cover_time_sec, 2))

    report["cover_verification"] = "vlm"
    report["cover_vlm_person"] = verdict["person"]
    report["cover_vlm_score"] = verdict["cover_score"]
    report["cover_vlm_reason"] = verdict["reason"]
    if not verdict["passed"]:
        die(EXIT_QUALITY, "cover", "pinned_frame_rejected",
            detail=f"--cover-time-sec 钉下的帧未通过 VLM 人物校验："
                   f"画面里是「{verdict['person']}」，"
                   f"而 --speaker 是「{speaker}」",
            cover_time_sec=round(cover_time_sec, 2),
            speaker=speaker,
            vlm_person=verdict["person"],
            vlm_cover_score=verdict["cover_score"],
            vlm_reason=verdict["reason"],
            min_cover_score=COVER.MIN_VLM_PASS_SCORE,
            hint="请换一个时间点；确认这帧就是要的画面时，"
                 "可加 --cover-allow-unverified 显式放行（封面将不经人物核验）")
    report["cover_vlm_passed"] = True
    log("cover", f"钉帧 t={cover_time_sec:.1f}s 通过 VLM 校验："
                 f"{verdict['person']}，封面分={verdict['cover_score']}")


def select_cover_frame(video: Path, quotes: list, speaker: str, work: Path,
                       api_key: str, no_vlm: bool,
                       cover_time_sec: float | None = None,
                       candidates: int = COVER.DEFAULT_COVER_CANDIDATES,
                       cover_crop: str | None = None,
                       allow_unverified: bool = False) -> tuple[str, dict]:
    """选出封面帧并过阈值判定。挑不出合格帧时由 cover 侧退 2。

    这一步**故意排在翻译之前**：它是全流程里唯一一个会在末尾把整条片子否掉的
    质量闸门，留在最后就意味着每次退 2 都先把翻译的钱花光、把成片也烧完
    （CI run 30259127265 / 30260691746 就是这么白跑的）。烧标题那步依赖标题，
    留在原位；选帧和阈值判定不依赖译文，提前。

    有些片源天生挑不出合格封面 —— 解说式剪辑用的是原声 + 素材空镜，全片
    没有主讲人正脸，自动选帧只会挑到不相干的素材人物，冒充主讲人属于误导。
    ``cover_time_sec`` 就是给这种源片的人工出口：钉死一个时间点，跳过人脸
    预筛和候选采样 —— 这条路径同样提前，截不出帧也要早点知道。

    钉帧只覆盖**选哪一帧**，不覆盖**质量闸门**：钉下的帧照样送 VLM 做人物
    识别与封面分判定，判定不过就按内容质量拒绝退 2。早先钉帧连校验一起跳，
    结果钉错时间点会静默出片 —— CI run 30281699063 把爱因斯坦的黑板资料照
    配上「查理·芒格」的角标发了 Release。要跳过校验必须显式给
    ``allow_unverified``。

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
        log("cover", f"手动指定封面帧 t={cover_time_sec:.1f}s（跳过人脸预筛与候选采样）")
        report["cover_source"] = "manual"
        report["cover_time_sec"] = round(cover_time_sec, 2)
        report["cover_vlm_passed"] = False
        verify_pinned_frame(frame, cover_time_sec, speaker, api_key, no_vlm,
                            allow_unverified, report)
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
        except sf_client.FatalHTTPError as e:
            die_fatal_http("cover", e)
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

    report["cover_source_frame"] = os.path.basename(frame)
    report.setdefault("cover_vlm_passed", not no_vlm)
    report.setdefault("cover_vlm_rejections", [])
    return frame, report


def render_covers(frame: str, title: str, speaker: str, out_dir: Path,
                  report: dict) -> dict:
    """把标题烧上已选定的帧，出 16:9 / 9:16 两张图。标题来自 title 这一步。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    covers = {}
    for name, size in (("cover_16x9.jpg", (1280, 720)),
                       ("cover_9x16.jpg", (1080, 1920))):
        path = out_dir / name
        COVER.make_cover(frame, title, speaker, str(path), size)
        covers[name] = path
        log("cover", f"→ {path}")
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
    except sf_client.FatalHTTPError as e:
        die_fatal_http("title", e)
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


# ── 9. queue.json ─────────────────────────────────────────────────────────────

def episode_id(index: int) -> str:
    return f"ep{index:02d}"


def release_tag(slug: str) -> str:
    return f"{RELEASE_TAG_PREFIX}{slug}"


def episode_dir(out_dir: Path, index: int, episodes: int) -> Path:
    """单集仍然直接落在 ``deliver/<slug>/``，多集才分 ``ep01/`` 子目录。

    单集的产物路径是既有契约（artifact、README、手动核片都按它来），不因为
    加了多集就挪窝。
    """
    return out_dir if episodes <= 1 else out_dir / episode_id(index)


def release_asset_names(index: int) -> dict:
    """Release 上的扁平文件名 —— 一个 tag 下所有集的资产平铺在一起。"""
    eid = episode_id(index)
    return {"video": f"{eid}.mp4",
            "cover_16x9": f"{eid}_cover_16x9.jpg",
            "cover_9x16": f"{eid}_cover_9x16.jpg"}


def episode_tags(speaker: str) -> list:
    return [speaker, *BASE_TAGS]


def episode_desc(speaker: str, title: str, source_url: str) -> str:
    """可直接粘进投稿框的简介，末尾带原视频出处。"""
    return (f"{speaker}：{title}\n\n"
            f"本片剪自{speaker}的原始访谈，中文字幕为本频道翻译制作，"
            f"未改动讲话内容。\n"
            f"原视频出处：{source_url}")


def queue_entry(index: int, title: str, final: Path, segs: list) -> dict:
    """把一集的产物收成 build_queue 要的那条记录。

    成片缺失时不炸：write_meta 对 sha256 也是同样的容忍。
    """
    starts = [s["source_start_sec"] for s in segs] or [0.0]
    ends = [s["source_end_sec"] for s in segs] or [0.0]
    return {
        "index": index,
        "title": title,
        "duration_sec": round(probe_duration(final), 2) if final.is_file() else 0.0,
        "source_start_sec": min(starts),
        "source_end_sec": max(ends),
        "cue_count": sum(s["cues"] for s in segs),
        "sha256": sha256(final) if final.is_file() else "",
    }


def build_queue(slug: str, source_url: str, speaker: str, episodes: list,
                generated_at: datetime | None = None,
                commit: str | None = None, repo: str | None = None,
                server_url: str | None = None) -> dict:
    """把每集的元数据汇总成 ``queue.json``（规格第一章的 schema）。

    ``episodes`` 每项需要 ``index / title / duration_sec / source_start_sec /
    source_end_sec / cue_count / sha256``。``scheduled_date`` 从生成日 +1 天
    起顺序每天一条。
    """
    now = generated_at or datetime.now(timezone.utc)
    repo = repo or (os.environ.get("GITHUB_REPOSITORY") or "").strip() or DEFAULT_REPO
    server = (server_url
              or (os.environ.get("GITHUB_SERVER_URL") or "").strip()
              or DEFAULT_SERVER_URL).rstrip("/")
    tag = release_tag(slug)
    base = f"{server}/{repo}/releases/download/{tag}"

    items = []
    for ep in episodes:
        index = ep["index"]
        files = release_asset_names(index)
        items.append({
            "index": index,
            "id": episode_id(index),
            "title": ep["title"],
            "duration_sec": round(float(ep["duration_sec"]), 2),
            "source_start_sec": round(float(ep["source_start_sec"]), 2),
            "source_end_sec": round(float(ep["source_end_sec"]), 2),
            "cue_count": int(ep["cue_count"]),
            "tags": episode_tags(speaker),
            "desc": episode_desc(speaker, ep["title"], source_url),
            "files": files,
            "urls": {k: f"{base}/{v}" for k, v in files.items()},
            "sha256": {"video": ep["sha256"]},
            "scheduled_date": (now + timedelta(days=index)).date().isoformat(),
            "status": "pending",
            "publish": {"bilibili": None, "douyin": None},
        })

    return {
        "schema": QUEUE_SCHEMA,
        "slug": slug,
        "source_url": source_url,
        "speaker": speaker,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "commit": commit or os.environ.get("GITHUB_SHA", "local"),
        "release_tag": tag,
        "episodes": items,
    }


def write_queue(out_dir: Path, queue: dict) -> Path:
    path = out_dir / QUEUE_NAME
    path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n",
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


def cover_time_sec_arg(raw: str) -> list[float]:
    """``--cover-time-sec`` 接一个或多个非负秒数，逗号分隔，一集一个。"""
    parts = [p.strip() for p in raw.split(",")]
    values = []
    for p in parts:
        try:
            v = float(p)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"{raw!r} 无效，应为逗号分隔的非负秒数，一集一个（如 287 或 287,412）")
        if v < 0:
            raise argparse.ArgumentTypeError(f"{raw!r} 无效，秒数不能为负")
        values.append(v)
    return values


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="produce.py",
        description="一键出片：视频源 → deliver/<slug>/{final.mp4, cover_*.jpg, meta.json}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="退出码：0 成功 / 1 配置错误 / 2 内容质量不达标 / 3 外部依赖失败")
    p.add_argument("--source", required=True, help="视频 URL 或本地路径（支持 file://）")
    p.add_argument("--slug", required=True, help="产物目录名")
    p.add_argument("--title-override", default="", help="跳过模型起标题，直接用这个")
    p.add_argument("--translator", default=DEFAULT_TRANSLATOR,
                   choices=sorted(TRANSLATORS), help="翻译模型（默认 deepseek-v3）")
    p.add_argument("--dual", action="store_true",
                   help="两个翻译都跑，额外产出对比拼图")
    p.add_argument("--episodes", type=int, default=DEFAULT_EPISODES, metavar="N",
                   help=f"一次出几集（默认 {DEFAULT_EPISODES}）。下载和转写只做一次，"
                        f"highlight 挑 N 组互不重叠的片段，每集各自选帧/翻译/"
                        f"烧字幕/起标题/出封面。源不够时不凑数，能出几集就几集，"
                        f"实际集数写进 queue.json；N>1 时产物落在 "
                        f"deliver/<slug>/ep01/、ep02/…")
    p.add_argument("--out", default="deliver", help="产物根目录（默认 deliver/）")
    p.add_argument("--no-vlm", action="store_true",
                   help="封面跳过 VLM 校验，只按几何规则选帧")
    p.add_argument("--cover-candidates", type=int,
                   default=COVER.DEFAULT_COVER_CANDIDATES,
                   help=f"封面候选帧数量，在金句时长上均匀取样，避开首尾各 "
                        f"{COVER.EDGE_MARGIN_SEC:.0f}s"
                        f"（默认 {COVER.DEFAULT_COVER_CANDIDATES}）")
    p.add_argument("--cover-time-sec", type=cover_time_sec_arg, default=None,
                   metavar="SEC[,SEC...]",
                   help="手动指定封面帧时间点（秒），跳过人脸预筛和候选采样。"
                        "用于全片没有主讲人正脸的解说式剪辑/空镜素材片。"
                        "钉下的帧仍会送 VLM 做人物校验，判定不是 --speaker "
                        "本人就退 2。多集时给逗号分隔的多个值，一集一个，"
                        "按集顺序对应，个数必须与实际产出集数相等")
    p.add_argument("--cover-allow-unverified", action="store_true",
                   help="放行未经 VLM 人物核验的钉帧封面（默认关闭）。"
                        "只在确认过画面内容时用；打开后日志会打醒目告警，"
                        "产出的封面有张冠李戴的风险")
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
                   help=f"LLM/VLM 响应的磁盘缓存目录（默认仓库根 "
                        f"{sf_client.DEFAULT_CACHE_DIRNAME}/）。键是 "
                        f"sha256(模型名+端点+规范化请求体)，晚期失败重跑时"
                        f"前面已付费的调用直接命中，一次都不重发")
    p.add_argument("--no-llm-cache", action="store_true",
                   help="关闭 LLM/VLM 响应缓存，所有调用一律实发")
    p.add_argument("--llm-max-retries", type=int,
                   default=sf_client.DEFAULT_MAX_RETRIES, metavar="N",
                   help=f"单个 SiliconFlow 请求最多尝试几次（含首次，默认 "
                        f"{sf_client.DEFAULT_MAX_RETRIES}）。429/5xx 和连接失败才"
                        f"退避重试；400/401/402/403 一次都不重试。客户端超时最多"
                        f"只试 {sf_client.TIMEOUT_MAX_ATTEMPTS} 次 —— 服务端可能"
                        f"已处理并计费")
    p.add_argument("--download-retries", type=int,
                   default=DEFAULT_DOWNLOAD_RETRIES, metavar="N",
                   help=f"yt-dlp 下载最多尝试几次（含首次，默认 "
                        f"{DEFAULT_DOWNLOAD_RETRIES}）。5xx / 超时 / 连接重置会"
                        f"退避重试，404 和非法 URL 直接失败")
    p.add_argument("--download-backoff-sec", type=float,
                   default=DEFAULT_DOWNLOAD_BACKOFF_SEC, metavar="SEC",
                   help=f"yt-dlp 重试的退避基数秒，逐次翻倍，单次上限 "
                        f"{DOWNLOAD_BACKOFF_CAP_SEC:.0f}s"
                        f"（默认 {DEFAULT_DOWNLOAD_BACKOFF_SEC:.0f}）")
    p.add_argument("--download-socket-timeout", type=float,
                   default=DEFAULT_DOWNLOAD_SOCKET_TIMEOUT_SEC, metavar="SEC",
                   help=f"yt-dlp 单个 socket 读写的超时秒数（默认 "
                        f"{DEFAULT_DOWNLOAD_SOCKET_TIMEOUT_SEC:.0f}）。archive.org "
                        f"首字节实测能到 14s，yt-dlp 自带的 20s 会把活着的源判死")
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

    if args.episodes < 1:
        die(EXIT_CONFIG, "config", "invalid_episodes",
            detail="--episodes 至少为 1", episodes=args.episodes)

    if args.episodes > 1 and args.dual:
        die(EXIT_CONFIG, "config", "dual_with_multiple_episodes",
            detail="--dual 是单集的译本对比工具，多集下产物含义不清，"
                   "两者只能选一个",
            episodes=args.episodes)

    cover_times = args.cover_time_sec or []
    if args.episodes > 1 and len(cover_times) == 1:
        die(EXIT_CONFIG, "config", "cover_time_with_multiple_episodes",
            detail="一个时间点不能给多集共用：每集片段不同，同一帧未必落在第 2 集"
                   "里，复用还会让两集出一模一样的封面。请给逗号分隔的多个值，"
                   "一集一个，按集顺序对应",
            episodes=args.episodes, cover_time_sec=cover_times)

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

    work = Path(args.work).resolve() / args.slug
    work.mkdir(parents=True, exist_ok=True)
    out_dir = Path(args.out).resolve() / args.slug
    out_dir.mkdir(parents=True, exist_ok=True)

    sf_client.configure(cache_dir=args.llm_cache_dir,
                        cache_enabled=not args.no_llm_cache,
                        max_retries=args.llm_max_retries)
    log_stage_plan()

    stage("input")
    video = resolve_source(args.source, work, args.download_retries,
                           args.download_backoff_sec,
                           args.download_socket_timeout)
    stage("transcribe")
    srt = transcribe(video, work, args.language)
    stage("highlight")
    if args.episodes <= 1:
        groups = [pick_quotes(srt, work, args.speaker, probe_duration(video),
                              api_key, base_url, args.strict_highlights)]
    else:
        groups = pick_quote_groups(srt, work, args.speaker,
                                   probe_duration(video), api_key, base_url,
                                   args.strict_highlights, args.episodes)

    # 实际集数可能少于 --episodes 请求数（源不够时不凑数），所以钉帧个数要跟
    # 真正产出的组数核，而不是跟请求数核。核在翻译之前，免得白花钱。
    if cover_times and len(cover_times) != len(groups):
        die(EXIT_CONFIG, "config", "cover_time_count_mismatch",
            detail=f"--cover-time-sec 给了 {len(cover_times)} 个时间点，"
                   f"实际产出 {len(groups)} 集（源不够时会少于 --episodes "
                   f"请求的 {args.episodes} 集），必须一集一个、个数相等",
            cover_time_sec=cover_times, episodes=len(groups),
            episodes_requested=args.episodes)

    queue_episodes = []
    for index, quotes in enumerate(groups, 1):
        ep_dir = episode_dir(out_dir, index, args.episodes)
        ep_dir.mkdir(parents=True, exist_ok=True)
        ep_work = work if args.episodes <= 1 else work / episode_id(index)
        ep_work.mkdir(parents=True, exist_ok=True)
        if args.episodes > 1:
            log("episode", f"第 {index}/{len(groups)} 集 → {ep_dir}")

        # 翻译之前先过封面这道闸门：挑不出合格封面的片子在这里退 2，
        # 翻译和拼片的钱一分都不花。
        stage("cover-select")
        cover_frame, cover_report = select_cover_frame(
            video, quotes, args.speaker, ep_work, api_key, args.no_vlm,
            cover_times[index - 1] if cover_times else None,
            args.cover_candidates, args.cover_crop,
            args.cover_allow_unverified)

        translators = ([args.translator] if not args.dual
                       else sorted(TRANSLATORS,
                                   key=lambda t: t != args.translator))

        finals: dict = {}
        for t in translators:
            stage("translate")
            bilingual = translate_windows(srt, quotes, ep_work, t, api_key,
                                          base_url)
            name = "final.mp4" if t == args.translator else f"final_{t}.mp4"
            stage("assemble")
            segs, placements = assemble(video, srt, bilingual, quotes, ep_work,
                                        ep_dir / name, args.sub_mode,
                                        args.sub_margin_v, args.sub_avoid_gap)
            finals[t] = ep_dir / name
            if t == args.translator:
                primary_segs, primary_placements = segs, placements

        stage("title")
        title = make_title(quotes, args.speaker, api_key, base_url,
                           args.title_override)
        log("title", title)

        stage("cover-render")
        covers = render_covers(cover_frame, title, args.speaker, ep_dir,
                               cover_report)

        if args.dual and len(finals) > 1:
            build_compare_grid(finals, ep_dir / "compare_grid.jpg")

        stage("manifest")
        final = ep_dir / "final.mp4"
        write_meta(ep_dir, args.slug, title, args.source,
                   final, primary_segs, covers, args.translator,
                   args.speaker, args.sub_mode, args.sub_margin_v,
                   args.sub_avoid_gap, primary_placements)
        queue_episodes.append(queue_entry(index, title, final, primary_segs))

    write_queue(out_dir, build_queue(args.slug, args.source, args.speaker,
                                     queue_episodes))

    sf_client.log_cache_stats()
    log("done", f"{len(groups)} 集产物 → {out_dir}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
