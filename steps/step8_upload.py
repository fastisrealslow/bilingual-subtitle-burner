#!/usr/bin/env python3
"""
step8_upload.py — 素材包打包 + B站上传清单生成（Step 8）

输出：
  job_dir/
    upload_list.json     所有短片的上传信息清单
    upload_list.md       人类可读的上传清单
    package/             B站上传素材包（每条短片一个子目录）
      01_{title}/
        video.mp4        → 短片视频（复制）
        cover.jpg        → 封面（如存在）
        info.json        → 标题/简介/标签/分区建议
        biliup.toml      → biliup 配置文件（可直接用）
      02_{title}/
        ...

B站分区建议：
  - 财经类：分区 ID 208（股市）或 207（财经资讯）
  - 知识类：分区 ID 201
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# 字段阀值、分区表、繁简转换统一放在 platform_rules，避免多处硬编码不一致。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import platform_rules as PR  # noqa: E402

suggest_partition = PR.suggest_partition


def make_biliup_toml(title: str, desc: str, tags: list, cover: str,
                     video_path: str, tid: int, source: str = "",
                     copyright_type: int = 2) -> str:
    """生成 biliup 配置文件内容。

    两个踩过的坑：
    - ``source`` 必须是真实来源平台 + 链接。早期版本写的是“金句精选”
      这种栅目名，转载稿会被判来源标注不清。
    - ``no_reprint``（禁止转载）只对自制稿成立。自己就是转载还挂禁止转载
      是自相矛盾的，转载稿必须置 0。
    """
    tags_str = ",".join(tags)
    cover_line = f'cover = "{cover}"' if cover else '# cover = "cover.jpg"'
    desc_safe = desc.replace('"""', '""')
    src_line = f'source = "{source}"' if source else '# source 缺失！转载稿必填'
    return f"""# biliup 配置文件 - 自动生成
# 使用方法: biliup upload video.mp4 --config biliup.toml

[upload]
title = "{title}"
desc = \"\"\"{desc_safe}\"\"\"
tid = {tid}
tag = "{tags_str}"
{cover_line}
copyright = {copyright_type}
{src_line}
no_reprint = {0 if copyright_type == 2 else 1}
open_elec = 0
"""


def find_biliup() -> list | None:
    """定位可用的 B站上传 CLI，返回命令前缀数组。优先 biliup-rs（biliup 二进制），其次 biliupload。"""
    import shutil as _sh
    # biliup-rs 的可执行名也叫 biliup；优先环境变量指定路径
    env_bin = (os.environ.get("BILIUP_BIN") or "").strip()
    if env_bin and Path(env_bin).exists():
        return [env_bin]
    for name in ("biliup", "biliupR"):
        p = _sh.which(name)
        if p:
            return [p]
    # biliupload （pip 包，纯 Python）
    p = _sh.which("biliupload")
    if p:
        return [p]
    # 作为模块调用兑底
    try:
        import biliup  # noqa
        return [sys.executable, "-m", "biliup"]
    except ImportError:
        pass
    return None


def upload_bilibili(cli: list, video: Path, cover: str, title: str, desc: str,
                    tags: list, tid: int, cookies: str, copyright_type: int = 2,
                    source: str = "", line: str = "") -> bool:
    """调用 biliup CLI 上传单个视频到 B站。返回是否成功。

    兼容 biliup-rs 与 biliupload 两种 CLI 的参数风格。
    copyright_type: 1=自制, 2=转载（搬运国外视频应用 2 并注明来源）
    """
    # 提交前把字段修合规。超限直接报错会让整条流水线在最后一步白跑。
    title, w1 = PR.clean_title(title, "bilibili")
    tags, w2 = PR.clean_tags(tags, "bilibili")
    if len(desc) > PR.BILI_DESC_MAX:
        w2.append(f"简介 {len(desc)} 字超上限 {PR.BILI_DESC_MAX}，已截断")
        desc = desc[:PR.BILI_DESC_MAX]
    if copyright_type == 2 and not source:
        w2.append("copyright=2（转载）但 source 为空，很可能被审核退回")
    PR.report(w1 + w2, "B 站投稿字段")

    tags_str = ",".join(tags) or "投资,价值投资"
    is_rs = cli[0].endswith("biliup") or cli[0].endswith("biliupR") or "-m" in cli
    is_pip = cli[0].endswith("biliupload")

    if is_pip:
        # biliupload upload <video> --title ... --tid ... --tag ... [--cover] [--copyright] [--source]
        cmd = cli + ["upload", str(video),
                     "--title", title,
                     "--desc", desc,
                     "--tid", str(tid),
                     "--tag", tags_str,
                     "--copyright", str(copyright_type)]
        if cover:
            cmd += ["--cover", cover]
        if copyright_type == 2 and source:
            cmd += ["--source", source]
        if line:
            cmd += ["--line", line]
    else:
        # biliup-rs: biliup -u cookies.json upload <video> --title ... --tid ... --tag ... [--cover]
        pre = list(cli)
        if cookies:
            pre += ["-u", cookies]
        cmd = pre + ["upload", str(video),
                     "--title", title,
                     "--desc", desc,
                     "--tid", str(tid),
                     "--tag", tags_str,
                     "--copyright", str(copyright_type)]
        if cover:
            cmd += ["--cover", cover]
        if copyright_type == 2 and source:
            cmd += ["--source", source]
        if line:
            cmd += ["--line", line]

    print(f"[upload]   ▶ 上传中: {title[:40]}...", flush=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode == 0:
            print(f"[upload]   ✅ B站上传成功", flush=True)
            return True
        print(f"[upload]   ❌ B站上传失败 (code={r.returncode})", flush=True)
        print(f"[upload]      stderr: {(r.stderr or '')[-500:]}", flush=True)
        return False
    except subprocess.TimeoutExpired:
        print(f"[upload]   ❌ 上传超时", flush=True)
        return False
    except Exception as e:
        print(f"[upload]   ❌ 上传异常: {e}", flush=True)
        return False


# GitHub Actions runner 是海外 IP，大概率走不了境内 bda2 线路，而是落到
# bupfetch（qn/kodo/gcs）。单线路失败率不低，所以依次试备用线路。
BILI_LINE_FALLBACKS = ["", "qn", "ws", "bda2"]


def upload_bilibili_with_retry(cli: list, video: Path, cover: str, title: str,
                               desc: str, tags: list, tid: int, cookies: str,
                               copyright_type: int = 2, source: str = "",
                               line: str = "", max_attempts: int = 3) -> bool:
    """带线路降级的上传。用户显式指定了 --bili-line 就只试该线路。"""
    lines = [line] if line else BILI_LINE_FALLBACKS
    lines = lines[:max_attempts]

    for i, ln in enumerate(lines, 1):
        label = ln or "自动测速"
        print(f"[upload]   尝试 {i}/{len(lines)}（线路={label}）", flush=True)
        if upload_bilibili(cli, video, cover, title, desc, tags, tid, cookies,
                           copyright_type, source, ln):
            return True
        if i < len(lines):
            wait = 10 * i
            print(f"[upload]   等 {wait}s 后换线路重试", flush=True)
            time.sleep(wait)

    print(f"[upload]   ❌ {len(lines)} 条线路全部失败，放弃本条", flush=True)
    return False


def main():
    parser = argparse.ArgumentParser(description="Step 8: 素材包打包 + B站自动上传")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--clips-dir", required=True)
    parser.add_argument("--speaker", default="演讲者")
    parser.add_argument("--channel", default="价值投资讲堂")
    # ── 真实上传开关 ──
    parser.add_argument("--do-upload", action="store_true",
                        help="实际调用 biliup 上传到 B站（不加则只生成素材包）")
    parser.add_argument("--bili-cookies", default="",
                        help="B站 cookies.json 路径（biliup-rs 登录产物）；也可由 BILI_COOKIES 环境变量指定")
    parser.add_argument("--copyright", type=int, default=2, choices=[1, 2],
                        help="1=自制 2=转载（搬运国外视频默认 2）")
    parser.add_argument("--source", default="",
                        help="转载来源（copyright=2 时必填，如原 YouTube 链接）")
    parser.add_argument("--line", default="", help="B站上传线路 bda2/ws/qn/tx 等")
    parser.add_argument("--upload-interval", type=int, default=30,
                        help="多条上传间隔秒数（防风控）")
    args = parser.parse_args()

    # cookies 优先用参数，其次环境变量
    bili_cookies = args.bili_cookies or (os.environ.get("BILI_COOKIES") or "").strip()

    job_dir = Path(args.job_dir)
    clips_dir = Path(args.clips_dir)

    with open(args.manifest, encoding="utf-8") as f:
        manifest = json.load(f)

    # 创建素材包目录
    pkg_dir = job_dir / "package"
    pkg_dir.mkdir(exist_ok=True)

    upload_list = []
    md_lines = [
        f"# 上传清单 — {args.speaker} 金句精选",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"频道：{args.channel}",
        "",
        "---",
        "",
    ]

    for item in manifest:
        rank = item["rank"]
        title = item.get("title", f"clip_{rank}")
        desc = item.get("desc", item.get("copywrite", ""))
        tags = item.get("tags", [args.speaker, args.channel, "价值投资"])
        safe = PR.safe_filename(title)

        # 标题/标签先过一道规范化（繁转简 + 限长 + 去重），让素材包里的
        # info.json、biliup.toml、Markdown 清单与真正提交的字段一致。
        title, _tw = PR.clean_title(title, "bilibili")
        tags, _gw = PR.clean_tags(tags, "bilibili")
        PR.report(_tw + _gw, f"[{rank:02d}] 字段")

        # 找视频文件
        mp4_files = list(clips_dir.glob(f"{rank:02d}_*.mp4"))
        cover_files = list(clips_dir.glob(f"{rank:02d}_cover.jpg"))

        if not mp4_files:
            print(f"[upload] [{rank:02d}] 找不到视频文件，跳过", flush=True)
            continue

        mp4_file = mp4_files[0]
        cover_file = cover_files[0] if cover_files else None

        # 创建子目录
        sub_dir = pkg_dir / f"{rank:02d}_{safe}"
        sub_dir.mkdir(exist_ok=True)

        # 复制视频和封面
        dst_video = sub_dir / "video.mp4"
        shutil.copy2(mp4_file, dst_video)

        cover_str = ""
        if cover_file:
            dst_cover = sub_dir / "cover.jpg"
            shutil.copy2(cover_file, dst_cover)
            # 压成 16:9 JPG 并控在 2MB 内，就地覆盖
            fixed, _cw = PR.normalize_cover(str(dst_cover), str(dst_cover))
            PR.report(_cw, f"[{rank:02d}] 封面")
            cover_str = "cover.jpg"

        # 推断分区
        tid, partition_name = suggest_partition(title, desc)

        # 完整简介。转载来源声明由 build_desc 强制拼上，B 站公约明确
        # “未经授权添加的翻译字幕不属于自制”，本流水线只能走转载。
        body = desc
        if args.speaker:
            body = f"{body}\n\n主讲：{args.speaker}" if body else f"主讲：{args.speaker}"
        clip_range = (f"{item.get('clip_start', '')}～{item.get('clip_end', '')}"
                      if item.get("clip_start") else "")
        full_desc, _dw = PR.build_desc(
            body=body,
            source=args.source or args.channel,
            full_title=item.get("title", title),
            clip_range=clip_range,
            platform="bilibili",
        )
        PR.report(_dw, f"[{rank:02d}] 简介")

        # info.json
        info = {
            "rank": rank,
            "title": title,
            "desc": full_desc,
            "tags": tags,
            "tid": tid,
            "partition": partition_name,
            "cover": cover_str,
            "video": "video.mp4",
            "duration_sec": item.get("clip_duration_sec", 0),
            "score": item.get("score", 0),
        }
        with open(sub_dir / "info.json", "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)

        # biliup.toml
        toml_content = make_biliup_toml(
            title=title,
            desc=full_desc,
            tags=tags,
            cover=cover_str,
            video_path="video.mp4",
            tid=tid,
            source=args.source or args.channel,
            copyright_type=args.copyright,
        )
        with open(sub_dir / "biliup.toml", "w", encoding="utf-8") as f:
            f.write(toml_content)

        size_mb = mp4_file.stat().st_size / 1024 / 1024
        upload_list.append({**info, "package_dir": str(sub_dir), "size_mb": round(size_mb, 1)})

        # Markdown 清单
        md_lines.extend([
            f"## [{rank:02d}] {title}",
            f"- **评分**：{item.get('score', 'N/A')}",
            f"- **时段**：{item.get('clip_start', '')} ～ {item.get('clip_end', '')}",
            f"- **时长**：{item.get('clip_duration_sec', 0):.0f}s",
            f"- **分区**：{partition_name}（tid={tid}）",
            f"- **标签**：{', '.join(tags[:5])}",
            f"- **视频**：`{mp4_file.name}` ({size_mb:.1f}MB)",
            f"- **封面**：{'✅' if cover_file else '❌ 无封面'}",
            "",
            f"**简介**：",
            f"```",
            full_desc[:300],
            f"```",
            "",
            f"**biliup 上传命令**：",
            f"```bash",
            f"cd {sub_dir}",
            f"biliup upload video.mp4 --config biliup.toml",
            f"```",
            "",
            "---",
            "",
        ])

        print(f"[upload] ✅ [{rank:02d}] {title} → {sub_dir.name}", flush=True)

    # 保存清单
    with open(job_dir / "upload_list.json", "w", encoding="utf-8") as f:
        json.dump(upload_list, f, ensure_ascii=False, indent=2)

    with open(job_dir / "upload_list.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    print(f"\n[upload] 素材包: {pkg_dir}", flush=True)
    print(f"[upload] 上传清单: {job_dir / 'upload_list.md'}", flush=True)
    print(f"[upload] 共 {len(upload_list)} 条视频准备就绪", flush=True)

    # ───── 真实上传阶段（仅 --do-upload） ─────
    if not args.do_upload:
        print("[upload] 未开启 --do-upload，仅生成素材包（不上传）。", flush=True)
        return

    print("\n[upload] ========== 开始 B站自动上传 ==========", flush=True)
    if not bili_cookies or not Path(bili_cookies).exists():
        print(f"[upload] ❌ 未找到 B站 cookies（--bili-cookies / BILI_COOKIES）：{bili_cookies!r}", flush=True)
        print("[upload]    请先用 `biliup login` 扫码登录生成 cookies.json，并存入 GitHub Secrets。", flush=True)
        sys.exit(2)

    cli = find_biliup()
    if not cli:
        print("[upload] ❌ 未找到 biliup / biliupload CLI。请 `pip install biliup` 或安装 biliup-rs。", flush=True)
        sys.exit(3)
    print(f"[upload] 使用上传器: {' '.join(cli)}", flush=True)

    ok, fail = 0, 0
    results = []
    for i, entry in enumerate(upload_list):
        sub_dir = Path(entry["package_dir"])
        video = sub_dir / "video.mp4"
        cover = "cover.jpg" if (sub_dir / "cover.jpg").exists() else ""
        # 在素材子目录内执行，便于 cover 相对路径
        cwd = os.getcwd()
        success = False   # chdir 失败时也得有值，不能让 finally 后面撞 UnboundLocal
        try:
            os.chdir(sub_dir)
            success = upload_bilibili_with_retry(
                cli=cli,
                video=Path("video.mp4"),
                cover=cover,
                title=entry["title"],
                desc=entry["desc"],
                tags=entry["tags"],
                tid=entry["tid"],
                cookies=bili_cookies if not str(cli[0]).endswith("biliupload") else "",
                copyright_type=args.copyright,
                source=args.source or args.channel,
                line=args.line,
            )
        except Exception as e:
            # 单条失败不能拖死剩下的稿子
            print(f"[upload]   ❌ [{entry['rank']:02d}] 上传环节异常：{e}", flush=True)
            success = False
        finally:
            os.chdir(cwd)

        results.append({"rank": entry["rank"], "title": entry["title"], "uploaded": success})
        if success:
            ok += 1
        else:
            fail += 1
        # 防风控间隔
        if i < len(upload_list) - 1 and args.upload_interval > 0:
            time.sleep(args.upload_interval)

    # 上传结果存档
    with open(job_dir / "upload_result.json", "w", encoding="utf-8") as f:
        json.dump({"ok": ok, "fail": fail, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n[upload] ========== B站上传完成：成功 {ok} / 失败 {fail} ==========", flush=True)
    if fail > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
