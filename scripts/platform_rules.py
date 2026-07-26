#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B 站 / 抖音投稿字段的本地校验与规范化。

设计原则
--------
1. **提交前就把字段修合规**，不要指望平台接口自动截断。超限直接报错会让
   整条流水线在最后一步白跑，代价太高。
2. **数字都标注出处**。分两类：
   - ``[官方]``  有官方或权威文档佐证
   - ``[社区]``  社区经验/实测，未见官方文档，规则可能变
   所有阈值都做成模块级常量，规则变了只改这里。
3. **繁转简**。目标平台是 B 站/抖音，受众以简体中文为主；而 Whisper 对
   中文源的输出经常是繁体（实测台湾财经视频转写出「公佈」「調到了」），
   翻译模型也可能回繁体。统一走一道 zhconv。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

# ---------------------------------------------------------------- B 站阈值
BILI_TITLE_HARD_MAX = 80          # [官方] 超过接口直接报错
BILI_TITLE_SOFT_MAX = 25          # [社区] 首页/榜单展示截断位置，超了不报错但显示不全
BILI_DESC_MAX = 2000              # [官方]
BILI_TAG_MAX_COUNT = 10           # [官方]
BILI_TAG_MAX_LEN = 20             # [社区] 单个标签字数，社区反馈值
BILI_COVER_MAX_BYTES = 2 * 1024 * 1024   # [社区] 留足压缩余量，官方未给精确值

# ---------------------------------------------------------------- 抖音阈值
DOUYIN_TITLE_MAX = 55             # [社区] 网页投稿框实测
DOUYIN_DESC_MAX = 2200            # [社区]
DOUYIN_MAX_DURATION = 15 * 60     # [官方] 开放平台文档：≤15 分钟
DOUYIN_MIN_HEIGHT = 720           # [官方] 分辨率不低于 720p

# 文件名里平台/文件系统都不待见的字符
_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\n\r\t]')


# ====================================================================
# 繁 → 简
# ====================================================================
def to_simplified(text: str, protect=None) -> str:
    """繁体转简体。zhconv 缺失时原样返回，不让它成为流水线的硬依赖。

    ``protect`` 是不参与转换的专有名词列表。这个参数不是可有可无的：
    实测频道名「股乾爹」会被通用规则转成「股干爹」——「乾爹→干爹」在
    一般文本里没错，但它是个品牌名，改了就是错字。人名、频道名、书名
    这类专名都得原样留住，所以先挖坑占位、转换完再填回去。
    """
    if not text:
        return text
    try:
        import zhconv
    except ImportError:
        return text
    terms = [t for t in (protect or []) if t and t in text]
    # 占位符用私有区字符，正常文案里不会出现，也不会被 zhconv 改写。
    holders = {}
    for i, t in enumerate(sorted(terms, key=len, reverse=True)):
        h = f"\ue000{i}\ue001"
        holders[h] = t
        text = text.replace(t, h)
    text = zhconv.convert(text, "zh-cn")
    for h, t in holders.items():
        text = text.replace(h, t)
    return text


def simplify_srt(path: str) -> int:
    """就地把 SRT 的字幕正文转成简体，返回改动的行数。

    只动正文：序号行和 ``-->`` 时间码行原样保留，避免破坏时间轴。
    """
    try:
        import zhconv  # noqa: F401
    except ImportError:
        print("[rules] 未安装 zhconv，跳过繁简转换")
        return 0

    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")

    changed = 0
    out = []
    for ln in lines:
        s = ln.strip()
        if not s or "-->" in s or s.isdigit():
            out.append(ln)
            continue
        conv = to_simplified(ln)
        if conv != ln:
            changed += 1
        out.append(conv)

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
    print(f"[rules] 繁简转换：{path} 改动 {changed} 行")
    return changed


# ====================================================================
# 标题 / 标签 / 简介
# ====================================================================
def clean_title(title: str, platform: str = "bilibili") -> tuple[str, list[str]]:
    """规范化标题，返回 ``(标题, 警告列表)``。

    硬上限强制截断；软上限只告警，因为完整标题会被放进简介首行兜住。
    """
    warns: list[str] = []
    t = to_simplified(str(title or "").strip())
    t = re.sub(r"\s+", " ", t)

    hard = BILI_TITLE_HARD_MAX if platform == "bilibili" else DOUYIN_TITLE_MAX
    if len(t) > hard:
        warns.append(f"标题 {len(t)} 字超过 {platform} 硬上限 {hard}，已截断")
        t = t[:hard]
    elif platform == "bilibili" and len(t) > BILI_TITLE_SOFT_MAX:
        warns.append(
            f"标题 {len(t)} 字超过展示建议长度 {BILI_TITLE_SOFT_MAX}，"
            f"首页可能显示不全（完整标题已写入简介首行）")

    if not t:
        warns.append("标题为空")
    return t, warns


def clean_tags(tags, platform: str = "bilibili") -> tuple[list[str], list[str]]:
    """去空、去重、繁转简、限长、限数量。"""
    warns: list[str] = []
    seen: set[str] = set()
    out: list[str] = []

    for raw in (tags or []):
        tag = to_simplified(str(raw or "").strip())
        # B 站标签里出现逗号会被当分隔符，直接剔掉
        tag = tag.replace(",", "").replace("，", "").strip()
        if not tag:
            continue
        if len(tag) > BILI_TAG_MAX_LEN:
            warns.append(f"标签「{tag}」{len(tag)} 字超上限 {BILI_TAG_MAX_LEN}，已截断")
            tag = tag[:BILI_TAG_MAX_LEN]
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)

    if len(out) > BILI_TAG_MAX_COUNT:
        warns.append(f"标签 {len(out)} 个超上限 {BILI_TAG_MAX_COUNT}，保留前 {BILI_TAG_MAX_COUNT} 个")
        out = out[:BILI_TAG_MAX_COUNT]
    if not out:
        warns.append("标签为空，B 站要求至少 1 个")
        out = ["财经"]
    return out, warns


def build_desc(body: str, source: str = "", full_title: str = "",
               clip_range: str = "", platform: str = "bilibili") -> tuple[str, list[str]]:
    """拼装简介。

    转载来源声明是**默认行为而不是可选项** —— B 站公约明确「未经授权添加的
    翻译字幕不属于自制」，本流水线只能走转载，来源标注漏了就可能被退回。
    """
    warns: list[str] = []
    parts: list[str] = []

    # 完整标题兜在首行，弥补标题被展示截断
    if full_title and len(full_title) > BILI_TITLE_SOFT_MAX:
        parts.append(full_title)

    if body:
        parts.append(to_simplified(str(body).strip()))

    if clip_range:
        parts.append(f"片段时间：{clip_range}")

    if source:
        parts.append(f"原视频出处：{source}")
    else:
        warns.append("缺少 source，转载稿件必须标注来源")

    parts.append("本片仅做翻译搬运与片段剪辑，版权归原作者所有。"
                 "如原作者不希望本片传播，请联系我立即删除。")

    desc = "\n\n".join(p for p in parts if p)
    cap = BILI_DESC_MAX if platform == "bilibili" else DOUYIN_DESC_MAX
    if len(desc) > cap:
        warns.append(f"简介 {len(desc)} 字超上限 {cap}，已截断")
        # 截断时保底留住来源声明，宁可砍正文
        tail = "\n\n原视频出处：" + source if source else ""
        tail += "\n\n本片仅做翻译搬运，版权归原作者所有。"
        keep = cap - len(tail)
        desc = desc[:max(keep, 0)] + tail
    return desc, warns


def safe_filename(name: str, maxlen: int = 40) -> str:
    """清掉非法字符，截断，避免空名。"""
    n = _BAD_FILENAME.sub("", to_simplified(str(name or "").strip()))
    n = re.sub(r"\s+", " ", n).strip()[:maxlen].strip()
    return n or "video"


# ====================================================================
# 封面 / 视频 体检
# ====================================================================
def normalize_cover(path: str, out_path: str = "",
                    max_bytes: int = BILI_COVER_MAX_BYTES) -> tuple[str, list[str]]:
    """把封面压成 B 站能收的 JPG。

    不去追某个「官方精确像素」（查不到权威出处），只保证：JPG 编码、
    16:9 左右的比例、体积留余量。
    """
    warns: list[str] = []
    if not path or not os.path.exists(path):
        return path, ["封面文件不存在"]

    try:
        from PIL import Image
    except ImportError:
        return path, ["未安装 Pillow，跳过封面规范化"]

    out_path = out_path or os.path.splitext(path)[0] + "_bili.jpg"
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        ratio = w / h if h else 0
        if not (1.5 <= ratio <= 1.9):
            warns.append(f"封面比例 {w}x{h}（{ratio:.2f}）偏离 16:9，已居中裁切")
            target = 16 / 9
            if ratio > target:      # 太宽，裁左右
                nw = int(h * target)
                im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
            else:                   # 太高，裁上下
                nh = int(w / target)
                im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))

        # 逐级降质直到进预算
        for q in (92, 85, 78, 70, 60, 50):
            im.save(out_path, "JPEG", quality=q, optimize=True)
            if os.path.getsize(out_path) <= max_bytes:
                break
        else:
            warns.append(f"封面压到最低画质仍有 {os.path.getsize(out_path)} 字节，"
                         f"超出 {max_bytes}")

    print(f"[rules] 封面规范化 -> {out_path} "
          f"({os.path.getsize(out_path)//1024} KB)")
    return out_path, warns


def probe_video(path: str) -> dict:
    """用 ffprobe 读宽高与时长，失败返回空 dict（不抛异常）。"""
    if not shutil.which("ffprobe") or not os.path.exists(path):
        return {}
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=0", path],
            capture_output=True, text=True, timeout=60)
        info: dict = {}
        for ln in r.stdout.strip().split("\n"):
            if "=" in ln:
                k, v = ln.split("=", 1)
                try:
                    info[k] = float(v) if k == "duration" else int(v)
                except ValueError:
                    pass
        return info
    except Exception:
        return {}


def validate_video(path: str, platform: str = "douyin") -> list[str]:
    """发布前的视频体检，只告警不阻断。"""
    warns: list[str] = []
    if not os.path.exists(path):
        return [f"视频不存在：{path}"]

    info = probe_video(path)
    if not info:
        return ["ffprobe 读取失败，跳过视频体检"]

    dur = info.get("duration", 0)
    w, h = info.get("width", 0), info.get("height", 0)

    if platform == "douyin":
        if dur > DOUYIN_MAX_DURATION:
            warns.append(f"时长 {dur:.0f}s 超过抖音上限 {DOUYIN_MAX_DURATION}s")
        # 竖屏看宽，横屏看高，取长边判断清晰度
        short_side = min(w, h)
        if short_side < DOUYIN_MIN_HEIGHT:
            warns.append(f"分辨率 {w}x{h} 短边 {short_side} 低于 720p 要求")
    return warns


# ====================================================================
# 分区
# ====================================================================
# [官方] tid 取自 bilibili-API-collect 分区一览（原仓库 2026-01-30 已归档，
# 数据经镜像文档 + biliup tid-ref + bilitool 三方交叉验证一致）。
# 知识区(36) 下：
#   201 科学科普 / 124 社科·法律·心理 / 228 人文历史 / 207 财经商业
#   208 校园学习 / 209 职业职场 / 229 设计·创意 / 122 野生技术协会
# 坑：投资/股市类属「财经商业 207」。旧版代码写的 208 是「校园学习」，
# 相当于把股市内容发进了学习区，会直接影响推荐。
BILI_TID_FINANCE = 207        # 知识 · 财经商业
BILI_TID_SCIENCE = 201        # 知识 · 科学科普
BILI_TID_TECH = 188           # 数码 · 科技数码
BILI_TID_NEWS = 204           # 资讯 · 环球

BILI_PARTITION_RULES = [
    (["投资", "股票", "巴菲特", "芒格", "价值", "基金", "港股", "A股",
      "股市", "财报", "估值", "帕伯莱", "李录"], BILI_TID_FINANCE, "财经商业"),
    (["财经", "经济", "GDP", "通胀", "美联储", "利率", "衰退",
      "汇率", "商业", "创业", "企业家", "CEO"], BILI_TID_FINANCE, "财经商业"),
    (["科技", "AI", "人工智能", "芯片", "新能源"], BILI_TID_TECH, "科技数码"),
]


def suggest_partition(title: str, desc: str = "") -> tuple[int, str]:
    """按关键词猜分区。投资访谈类内容兜底也走财经商业，不落到科学科普。"""
    text = f"{title}{desc}"
    for keywords, tid, name in BILI_PARTITION_RULES:
        if any(kw in text for kw in keywords):
            return tid, name
    return BILI_TID_FINANCE, "财经商业"


# ====================================================================
def report(warns: list[str], label: str = "") -> None:
    """统一打印告警。静默失败是这条流水线最贵的 bug，宁可吵一点。"""
    if not warns:
        return
    head = f"[rules] {label} 校验提示：" if label else "[rules] 校验提示："
    print(head)
    for w in warns:
        print(f"  ⚠ {w}")


if __name__ == "__main__":
    # 自检
    t, w = clean_title("米爾肯：大選後全面買入的反常識邏輯，这个标题特意写得非常非常长用来触发展示截断告警")
    print("标题:", t); report(w, "标题")
    tg, w = clean_tags(["投資", "价值投资", "", "价值投资", "这个标签超长" * 5, ",逗号"])
    print("标签:", tg); report(w, "标签")
    d, w = clean_title("很短的标题")
    print("短标题:", d); report(w, "标题")
    ds, w = build_desc("正文内容", source="YouTube https://youtu.be/xxx",
                       full_title="一个很长的完整标题" * 4, clip_range="00:10:00-00:11:30")
    print("---简介---"); print(ds); report(w, "简介")
    print("分区:", suggest_partition("米尔肯：大选后全面买入的反常识逻辑"))
