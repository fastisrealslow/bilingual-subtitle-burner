"""Publish reviewed new outputs sequentially and require exact Bilibili receipts."""
import hashlib
import io
import json
import os
from pathlib import Path
import time
import urllib.request

import index as fc


def state():
    req = urllib.request.Request(
        f"https://api.github.com/repos/{fc.REPO}/contents/{fc.STATE_KEY}?ref=main",
        headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                 "Accept": "application/vnd.github.raw", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.load(response)


def receipts(st):
    found = {}
    for slug, info in st.get("published", {}).items():
        for part in info.get("parts", []):
            sha = (part.get("fingerprints") or {}).get("sha256")
            if (slug in fc.FRESH_SIX_SLUGS and sha in fc.FRESH_SIX_APPROVED
                    and part.get("status") == "published" and part.get("bvid")
                    and part.get("fresh_six_date") == fc.FRESH_SIX_DATE):
                found[sha] = {"slug": slug, "bvid": part["bvid"], "title": part.get("title"),
                              "render_mode": part.get("render_mode"), "ts": part.get("ts")}
    return found


def main():
    from alibabacloud_fc20230330.client import Client
    from alibabacloud_fc20230330 import models as m
    from alibabacloud_tea_openapi import models as api
    from alibabacloud_tea_util import models as util
    if not 1 <= len(fc.FRESH_SIX_APPROVED) <= 6:
        raise SystemExit("No bounded reviewed fresh-six manifest")
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time()+8*3600))
    if today != fc.FRESH_SIX_DATE:
        raise SystemExit("The one-day fresh-six authorization has expired")
    client = Client(api.Config(access_key_id=os.environ["ALIYUN_AK"],
        access_key_secret=os.environ["ALIYUN_SK"],
        endpoint="fcv3."+os.environ.get("FC_REGION", "cn-hangzhou")+".aliyuncs.com"))
    function = os.environ.get("FC_FUNCTION_NAME", "fc-develop")

    def invoke(payload):
        response = client.invoke_function_with_options(function,
            m.InvokeFunctionRequest(qualifier="LATEST", body=io.BytesIO(json.dumps(payload).encode())),
            m.InvokeFunctionHeaders(x_fc_invocation_type="Sync"),
            util.RuntimeOptions(connect_timeout=10000, read_timeout=120000, autoretry=False))
        body = response.body.read() if hasattr(response.body, "read") else response.body
        return json.loads(body)

    health = invoke({"triggerName": "diagnose-production"})
    expected = hashlib.sha256(Path(fc.__file__).read_bytes()).hexdigest()
    if health.get("code_sha256") != expected or health.get("daily_limit") != 6:
        raise SystemExit("Deployed code does not match reviewed publisher")
    Path("fresh-six-receipts.json").write_text(json.dumps(receipts(state()), ensure_ascii=False, indent=2))
    ordered = sorted(fc.FRESH_SIX_APPROVED.items(), key=lambda item: (item[1]["slug"], item[1]["part_index"]))
    for sha, approved in ordered:
        current = state()
        if sha in receipts(current):
            continue
        daily = current.get("daily_publish") or {}
        if int((daily.get("fresh_six") or {}).get("count") or 0) >= 6:
            raise SystemExit("Six new uploads already reached; stop")
        candidate = next((e for e in current.get("dispatched", []) if e.get("slug") == approved["slug"]), {})
        if candidate.get("uploading"):
            raise SystemExit("Existing upload lease: inspect its receipt before another invocation")
        if int(candidate.get("published_parts") or 0) != approved["part_index"]:
            raise SystemExit("Manifest cursor differs from reviewed part; inspect skipped/recovered records")
        started = time.monotonic()
        try:
            result = invoke({"triggerName": "publish-batch", "batch_slug": approved["slug"],
                "source_url": approved["source_url"], "title": approved["title"], "batch_remaining": 1})
            if not result.get("published"):
                raise SystemExit("Publisher did not upload reviewed part: "+json.dumps(result))
        except Exception as exc:
            elapsed = time.monotonic()-started
            error = str(exc)
            if elapsed < 45 or not any(x in error.lower() for x in ("503", "timeout", "timed out", "504")):
                raise
            print("Gateway ended synchronous response; waiting for the exact receipt without re-upload", flush=True)
        deadline = time.monotonic()+30*60
        while time.monotonic() < deadline:
            got = receipts(state())
            Path("fresh-six-receipts.json").write_text(json.dumps(got, ensure_ascii=False, indent=2))
            if sha in got:
                print(json.dumps(got[sha], ensure_ascii=False), flush=True)
                break
            time.sleep(15)
        else:
            raise SystemExit("Exact receipt not found; do not retry an uncertain upload")
    got = receipts(state())
    print(json.dumps({"new_receipts":len(got), "receipts":got}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
