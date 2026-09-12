#!/usr/bin/env python3
"""更新阿里云 FC 投稿器代码与可靠性配置，保留凭据、层和触发器。"""

import base64
import os
import time
from pathlib import Path


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量 {name}")
    return value


def retry_code_update(update, attempts=3):
    """SDK marks socket write timeouts unretryable; this exact update is idempotent."""
    for attempt in range(attempts):
        try:
            return update()
        except Exception as exc:
            transient=any(token in str(exc).lower() for token in (
                'timed out','timeout','connection aborted','connection reset','remote disconnected'))
            if not transient or attempt+1==attempts:raise
            print(f'部署传输暂时失败，第 {attempt+2}/{attempts} 次重传同一代码包',flush=True)
            time.sleep(10*(attempt+1))


def main():
    # 延迟导入，让配置错误在安装 SDK 的环境里有清晰提示，也方便单元测试。
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as fc_models
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_tea_util import models as util_models

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
    # The reviewed media makes this request several MB. The SDK's short default
    # socket timeout can expire while writing from an overseas Actions runner.
    # Repeating this exact code/config update is idempotent; it invokes no posts.
    retry_code_update(lambda:client.update_function_with_options(
        function_name, fc_models.UpdateFunctionRequest(body=body), {},
        util_models.RuntimeOptions(connect_timeout=180000, read_timeout=180000,
                                   autoretry=False)))
    print(f"✓ 已更新 {region}/{function_name}，代码包 {zip_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
