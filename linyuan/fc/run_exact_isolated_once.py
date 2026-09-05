# -*- coding: utf-8 -*-
"""Create a temporary no-trigger FC clone and diagnose one exact V11 upload."""
import base64
import io
import json
import os
import time
import urllib.request
import zipfile
from pathlib import Path

from alibabacloud_fc20230330.client import Client
from alibabacloud_fc20230330 import models as m
from alibabacloud_tea_openapi import models as api
from alibabacloud_tea_util import models as util

WORKER = "fc-develop-v11-exact-0905"
SOURCE = os.environ.get("FC_FUNCTION_NAME", "fc-develop")


def client():
    return Client(api.Config(
        access_key_id=os.environ["ALIYUN_AK"],
        access_key_secret=os.environ["ALIYUN_SK"],
        endpoint="fcv3." + os.environ.get("FC_REGION", "cn-hangzhou") + ".aliyuncs.com",
    ))


def patched_code():
    code = Path("linyuan/fc/index.py").read_text(encoding="utf-8")
    old = 'log_event("fail", f"✗ {slug} 投稿失败", f"rc={r.returncode} {((r.stdout or \'\') + (r.stderr or \'\'))[:150]}")'
    new = 'log_event("fail", f"✗ {slug} 投稿失败", f"rc={r.returncode} tail={out[-1800:]}")'
    if old not in code:
        raise RuntimeError("expected failure log statement missing")
    code = code.replace(old, new, 1)
    submit_anchor = '    if cover:\n        cmd += ["--cover", str(cover)]'
    if submit_anchor not in code:
        raise RuntimeError("expected cover command block missing")
    code = code.replace(
        submit_anchor,
        '    cmd += ["--submit", "web"]\n' + submit_anchor,
        1,
    )
    anchor = '''        if tail:
            log.error(f"  错误信息: {'; '.join(tail)[:300]}")

    shutil.rmtree(tmp, ignore_errors=True)
    return {"published": done}'''
    replacement = '''        if tail:
            log.error(f"  错误信息: {'; '.join(tail)[:300]}")
        if explicit_v4_batch:
            e.pop("uploading", None)
            e.pop("uploading_ts", None)
            e.pop("upload_title", None)
            e["last_upload_failure_exact"] = out[-1800:]
            save_state(st)

    shutil.rmtree(tmp, ignore_errors=True)
    return {"published": done, "error_tail": out[-1800:] if explicit_v4_batch and r.returncode else ""}'''
    if anchor not in code:
        raise RuntimeError("expected failure return block missing")
    return code.replace(anchor, replacement, 1)


def create_worker(fc):
    source = fc.get_function(SOURCE, m.GetFunctionRequest()).body
    Path("/tmp/index.py").write_text(patched_code(), encoding="utf-8")
    with zipfile.ZipFile("/tmp/code.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write("/tmp/index.py", "index.py")
    try:
        fc.delete_function(WORKER)
        time.sleep(5)
    except Exception:
        pass
    layers = [getattr(item, "arn", None) for item in (source.layers or [])]
    body = m.CreateFunctionInput(
        function_name=WORKER,
        description="Temporary exact V11 publication diagnostic; no triggers",
        runtime=source.runtime,
        handler=source.handler,
        cpu=source.cpu,
        memory_size=source.memory_size,
        disk_size=source.disk_size,
        timeout=source.timeout,
        instance_concurrency=1,
        internet_access=source.internet_access,
        layers=[item for item in layers if item],
        role=source.role or None,
        environment_variables=source.environment_variables,
        code=m.InputCodeLocation(
            zip_file=base64.b64encode(Path("/tmp/code.zip").read_bytes()).decode()
        ),
    )
    fc.create_function(m.CreateFunctionRequest(body=body))
    print("isolated worker created", flush=True)


def invoke_once(fc):
    payload = json.dumps({
        "triggerName": "publish-batch",
        "batch_slug": "ly-parity-v3-14-0905",
        "batch_remaining": 1,
        "ignore_daily_limit": True,
        "source_url": "https://www.bilibili.com/video/BV1Pzug6fEyY",
        "title": "2026年8月7日林园57分钟专访",
    }, ensure_ascii=False).encode()
    response = fc.invoke_function_with_options(
        WORKER,
        m.InvokeFunctionRequest(qualifier="LATEST", body=io.BytesIO(payload)),
        m.InvokeFunctionHeaders(x_fc_invocation_type="Async"),
        util.RuntimeOptions(connect_timeout=10000, read_timeout=120000, autoretry=False),
    )
    print("async invocation accepted", flush=True)


def wait_for_final():
    token = os.environ.get("GH_TOKEN", "")
    if not token:
        raise RuntimeError("Actions state token missing")
    url = "https://api.github.com/repos/fastisrealslow/bilingual-subtitle-burner/contents/linyuan/.automation/fc_state.json?ref=main"
    deadline = time.time() + 5400
    while time.time() < deadline:
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github.raw+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            state = json.loads(response.read().decode())
        entry = next((item for item in state.get("dispatched", [])
                      if item.get("slug") == "ly-parity-v3-14-0905"), {})
        published = int(entry.get("published_parts") or 0)
        print(f"final progress {published}/14", flush=True)
        if published >= 14:
            return
        time.sleep(30)
    raise RuntimeError("final async upload did not form a receipt in 90 minutes")


def main():
    fc = client()
    try:
        create_worker(fc)
        time.sleep(25)
        invoke_once(fc)
        wait_for_final()
    finally:
        try:
            fc.delete_function(WORKER)
            print("isolated worker deleted", flush=True)
        except Exception as exc:
            print("cleanup result: " + repr(exc), flush=True)


if __name__ == "__main__":
    main()
