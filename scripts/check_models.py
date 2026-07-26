#!/usr/bin/env python3
"""开跑前校验所配置的模型是否仍在硅基流动在售列表中。

为什么需要这个：
    硅基流动会下架旧模型。当你请求一个已下架的模型名时，平台**不会报错**，
    而是静默把请求转到另一个模型上。实测 Qwen/Qwen2.5-VL-72B-Instruct 下架后
    被转到 Qwen/Qwen3.5-9B（纯文本模型），导致封面识别把几十张图片编码发过去、
    照常按 input token 扣费，但模型根本看不到图，返回空内容后退回兜底截帧。
    这一项占了实测总花费的 97%，产出为零，而且日志里毫无异常。

    对每天定时执行的流水线来说，这种静默失败会长期烧钱，必须在开跑前拦住。

用法：
    python3 scripts/check_models.py                 # 校验默认模型集
    python3 scripts/check_models.py --strict        # 有问题时退出码 1
    python3 scripts/check_models.py --models A B    # 校验指定模型
"""
import argparse
import json
import os
import sys
import urllib.request

API_BASE = (os.environ.get("SILICONFLOW_BASE_URL") or "").strip() or "https://api.siliconflow.cn/v1"

# 需要图片输入能力的模型，单独标出来便于提示
VISION_HINT = "Qwen/Qwen3-VL-8B-Instruct"


def fetch_available(api_key: str) -> set:
    url = f"{API_BASE}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    # 优先用 requests（它会遵守 HTTPS_PROXY，本地代理环境下才走得通），
    # 拿不到就回退到标准库 urllib，避免新增硬依赖。
    try:
        import requests  # noqa: PLC0415
        data = requests.get(url, headers=headers, timeout=60).json()
    except ImportError:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    return {m["id"] for m in data.get("data", [])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=None,
                    help="要校验的模型名；不传则读取环境变量里的默认组合")
    ap.add_argument("--strict", action="store_true",
                    help="发现已下架模型时以退出码 1 结束")
    args = ap.parse_args()

    api_key = (os.environ.get("SILICONFLOW_API_KEY") or "").strip()
    if not api_key:
        print("[check-models] 缺少 SILICONFLOW_API_KEY，跳过校验", file=sys.stderr)
        return 0

    if args.models:
        wanted = list(dict.fromkeys(args.models))
    else:
        wanted = list(dict.fromkeys([
            (os.environ.get("SILICONFLOW_MODEL") or "").strip() or "Qwen/Qwen3-8B",
            (os.environ.get("SILICONFLOW_TRANSLATE_MODEL") or "").strip() or "deepseek-ai/DeepSeek-V3",
            (os.environ.get("SILICONFLOW_VISION_MODEL") or "").strip() or VISION_HINT,
        ]))

    try:
        available = fetch_available(api_key)
    except Exception as e:
        print(f"[check-models] 无法获取模型列表（{e}），跳过校验", file=sys.stderr)
        return 0

    missing = [m for m in wanted if m not in available]
    for m in wanted:
        print(f"[check-models] {'✅ 在售' if m in available else '❌ 已下架'}  {m}")

    if missing:
        print("", file=sys.stderr)
        print("[check-models] ⚠️ 上述模型已从平台下架。", file=sys.stderr)
        print("[check-models] 硅基流动不会为此报错，而是静默转到其它模型，", file=sys.stderr)
        print("[check-models] 可能照常扣费却拿不到有效结果。请更新模型名后再跑。", file=sys.stderr)
        vl = sorted(m for m in available if "-VL-" in m and "Embedding" not in m and "Reranker" not in m)
        if vl:
            print(f"[check-models] 当前可用的视觉模型：{', '.join(vl[:6])}", file=sys.stderr)
        if args.strict:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
