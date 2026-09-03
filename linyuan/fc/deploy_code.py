#!/usr/bin/env python3
"""只更新阿里云 FC 代码包，保留函数现有环境变量、层和触发器。"""

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
    function_name = os.environ.get("FC_FUNCTION_NAME", "linyuan-pipeline").strip()
    zip_path = Path(os.environ.get("FC_CODE_ZIP", "/tmp/fc-code.zip"))
    if not zip_path.is_file() or zip_path.stat().st_size == 0:
        raise SystemExit(f"FC 代码包不存在或为空：{zip_path}")

    client = Client(open_api_models.Config(
        access_key_id=required_env("ALIYUN_AK"),
        access_key_secret=required_env("ALIYUN_SK"),
        endpoint=f"fc3.{region}.aliyuncs.com",
    ))
    code = fc_models.InputCodeLocation(
        zip_file=base64.b64encode(zip_path.read_bytes()).decode("ascii"))
    # UpdateFunction 是部分更新：这里只发送 code，不能覆盖函数里已有的
    # GITHUB_TOKEN、BILIBILI_COOKIES、依赖层、超时和定时触发器。
    body = fc_models.UpdateFunctionInput(code=code)
    client.update_function(
        function_name, fc_models.UpdateFunctionRequest(body=body))
    print(f"✓ 已更新 {region}/{function_name}，代码包 {zip_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
