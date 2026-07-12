#!/usr/bin/env python3
"""
run.py — 视频金句短片全流水线主控

用法：
  # 从 URL 开始跑全流程
  python3 run.py --url "https://www.bilibili.com/video/BV1xx..." --speaker 李录

  # 指定本地文件
  python3 run.py --video /path/to/video.mp4 --speaker 帕伯莱

  # 指定 job（已存在则续跑）
  python3 run.py --job my_job_id --from-step 3

  # 只跑特定步骤
  python3 run.py --job my_job_id --from-step 4 --to-step 6

流程（每步产物落盘，可断点续跑）：
  1  fetch       采集（yt-dlp 下载或复制本地文件）
  2  transcribe  ASR 转写（Whisper → full.srt）
  3  translate   字幕翻译（中→英 或 英→中 → bilingual.json）
  4  highlight   金句识别与评分（highlights.json）
  5  copywrite   标题/文案/标签生成（manifest.json）
  6  clip        切片+双语字幕烧录（clips/）
  7  cover       封面生成（clips/*_cover.jpg）
  8  upload      素材包 + B站上传清单生成

环境变量：
  SILICONFLOW_API_KEY   必填
  SILICONFLOW_MODEL     可选，默认 Qwen/Qwen3-8B
  WHISPER_MODEL         可选，默认 auto（优先 large-v3，fallback small）
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── 路径常量 ──────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
STEPS_DIR = ROOT / "steps"
OUTPUT_ROOT = ROOT / "output" / "jobs"

# 本地缓存目录（跨平台）：优先 ~/.cache/whisper/<model>/model.bin，
# 找不到则退回 faster-whisper 模型名（首次运行自动从 HuggingFace 下载）。
WHISPER_CACHE    = Path.home() / ".cache/whisper"
WHISPER_LARGE_V3 = WHISPER_CACHE / "large-v3"
WHISPER_MEDIUM   = WHISPER_CACHE / "medium"
WHISPER_SMALL    = WHISPER_CACHE / "small"
WHISPER_BASE     = WHISPER_CACHE / "base"

STEPS = [1, 2, 3, 4, 5, 6, 7, 8]
STEP_NAMES = {
    1: "fetch",
    2: "transcribe",
    3: "translate",
    4: "highlight",
    5: "copywrite",
    6: "clip",
    7: "cover",
    8: "upload",
}

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _available_mem_bytes() -> float:
    """估算可用内存（容器取 cgroup 限制，本地取物理内存）"""
    # 1) 容器 cgroup 限制
    for p in ['/sys/fs/cgroup/memory/memory.limit_in_bytes', '/sys/fs/cgroup/memory.max']:
        try:
            v = open(p).read().strip()
            if v.isdigit():
                lim = int(v)
                # cgroup v2 无限制时为 'max'；数值过大视为无限制
                if lim < 1 << 62:
                    return float(lim)
        except Exception:
            pass
    # 2) 本地物理内存
    try:
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except Exception:
        return float('inf')


def detect_whisper_model() -> str:
    """
    自动选择可用的 Whisper 模型。
    优先级：large-v3 > medium > small > base。
    - 先看环境变量 WHISPER_MODEL（路径或模型名均可）
    - 再看本地缓存 ~/.cache/whisper/<model>/model.bin
    - 都没有时，根据可用内存选一个 faster-whisper 模型名（首次运行自动下载）
    """
    env_model = os.environ.get("WHISPER_MODEL", "").strip()
    if env_model:
        # 路径存在就用路径；否则当作模型名交给 faster-whisper
        return env_model

    mem = _available_mem_bytes()

    # 1) 本地已缓存的模型目录
    lv3_bin = WHISPER_LARGE_V3 / "model.bin"
    if lv3_bin.exists() and lv3_bin.stat().st_size > 2_900_000_000:
        if mem >= 6 * 1024 ** 3:
            return str(WHISPER_LARGE_V3)
        print(f"[run] large-v3 已缓存但可用内存 {mem/1024**3:.1f}GB 不足 6GB，降级", flush=True)

    med_bin = WHISPER_MEDIUM / "model.bin"
    if med_bin.exists() and med_bin.stat().st_size > 1_400_000_000:
        return str(WHISPER_MEDIUM)
    if (WHISPER_SMALL / "model.bin").exists():
        return str(WHISPER_SMALL)
    if (WHISPER_BASE / "model.bin").exists():
        return str(WHISPER_BASE)

    # 2) 本地无缓存 → 返回 faster-whisper 模型名，首次运行自动下载
    #    根据内存选择（>=6GB 用 large-v3，>=3GB 用 medium，否则 small）
    if mem >= 6 * 1024 ** 3:
        chosen = "large-v3"
    elif mem >= 3 * 1024 ** 3:
        chosen = "medium"
    else:
        chosen = "small"
    print(f"[run] 本地无缓存模型，将自动下载 faster-whisper '{chosen}'（可用内存 {mem/1024**3:.1f}GB）", flush=True)
    return chosen


def load_state(job_dir: Path) -> dict:
    state_file = job_dir / "state.json"
    if state_file.exists():
        with open(state_file) as f:
            return json.load(f)
    return {"completed_steps": [], "created_at": datetime.now().isoformat()}


def save_state(job_dir: Path, state: dict):
    with open(job_dir / "state.json", "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def mark_done(job_dir: Path, step: int, meta: dict = None):
    state = load_state(job_dir)
    if step not in state["completed_steps"]:
        state["completed_steps"].append(step)
    state[f"step{step}_done_at"] = datetime.now().isoformat()
    if meta:
        state[f"step{step}_meta"] = meta
    save_state(job_dir, state)


def is_done(job_dir: Path, step: int) -> bool:
    state = load_state(job_dir)
    return step in state["completed_steps"]


def run_step(cmd: list, step_name: str, env: dict = None, **kw):
    print(f"\n{'='*60}", flush=True)
    print(f"=== Step: {step_name} ===", flush=True)
    print(f"{'='*60}", flush=True)
    print(f">>> {' '.join(str(c) for c in cmd)}\n", flush=True)
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, env=merged_env, **kw)
    if result.returncode != 0:
        print(f"[ERROR] {step_name} 失败，退出码 {result.returncode}", file=sys.stderr)
        sys.exit(result.returncode)


def get_video_duration(video: str) -> float:
    r = subprocess.run(["ffmpeg", "-i", video], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", r.stderr + r.stdout)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return 0.0


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="视频金句短片全流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # 输入源（二选一）
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--url", help="视频 URL（B站/YouTube/抖音等，yt-dlp 支持的平台）")
    src.add_argument("--video", help="本地视频文件路径")

    # Job 控制
    parser.add_argument("--job", help="Job ID（默认自动生成）")
    parser.add_argument("--from-step", type=int, default=1, choices=STEPS, metavar="N",
                        help="从第 N 步开始（1-8，默认 1）")
    parser.add_argument("--to-step", type=int, default=8, choices=STEPS, metavar="N",
                        help="到第 N 步结束（1-8，默认 8）")
    parser.add_argument("--force", action="store_true",
                        help="强制重跑，忽略已完成状态")

    # 内容参数
    parser.add_argument("--speaker", default="演讲者", help="说话人姓名")
    parser.add_argument("--speaker-desc", default="",
                        help="主讲人外貌描述，如'穿黑色西装的中年男性'，提高封面识别准确度")
    parser.add_argument("--speaker-color", default="auto",
                        choices=["auto", "blue", "other"],
                        help="主讲人西装颜色系（封面识别用）：blue=启用零费用颜色规则；other=直接vision；auto=从--speaker-desc推断")
    parser.add_argument("--channel", default="价值投资讲堂", help="频道/栏目名")
    parser.add_argument("--top-n", type=int, default=5, help="输出金句条数")
    parser.add_argument("--language", default="auto",
                        choices=["auto", "zh", "en"],
                        help="视频语言（auto=自动检测）")
    parser.add_argument("--direction", default="auto",
                        choices=["auto", "zh2en", "en2zh", "none"],
                        help="翻译方向（auto=根据语言自动判断，none=不翻译）")
    parser.add_argument("--no-subtitle", action="store_true", help="不烧录字幕")
    parser.add_argument("--vertical", action="store_true",
                        help="输出竖屏 9:16（适配手机端短视频）")

    parser.add_argument("--cookies", default=None,
                        help="Cookie 文件路径（B站/抖音等需要登录的平台）")
    parser.add_argument("--proxy", default=None,
                        help="代理地址，如 http://127.0.0.1:7890（YouTube 等需要）")

    # 模型参数
    parser.add_argument("--whisper-model", default=None,
                        help="Whisper 模型路径（默认自动选 large-v3 或 small）")
    parser.add_argument("--srt-lang", default=None,
                        choices=["zh", "en"],
                        help="SRT 字幕语言（默认根据 --language 推断）")

    args = parser.parse_args()

    # ── 校验 ──────────────────────────────────────────────────────────────────
    api_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
    if not api_key and args.to_step >= 3:
        print("[ERROR] 步骤 3+ 需要 SILICONFLOW_API_KEY", file=sys.stderr)
        sys.exit(1)

    if args.from_step == 1 and not args.url and not args.video:
        print("[ERROR] 从步骤 1 开始需要 --url 或 --video", file=sys.stderr)
        sys.exit(1)

    # ── Job 目录 ───────────────────────────────────────────────────────────────
    if args.job:
        job_id = args.job
    elif args.url:
        # 从 URL 提取视频 ID 作为 job_id
        m = re.search(r"[?&/](BV[\w]+|av\d+|[A-Za-z0-9_-]{11})", args.url)
        job_id = m.group(1) if m else f"job_{int(time.time())}"
    else:
        job_id = Path(args.video).stem if args.video else f"job_{int(time.time())}"

    job_dir = OUTPUT_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    clips_dir = job_dir / "clips"
    clips_dir.mkdir(exist_ok=True)

    print(f"\n[run] Job ID: {job_id}", flush=True)
    print(f"[run] 输出目录: {job_dir}", flush=True)
    print(f"[run] 步骤范围: {args.from_step} ~ {args.to_step}", flush=True)

    # ── 模型选择 ───────────────────────────────────────────────────────────────
    whisper_model = args.whisper_model
    if not whisper_model:
        try:
            whisper_model = detect_whisper_model()
        except RuntimeError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
    print(f"[run] Whisper 模型: {whisper_model}", flush=True)

    # ── 文件路径约定 ────────────────────────────────────────────────────────────
    raw_video  = job_dir / "_raw.mp4"
    full_srt   = job_dir / "full.srt"
    bilingual  = job_dir / "bilingual.json"
    highlights = job_dir / "highlights.json"
    manifest   = job_dir / "manifest.json"
    meta_file  = job_dir / "meta.json"

    # 语言推断
    lang = args.language if args.language != "auto" else "zh"  # 默认中文，ASR 完成后可更新
    srt_lang = args.srt_lang or ("zh" if lang == "zh" else "en")

    # 翻译方向推断
    direction = args.direction
    if direction == "auto":
        direction = "zh2en" if lang == "zh" else "en2zh"

    env = {
        "SILICONFLOW_API_KEY": api_key,
        "SILICONFLOW_MODEL": os.environ.get("SILICONFLOW_MODEL", "Qwen/Qwen3-8B"),
    }
    if args.cookies:
        env["COOKIES_FILE"] = args.cookies
    if args.proxy:
        env["YT_PROXY"] = args.proxy

    # ══════════════════════════════════════════════════════════════════════════
    # Step 1: 采集
    # ══════════════════════════════════════════════════════════════════════════
    if args.from_step <= 1 <= args.to_step:
        if is_done(job_dir, 1) and not args.force:
            print(f"\n[Step 1] ✅ 已完成，跳过", flush=True)
        else:
            run_step(
                [sys.executable, str(STEPS_DIR / "step1_fetch.py"),
                 "--output", str(raw_video),
                 "--meta", str(meta_file),
                 *(["--url", args.url] if args.url else ["--video", args.video])],
                "fetch", env=env,
            )
            mark_done(job_dir, 1)

    # ══════════════════════════════════════════════════════════════════════════
    # Step 2: 转写
    # ══════════════════════════════════════════════════════════════════════════
    if args.from_step <= 2 <= args.to_step:
        if is_done(job_dir, 2) and not args.force:
            print(f"\n[Step 2] ✅ 已完成，跳过", flush=True)
        else:
            # auto 时显式传 auto，让 transcribe.py 真正自动检测（而不是退到默认值）
            asr_lang = ["--language", args.language]
            run_step(
                [sys.executable, str(ROOT / "scripts" / "transcribe.py"),
                 "--input", str(raw_video),
                 "--output", str(full_srt),
                 "--model", whisper_model,
                 "--device", "cpu",
                 "--compute-type", "int8",
                 *asr_lang],
                "transcribe", env=env,
            )
            mark_done(job_dir, 2)

    # ══════════════════════════════════════════════════════════════════════════
    # Step 3: 翻译
    # ══════════════════════════════════════════════════════════════════════════
    if args.from_step <= 3 <= args.to_step:
        if direction == "none":
            print(f"\n[Step 3] 跳过翻译（--direction none）", flush=True)
            mark_done(job_dir, 3)
        elif is_done(job_dir, 3) and not args.force:
            print(f"\n[Step 3] ✅ 已完成，跳过", flush=True)
        else:
            run_step(
                [sys.executable, str(ROOT / "scripts" / "translate.py"),
                 "--input", str(full_srt),
                 "--output", str(bilingual),
                 "--direction", direction,
                 "--batch-size", "20"],
                "translate", env=env,
            )
            mark_done(job_dir, 3)

    # ══════════════════════════════════════════════════════════════════════════
    # Step 4: 金句识别
    # ══════════════════════════════════════════════════════════════════════════
    if args.from_step <= 4 <= args.to_step:
        if is_done(job_dir, 4) and not args.force:
            print(f"\n[Step 4] ✅ 已完成，跳过", flush=True)
        else:
            total_dur = get_video_duration(str(raw_video))
            bi_args = ["--bilingual", str(bilingual)] if bilingual.exists() else []
            run_step(
                [sys.executable, str(ROOT / "scripts" / "highlight.py"),
                 "--srt", str(full_srt),
                 *bi_args,
                 "--output", str(highlights),
                 "--speaker", args.speaker,
                 "--top-n", str(args.top_n),
                 "--total-duration", str(total_dur)],
                "highlight", env=env,
            )
            mark_done(job_dir, 4)

    # ══════════════════════════════════════════════════════════════════════════
    # Step 5: 文案生成
    # ══════════════════════════════════════════════════════════════════════════
    if args.from_step <= 5 <= args.to_step:
        if is_done(job_dir, 5) and not args.force:
            print(f"\n[Step 5] ✅ 已完成，跳过", flush=True)
        else:
            run_step(
                [sys.executable, str(ROOT / "scripts" / "copywrite.py"),
                 "--highlights", str(highlights),
                 "--output", str(manifest),
                 "--speaker", args.speaker,
                 "--channel", args.channel],
                "copywrite", env=env,
            )
            mark_done(job_dir, 5)

    # ══════════════════════════════════════════════════════════════════════════
    # Step 6: 切片+烧录
    # ══════════════════════════════════════════════════════════════════════════
    if args.from_step <= 6 <= args.to_step:
        if is_done(job_dir, 6) and not args.force:
            print(f"\n[Step 6] ✅ 已完成，跳过", flush=True)
        else:
            bi_args = ["--bilingual", str(bilingual)] if bilingual.exists() else []
            sub_args = ["--no-subtitle"] if args.no_subtitle else []
            vert_args = ["--vertical"] if args.vertical else []
            run_step(
                [sys.executable, str(ROOT / "scripts" / "clip.py"),
                 "--video", str(raw_video),
                 "--manifest", str(manifest),
                 "--srt", str(full_srt),
                 *bi_args,
                 "--output-dir", str(clips_dir),
                 "--srt-lang", srt_lang,
                 *sub_args,
                 *vert_args],
                "clip", env=env,
            )
            mark_done(job_dir, 6)

    # ══════════════════════════════════════════════════════════════════════════
    # Step 7: 封面生成
    # ══════════════════════════════════════════════════════════════════════════
    if args.from_step <= 7 <= args.to_step:
        if is_done(job_dir, 7) and not args.force:
            print(f"\n[Step 7] ✅ 已完成，跳过", flush=True)
        else:
            run_step(
                [sys.executable, str(STEPS_DIR / "step7_cover.py"),
                 "--manifest", str(manifest),
                 "--clips-dir", str(clips_dir),
                 "--raw-video", str(raw_video),
                 "--speaker", args.speaker,
                 "--speaker-color", args.speaker_color]
                 + (["--speaker-desc", args.speaker_desc] if args.speaker_desc else []),
                "cover", env=env,
            )
            mark_done(job_dir, 7)

    # ══════════════════════════════════════════════════════════════════════════
    # Step 8: 素材包 + 上传清单
    # ══════════════════════════════════════════════════════════════════════════
    if args.from_step <= 8 <= args.to_step:
        if is_done(job_dir, 8) and not args.force:
            print(f"\n[Step 8] ✅ 已完成，跳过", flush=True)
        else:
            run_step(
                [sys.executable, str(STEPS_DIR / "step8_upload.py"),
                 "--job-dir", str(job_dir),
                 "--manifest", str(manifest),
                 "--clips-dir", str(clips_dir),
                 "--speaker", args.speaker,
                 "--channel", args.channel],
                "upload", env=env,
            )
            mark_done(job_dir, 8)

    # ── 汇总 ──────────────────────────────────────────────────────────────────
    print(f"\n{'='*60}", flush=True)
    print(f"✅ 全部完成！Job: {job_id}", flush=True)
    print(f"   输出目录: {job_dir}", flush=True)
    state = load_state(job_dir)
    print(f"   完成步骤: {state['completed_steps']}", flush=True)
    clips = list(clips_dir.glob("*.mp4"))
    if clips:
        print(f"   短片数量: {len(clips)}", flush=True)
        for c in sorted(clips):
            size = c.stat().st_size / 1024 / 1024
            print(f"     {c.name} ({size:.1f}MB)", flush=True)
    print(f"{'='*60}\n", flush=True)


if __name__ == "__main__":
    main()
