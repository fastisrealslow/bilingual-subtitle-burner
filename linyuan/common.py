#!/usr/bin/env python3
"""公共常量与工具。

抽出来的原因：UA / Referer 之前在 5 个脚本里各写一份，
改一处漏一处（比如给微博加 Referer 时就漏过 check_links.py）。
"""

# 桌面端 UA。部分站点（B站、网易、腾讯）对移动 UA 返回简化页面
UA_PC = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 移动端 UA。抖音分享页、好看视频播放页必须用移动 UA 才返回完整数据
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
             "Mobile/15E148 Safari/604.1")

# 各平台 CDN 的防盗链要求。
# 微博尤其严格：缺 Referer 直接 403，这是之前误判"链接失效"的根因。
REFERER = {
    "weibo_search": "https://weibo.com/",
    "tencent_live": "https://v.qq.com/",
    "douyin_video": "https://www.douyin.com/",
    "haokan_video": "https://haokan.baidu.com/",
    "netease_video": "https://www.163.com/",
    "yicai_video": "https://www.yicai.com/",
    "bilibili_search": "https://www.bilibili.com/",
}

# 来源代码 → 中文名
SRC_NAME = {
    "bilibili_search": "B站",
    "bilibili_api": "B站",
    "bilibili_space": "B站",
    "weibo_search": "微博",
    "tencent_live": "腾讯新闻",
    "xueqiu_search": "雪球",
    "douyin_video": "抖音",
    "douyin_search": "抖音",
    "haokan_video": "好看视频",
    "netease_video": "网易",
    "yicai_video": "第一财经",
    "shareholder_meeting": "股东大会",
}


def http_get(url, referer=None, ua=None, timeout=25):
    """纯 HTTP GET，不依赖浏览器。"""
    import urllib.request
    headers = {
        "User-Agent": ua or UA_PC,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def to_sec(d):
    """时长归一化为秒。接受 int / "M:SS" / "H:MM:SS"。"""
    if isinstance(d, (int, float)):
        return int(d)
    parts = str(d).split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return int(parts[0])
    except (ValueError, IndexError):
        return 0


def fmt_dur(sec):
    """秒 → M:SS，0 返回占位符。"""
    if not sec:
        return "—"
    return f"{sec // 60}:{sec % 60:02d}"
