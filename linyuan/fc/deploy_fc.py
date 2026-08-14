#!/usr/bin/env python3
"""一键部署林园流水线到阿里云 FC。用法：
    ALIYUN_AK=xxx ALIYUN_SK=yyy GITHUB_TOKEN=ghp_... BILI_COOKIES_FILE=~/Downloads/.../cookies.json \
        python3 deploy_fc.py
"""
import base64
import json
import os
import sys
from pathlib import Path

from alibabacloud_fc20230330.client import Client
from alibabacloud_fc20230330 import models as m
from alibabacloud_tea_openapi import models as open_api

REGION = os.environ.get("FC_REGION", "cn-hangzhou")
FUNC = "linyuan-pipeline"
ZIP = os.environ.get("FC_CODE_ZIP", "/tmp/fc-code.zip")

ak = os.environ.get("ALIYUN_AK", "")
sk = os.environ.get("ALIYUN_SK", "")
gh_token = os.environ.get("GITHUB_TOKEN", "")
cookies_file = os.environ.get("BILI_COOKIES_FILE", "")
assert ak and sk, "缺 ALIYUN_AK / ALIYUN_SK"
assert gh_token, "缺 GITHUB_TOKEN"
assert Path(cookies_file).exists(), f"缺 cookies 文件：{cookies_file}"

client = Client(open_api.Config(
    access_key_id=ak, access_key_secret=sk,
    endpoint=f"fc3.{REGION}.aliyuncs.com"))

code_b64 = base64.b64encode(Path(ZIP).read_bytes()).decode()
print(f"代码包 {Path(ZIP).stat().st_size/1048576:.1f}MB，base64 {len(code_b64)//1048576}MB")

# 1. 创建/更新函数
body = m.CreateFunctionInput(
    function_name=FUNC,
    handler="index.handler",
    runtime="python3.10",
    timeout=600,
    memory_size=1024,
    cpu=0.5,
    disk_size=512,
    code=m.InputCodeLocation(zip_file=code_b64),
    environment_variables={
        "GITHUB_TOKEN": gh_token,
        "BILIBILI_COOKIES": Path(cookies_file).read_text(),
    },
    description="林园流水线境内执行端：每日选片调度 + 每小时投稿B站",
)
try:
    client.create_function(m.CreateFunctionRequest(body=body))
    print(f"✓ 函数 {FUNC} 已创建")
except Exception as e:
    if "409" in str(e) or "already exists" in str(e).lower():
        upd = m.UpdateFunctionInput(
            handler="index.handler", runtime="python3.10", timeout=600,
            memory_size=1024, cpu=0.5,
            code=m.InputCodeLocation(zip_file=code_b64),
            environment_variables=body.environment_variables)
        client.update_function(FUNC, m.UpdateFunctionRequest(body=upd))
        print(f"✓ 函数 {FUNC} 已更新")
    else:
        raise

# 2. 两个定时触发器
for name, cron in [("dispatch", "0 0 10 * * *"), ("publish", "0 30 * * * *")]:
    t = m.CreateTriggerInput(trigger_name=name, trigger_type="timer",
                             qualifier="LATEST",
                             trigger_config=json.dumps({
                                 "cronExpression": cron,
                                 "enable": True, "payload": "{}"}))
    try:
        client.create_trigger(FUNC, m.CreateTriggerRequest(body=t))
        print(f"✓ 触发器 {name}（{cron}）")
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            print(f"· 触发器 {name} 已存在，跳过")
        else:
            raise

# 3. 同步测试调用 publish（队列空时会秒回）
print("\n测试调用 publish ...")
r = client.invoke_function(FUNC, m.InvokeFunctionRequest(body=m.InvokeRequest(
    qualifier="LATEST", body=b'{"triggerName":"publish"}')))
resp = r.body.read().decode() if hasattr(r.body, "read") else str(r.body)
print("返回:", resp[:300])
print("\n✅ 部署完成。明天 10:00 第一次自动调度。")
