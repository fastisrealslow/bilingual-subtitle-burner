#!/usr/bin/env python3
"""更新阿里云 FC 投稿器代码与可靠性配置，保留凭据、层和触发器。"""

import base64
import os
from pathlib import Path


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量 {name}")
    return value


def main():
    # 延迟导入，让配置错误在安装 SDK 的环境里有清晰提示，也方便单元测试。
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as fc_models
    from alibabacloud_tea_openapi import models as open_api_models

    region = os.environ.get("FC_REGION", "cn-hangzhou").strip()
    function_name = os.environ.get("FC_FUNCTION_NAME", "fc-develop").strip()
    zip_path = Path(os.environ.get("FC_CODE_ZIP", "/tmp/fc-code.zip"))
    if not zip_path.is_file() or zip_path.stat().st_size == 0:
        raise SystemExit(f"FC 代码包不存在或为空：{zip_path}")

    client = Client(open_api_models.Config(
        access_key_id=required_env("ALIYUN_AK"),
        access_key_secret=required_env("ALIYUN_SK"),
        endpoint=f"fcv3.{region}.aliyuncs.com",
    ))
    code = fc_models.InputCodeLocation(
        zip_file=base64.b64encode(zip_path.read_bytes()).decode("ascii"))
    # UpdateFunction 是部分更新。只调整投稿可靠性所需字段，不发送环境变量、
    # 层或触发器，因此现有登录态不会被覆盖。10GB 是 FC 支持的下一个磁盘档位，
    # 用于容纳 57 分钟完整版；一次调用只下载当前一条，使用完立即清理。
    body = fc_models.UpdateFunctionInput(
        code=code,
        memory_size=int(os.environ.get("FC_MEMORY_SIZE_MB", "1024")),
        cpu=float(os.environ.get("FC_CPU", "0.5")),
        timeout=int(os.environ.get("FC_TIMEOUT_SECONDS", "1800")),
        disk_size=int(os.environ.get("FC_DISK_SIZE_MB", "10240")),
        instance_concurrency=1,
    )
    client.update_function(
        function_name, fc_models.UpdateFunctionRequest(body=body))
    print(f"✓ 已更新 {region}/{function_name}，代码包 {zip_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
