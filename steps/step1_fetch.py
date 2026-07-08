#!/usr/bin/env python3
"""
step1_fetch.py — 采集视频（Step 1）

支持：
  - B站、抖音、YouTube（通过 yt-dlp）
  - 本地文件（直接复制/软链）

输出：
  - _raw.mp4（视频文件）
  - meta.json（标题、作者、时长、分辨率、原链接、平台）
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def get_video_info_ffmpeg(path: str) -> dict:
    """用 ffmpeg 获取视频基本信息"""
    r = subprocess.run(["ffmpeg", "-i", path], capture_output=True, text=True)
    output = r.stderr + r.stdout
    duration = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", output)
    if m:
        duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    width, height = 0, 0
    m = re.search(r"(\d{3,5})x(\d{3,5})", output)
    if m:
        width, height = int(m.group(1)), int(m.group(2))
    return {"duration_sec": round(duration, 2), "width": width, "height": height}


def download_url(url: str, output_path: Path, meta_path: Path):
    """用 yt-dlp 下载视频，提取 meta"""
    print(f"[fetch] 从 URL 下载: {url}", flush=True)

    # 先获取 metadata（不下载视频）
    result = subprocess.run(
        ["yt-dlp", "--dump-json", "--no-playlist", url],
        capture_output=True, text=True
    )
    meta_raw = {}
    if result.returncode == 0 and result.stdout.strip():
        try:
            meta_raw = json.loads(result.stdout.strip().splitlines()[-1])
        except Exception:
            pass

    # 下载视频，优先 mp4，最高 1080p
    print(f"[fetch] 下载视频...", flush=True)
    tmp_path = output_path.parent / "_download_tmp"

    # 检测 cookie 文件
    cookies_file = os.environ.get("COOKIES_FILE", "").strip()
    cookies_args = ["--cookies", cookies_file] if cookies_file and os.path.exists(cookies_file) else []
    if not cookies_args:
        # 尝试常见位置
        for candidate in [
            Path.home() / ".config/bilibili.com.txt",
            Path(__file__).parent.parent / "cookies.txt",
        ]:
            if candidate.exists():
                cookies_args = ["--cookies", str(candidate)]
                print(f"[fetch] 使用 cookie: {candidate}", flush=True)
                break

    # 检测是否需要代理
    proxy = os.environ.get("YT_PROXY", "").strip()
    proxy_args = ["--proxy", proxy] if proxy else []

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", str(tmp_path) + ".%(ext)s",
        *cookies_args,
        *proxy_args,
        url,
    ]
    subprocess.run(cmd, check=True)

    # 找到下载的文件
    found = list(output_path.parent.glob("_download_tmp.*"))
    if not found:
        raise RuntimeError("yt-dlp 下载完成但找不到输出文件")
    dl_file = found[0]

    # 重编码为兼容 mp4（如果不是 mp4 或需要重封装）
    if dl_file.suffix.lower() != ".mp4" or dl_file.name.endswith(".webm"):
        print(f"[fetch] 重封装为 mp4...", flush=True)
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(dl_file),
             "-c:v", "libx264", "-crf", "18", "-preset", "fast",
             "-c:a", "aac", str(output_path)],
            check=True
        )
        dl_file.unlink()
    else:
        shutil.move(str(dl_file), str(output_path))

    # 获取视频信息
    info = get_video_info_ffmpeg(str(output_path))
    meta = {
        "source": "url",
        "url": url,
        "platform": meta_raw.get("extractor_key", "unknown"),
        "title": meta_raw.get("title", ""),
        "uploader": meta_raw.get("uploader", ""),
        "upload_date": meta_raw.get("upload_date", ""),
        "webpage_url": meta_raw.get("webpage_url", url),
        "duration_sec": info["duration_sec"],
        "width": info["width"],
        "height": info["height"],
        "file": str(output_path),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[fetch] ✅ 下载完成: {output_path} ({output_path.stat().st_size/1024/1024:.1f}MB)", flush=True)
    print(f"[fetch]    时长: {info['duration_sec']:.1f}s, 分辨率: {info['width']}x{info['height']}", flush=True)


def use_local(video_path: str, output_path: Path, meta_path: Path):
    """使用本地文件"""
    src = Path(video_path)
    if not src.exists():
        raise FileNotFoundError(f"本地文件不存在: {video_path}")
    print(f"[fetch] 使用本地文件: {src}", flush=True)

    if src.resolve() != output_path.resolve():
        if src.suffix.lower() == ".mp4":
            # 软链或复制
            if not output_path.exists():
                shutil.copy2(str(src), str(output_path))
                print(f"[fetch] 已复制到: {output_path}", flush=True)
        else:
            # 需要重封装
            print(f"[fetch] 非 mp4 格式，重封装...", flush=True)
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(src),
                 "-c:v", "libx264", "-crf", "18", "-preset", "fast",
                 "-c:a", "aac", str(output_path)],
                check=True
            )

    info = get_video_info_ffmpeg(str(output_path))
    meta = {
        "source": "local",
        "original_path": str(src.resolve()),
        "title": src.stem,
        "duration_sec": info["duration_sec"],
        "width": info["width"],
        "height": info["height"],
        "file": str(output_path),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[fetch] ✅ 完成: {output_path} ({output_path.stat().st_size/1024/1024:.1f}MB)", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Step 1: 视频采集")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="视频 URL")
    src.add_argument("--video", help="本地视频路径")
    parser.add_argument("--output", required=True, help="输出 _raw.mp4 路径")
    parser.add_argument("--meta", required=True, help="输出 meta.json 路径")
    args = parser.parse_args()

    output_path = Path(args.output)
    meta_path = Path(args.meta)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.url:
        download_url(args.url, output_path, meta_path)
    else:
        use_local(args.video, output_path, meta_path)


if __name__ == "__main__":
    main()
