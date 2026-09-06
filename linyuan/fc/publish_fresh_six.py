"""Publish reviewed new outputs sequentially and require exact Bilibili receipts."""
import hashlib
import http.cookiejar
import io
import json
import os
from pathlib import Path
import time
import urllib.request

import index as fc


PUBLIC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/120.0 Safari/537.36")


def bilibili_opener():
    """Build the same browser-like session already proven by the CI fetcher.

    FC's mainland egress currently receives HTTP 412 from Bilibili's public
    archive API.  Public verification therefore belongs on the GitHub runner;
    uploads and their exact receipts remain on FC.
    """
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", PUBLIC_UA),
        ("Accept-Language", "zh-CN,zh;q=0.9"),
        ("Referer", "https://www.bilibili.com/"),
    ]
    try:
        opener.open("https://www.bilibili.com/", timeout=20).read()
        spi = json.loads(opener.open(
            "https://api.bilibili.com/x/frontend/finger/spi",
            timeout=20).read().decode())
        for name, value in (("buvid3", spi["data"]["b_3"]),
                            ("buvid4", spi["data"]["b_4"])):
            jar.set_cookie(http.cookiejar.Cookie(
                0, name, value, None, False, ".bilibili.com", True, False,
                "/", True, False, None, False, None, None, {}))
    except Exception as exc:
        print(f"Bilibili fingerprint bootstrap warning: {exc}", flush=True)
    return opener


def runner_publication_status(found):
    """Verify exact BV receipts through GitHub egress, never by re-uploading."""
    opener = bilibili_opener()
    rows = []
    for sha, receipt in found.items():
        bvid = receipt.get("bvid")
        row = {"sha256": sha, "bvid": bvid,
               "title": receipt.get("title"), "public": False}
        try:
            req = urllib.request.Request(
                "https://api.bilibili.com/x/web-interface/view?bvid=" + bvid,
                headers={"Referer": "https://www.bilibili.com/",
                         "Accept": "application/json"})
            response = json.loads(opener.open(req, timeout=20).read().decode())
            data = response.get("data") or {}
            row.update(api_code=response.get("code"),
                       archive_state=data.get("state"),
                       duration=data.get("duration"),
                       owner_mid=(data.get("owner") or {}).get("mid"))
            row["public"] = (
                response.get("code") == 0 and data.get("state") == 0
                and str((data.get("owner") or {}).get("mid"))
                == str(fc.OWNER_MID)
                and data.get("bvid") == bvid)
        except Exception as exc:
            row["error"] = str(exc)[:160]
        rows.append(row)
    if rows and not all(row['public'] for row in rows) and os.environ.get('BILIBILI_COOKIES'):
        from bili_archive_status import archive_status
        try:
            creator=archive_status([r['bvid'] for r in rows],fc.OWNER_MID,
                                  os.environ['BILIBILI_COOKIES'])
            by_bvid={r['bvid']:r for r in creator['videos']}
            for row in rows:
                evidence=by_bvid.get(row['bvid'])
                if evidence is not None:
                    row['creator_archive']=evidence
                    row['public']=evidence['public']
                    row['verification_origin']='authenticated-creator-read'
        except Exception as exc:
            print('Creator status unavailable: '+type(exc).__name__,flush=True)
    for row in rows:
        expected=(fc.FRESH_SIX_APPROVED.get(row['sha256']) or {}).get('duration_sec')
        archive=row.get('creator_archive') or {}
        if 'duration' in archive:
            row['duration']=archive['duration']
        row['expected_duration_sec']=expected
        row['duration_verified']=duration_matches_review(row.get('duration'),expected)
    return {"verification_origin": "github-actions",
            "receipts": len(rows),
            "public_count": sum(row["public"] for row in rows),
            "verified_count":sum(row['public'] and row['duration_verified'] for row in rows),
            "videos": rows}


def duration_matches_review(actual, expected):
    """Bilibili's archive duration is seconds; require the reviewed long video."""
    try:
        return float(actual)>=120 and float(expected)>=120 and abs(float(actual)-float(expected))<=2
    except (TypeError,ValueError):
        return False


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


def prepare_async_tasks(client, function, m, runtime):
    """Preserve destinations/retention, with no automatic replay of uploads."""
    try:
        old = client.get_async_invoke_config_with_options(function,
            m.GetAsyncInvokeConfigRequest(qualifier='LATEST'), {}, runtime).body
    except Exception as exc:
        if 'NotFound' not in str(getattr(exc, 'code', '')):
            raise
        old = None
    body = m.PutAsyncInvokeConfigInput(async_task=True, max_async_retry_attempts=0)
    if old is not None:
        body.destination_config = old.destination_config
        body.max_async_event_age_in_seconds = old.max_async_event_age_in_seconds
    client.put_async_invoke_config_with_options(function,
        m.PutAsyncInvokeConfigRequest(qualifier='LATEST', body=body), {}, runtime)
    verified = client.get_async_invoke_config_with_options(function,
        m.GetAsyncInvokeConfigRequest(qualifier='LATEST'), {}, runtime).body
    if not verified.async_task or verified.max_async_retry_attempts != 0:
        raise SystemExit('Async task mode/no-replay configuration did not persist')


def publish_async_part(client, function, m, runtime, sha, approved):
    """A stable task ID survives a lost HTTP response without a second upload."""
    terminal = {'Succeeded', 'Failed', 'Stopped', 'Expired', 'Invalid'}
    for attempt in range(1, 4):
        task_id = 'ly-long-0906-' + sha[:20] + '-a' + str(attempt)
        def query():
            try:
                return client.get_async_task_with_options(function, task_id,
                    m.GetAsyncTaskRequest(qualifier='LATEST'), {}, runtime).body
            except Exception as exc:
                if 'NotFound' in str(getattr(exc, 'code', '')):
                    return None
                raise
        task = query()
        if task is None:
            payload = {'triggerName':'publish-batch', 'batch_slug':approved['slug'],
                'source_url':approved['source_url'], 'title':approved['title'], 'batch_remaining':1}
            try:
                response = client.invoke_function_with_options(function,
                    m.InvokeFunctionRequest(qualifier='LATEST', body=io.BytesIO(json.dumps(payload).encode())),
                    m.InvokeFunctionHeaders(x_fc_invocation_type='Async', x_fc_async_task_id=task_id), runtime)
                if response.status_code != 202:
                    raise RuntimeError('Async task was not accepted')
            except Exception:
                # The same ID is queried after an uncertain submission; never
                # create a different invocation to overcome a network error.
                task = query()
                if task is None:
                    raise
        print(json.dumps({'task_id':task_id, 'sha256':sha, 'phase':'accepted-or-existing'}), flush=True)
        deadline = time.monotonic() + 30 * 60
        while time.monotonic() < deadline:
            task = query()
            if task is not None:
                Path('fresh-six-async-task.json').write_text(json.dumps(task.to_map(), ensure_ascii=False, indent=2))
                if task.status in terminal:
                    break
            time.sleep(15)
        else:
            raise SystemExit('Async task still unresolved; retain task ID and do not resubmit')
        got = receipts(state())
        Path('fresh-six-receipts.json').write_text(json.dumps(got, ensure_ascii=False, indent=2))
        if sha in got:
            print(json.dumps(got[sha], ensure_ascii=False), flush=True)
            return
        try:
            result = json.loads(task.return_payload or '{}')
        except (TypeError, ValueError):
            result = {}
        current = state()
        candidate = next((e for e in current.get('dispatched', []) if e.get('slug') == approved['slug']), {})
        if (task.status == 'Succeeded' and result.get('artifact_download_retryable') == 1
                and not candidate.get('uploading') and sha not in receipts(current)):
            print(json.dumps({'task_id':task_id, 'result':result, 'phase':'download-only-retry'}), flush=True)
            continue
        raise SystemExit('Completed async task has no exact receipt: ' + json.dumps({
            'task_id':task_id, 'status':task.status, 'result':result}, ensure_ascii=False))
    raise SystemExit('Three completed download-only failures; no upload was retried')


def _publish_missing_receipts():
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
            util.RuntimeOptions(connect_timeout=10000,
                # A cancelled synchronous client stops the FC invocation.
                # Keep this connection beyond the bounded download + upload;
                # the old 120s timeout could kill a transfer before any receipt.
                read_timeout=2700000 if payload.get('triggerName')=='publish-batch' else 120000,
                autoretry=False))
        body = response.body.read() if hasattr(response.body, "read") else response.body
        return json.loads(body)

    health = invoke({"triggerName": "diagnose-production"})
    expected = hashlib.sha256(Path(fc.__file__).read_bytes()).hexdigest()
    if health.get("code_sha256") != expected or health.get("daily_limit") != 6:
        raise SystemExit("Deployed code does not match reviewed publisher")
    task_runtime = util.RuntimeOptions(connect_timeout=10000, read_timeout=60000, autoretry=False)
    prepare_async_tasks(client, function, m, task_runtime)
    Path("fresh-six-receipts.json").write_text(json.dumps(receipts(state()), ensure_ascii=False, indent=2))
    ordered = sorted(fc.FRESH_SIX_APPROVED.items(), key=lambda item: (
        item[1].get('render_mode')=='audio_card',item[1]["slug"],item[1]["part_index"]))
    for sha, approved in ordered:
        current = state()
        if sha in receipts(current):
            continue
        daily = current.get("daily_publish") or {}
        if int((daily.get(fc.fresh_six_counter(approved["slug"])) or {}).get("count") or 0) >= 6:
            raise SystemExit("Six new uploads already reached; stop")
        candidate = next((e for e in current.get("dispatched", []) if e.get("slug") == approved["slug"]), {})
        if candidate.get("uploading"):
            raise SystemExit("Existing upload lease: inspect its receipt before another invocation")
        if int(candidate.get("published_parts") or 0) != approved["part_index"]:
            raise SystemExit("Manifest cursor differs from reviewed part; inspect skipped/recovered records")
        publish_async_part(client, function, m, task_runtime, sha, approved)
    got = receipts(state())
    print(json.dumps({"new_receipts":len(got), "receipts":got}, ensure_ascii=False, indent=2))
    return got


def main():
    if not 1 <= len(fc.FRESH_SIX_APPROVED) <= 6:
        raise SystemExit("No bounded reviewed fresh-six manifest")
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time()+8*3600))
    if today != fc.FRESH_SIX_DATE:
        raise SystemExit("The one-day fresh-six authorization has expired")
    got = receipts(state())
    Path("fresh-six-receipts.json").write_text(
        json.dumps(got, ensure_ascii=False, indent=2))
    if len(got) != len(fc.FRESH_SIX_APPROVED):
        got = _publish_missing_receipts()
    print(json.dumps({"new_receipts": len(got), "receipts": got},
                     ensure_ascii=False, indent=2))
    deadline=time.monotonic()+30*60
    while True:
        public=runner_publication_status(got)
        Path('fresh-six-public-status.json').write_text(json.dumps(public,ensure_ascii=False,indent=2))
        print(json.dumps(public,ensure_ascii=False),flush=True)
        if public.get('verified_count')==len(fc.FRESH_SIX_APPROVED):
            break
        if any((r.get('creator_archive') or {}).get('is_only_self')==1
               for r in public.get('videos',[])):
            raise SystemExit('Owner has hidden a reviewed upload; preserve visibility and do not re-upload')
        if time.monotonic()>=deadline:
            raise SystemExit('Uploads have receipts but public archive verification is incomplete; do not re-upload')
        time.sleep(45)


if __name__ == "__main__":
    main()
