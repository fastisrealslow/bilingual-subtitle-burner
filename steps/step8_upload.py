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
import re
import shutil
from datetime import datetime
from pathlib import Path


# B站分区建议（关键词匹配）
BILI_PARTITION_RULES = [
    (["投资", "股票", "巴菲特", "价值", "基金", "港股", "A股"], 208, "股市"),
    (["财经", "经济", "GDP", "通胀", "美联储"], 207, "财经资讯"),
    (["科技", "AI", "人工智能", "芯片", "新能源"], 188, "科技"),
    (["创业", "商业", "企业家", "CEO"], 207, "财经资讯"),
]


def suggest_partition(title: str, desc: str = "") -> tuple:
    text = title + desc
    for keywords, tid, name in BILI_PARTITION_RULES:
        if any(kw in text for kw in keywords):
            return tid, name
    return 201, "知识"  # 默认


def make_biliup_toml(title: str, desc: str, tags: list, cover: str,
                     video_path: str, tid: int) -> str:
    """生成 biliup 配置文件内容"""
    tags_str = ",".join(tags[:10])  # B站最多10个标签
    cover_line = f'cover = "{cover}"' if cover else '# cover = "cover.jpg"'
    desc_safe = desc[:2000].replace('"""', '""')
    return f"""# biliup 配置文件 - 自动生成
# 使用方法: biliup upload video.mp4 --config biliup.toml

[upload]
title = "{title[:80]}"
desc = \"\"\"{desc_safe}\"\"\"
tid = {tid}
tag = "{tags_str}"
{cover_line}
source = "金句精选"
no_reprint = 1
open_elec = 0
"""


def main():
    parser = argparse.ArgumentParser(description="Step 8: 素材包打包 + 上传清单")
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--clips-dir", required=True)
    parser.add_argument("--speaker", default="演讲者")
    parser.add_argument("--channel", default="价值投资讲堂")
    args = parser.parse_args()

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
        safe = re.sub(r'[\\/:*?"<>|]', '_', title).strip()[:40]

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
            cover_str = "cover.jpg"

        # 推断分区
        tid, partition_name = suggest_partition(title, desc)

        # 完整简介（包含片段信息）
        full_desc = f"{desc}\n\n" if desc else ""
        full_desc += f"片段时间：{item.get('clip_start', '')}～{item.get('clip_end', '')}\n"
        full_desc += f"来源：{args.channel}\n"
        full_desc += f"主讲：{args.speaker}"

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


if __name__ == "__main__":
    main()
