"""Read-only, allowlisted public projection of our own Bilibili creator analytics."""
import json
import os
import re
from time import monotonic, sleep
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parents[1]
OWNER = "275211725"
LIMIT = 30
NAV = "https://api.bilibili.com/x/web-interface/nav"
COMPARE = f"https://member.bilibili.com/x/web/data/archive_diagnose/compare?size={LIMIT}"
ARCHIVE = "https://member.bilibili.com/x/web/data/archive"
BV = re.compile(r"BV[A-Za-z0-9]{10}")
COUNTS = {"plays": "play", "new_follows": "total_new_attention_cnt"}
RATES = {
    "follow_rate_bps": "play_trans_fan_rate",
    "thumbnail_ctr_bps": "tm_rate",
    "early_exit_bps": "crash_rate",
    "completion_bps": "full_play_ratio",
    "engagement_bps": "interact_rate",
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def nonnegative(value, cap=None):
    return value if type(value) is int and value >= 0 and (cap is None or value <= cap) else None


def credentials(raw):
    obj = json.loads(raw)
    items = (obj.get("cookie_info") or {}).get("cookies") or obj.get("cookies") or []
    jar = {c["name"]: str(c["value"]) for c in items}
    if jar.get("DedeUserID") != OWNER or not jar.get("SESSDATA"):
        raise ValueError("Creator credential unavailable or wrong account")
    keys = ("SESSDATA", "DedeUserID", "DedeUserID__ckMd5", "bili_jct")
    header = "; ".join(f"{k}={jar[k]}" for k in keys if jar.get(k))
    if any(c in header for c in "\n\r"):
        raise ValueError("Invalid cookie formatting")
    return header


def read_api(url, header, opener):
    if url not in (NAV, COMPARE):
        raise ValueError("Unexpected API URL")
    return fetch_data(url, header, opener, timeout=15)


def fetch_data(url, header, opener, timeout):
    req = Request(url, headers={"Cookie": header, "User-Agent": "Mozilla/5.0",
                                "Referer": "https://member.bilibili.com/"}, method="GET")
    with opener.open(req, timeout=timeout) as response:
        payload = json.load(response)
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise ValueError("Creator API did not return data")
    return payload["data"]


def watch_seconds(data):
    # Single-archive analytics supplies settled seconds, unlike compare.
    play = data.get("play")
    if not isinstance(play, dict):
        return None
    return nonnegative(play.get("avg_duration")) or None


def enrich_watch_time(videos, header, opener):
    # Only call for rows that passed ownership and public-video checks.
    # Stop on the first transport/API failure, without retrying a block.
    deadline = monotonic() + 45
    for i, video in enumerate(videos):
        if i:
            sleep(0.35)
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        bvid = video["bvid"]
        if not BV.fullmatch(bvid):
            continue
        url = ARCHIVE + "?" + urlencode({"bvid": bvid})
        try:
            archive = fetch_data(url, header, opener, timeout=min(5, remaining))
        except (OSError, ValueError):
            print("[creator-metrics] single-video lookup incomplete; missing durations left blank")
            break
        video["average_watch_seconds"] = watch_seconds(archive)


def public_projection(rows, public_items, observed_at):
    if not isinstance(rows, list) or not 0 < len(rows) <= LIMIT:
        raise ValueError("Unexpected creator list size")
    allowed = {r["bvid"]: r for r in public_items if isinstance(r, dict)
               and isinstance(r.get("bvid"), str) and BV.fullmatch(r["bvid"])}
    seen, result = set(), []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bvid = row.get("bvid")
        if not isinstance(bvid, str) or bvid not in allowed or bvid in seen:
            continue
        visible = row.get("is_only_self")
        if not (visible is False or type(visible) is int and visible == 0):
            continue
        stat = row.get("stat")
        if not isinstance(stat, dict):
            continue
        item = {"bvid": bvid, "title": str(allowed[bvid].get("title") or bvid)[:150]}
        item.update({key: nonnegative(stat.get(field)) for key, field in COUNTS.items()})
        item.update({key: nonnegative(stat.get(field), 10000) for key, field in RATES.items()})
        item["average_watch_seconds"] = None
        if item["plays"] is None or item["new_follows"] is None:
            continue
        result.append(item)
        seen.add(bvid)
    if not result:
        raise ValueError("No public video with both views and an explicit follow count")
    return {"schema": 1, "source": "bilibili_creator_archive_diagnose",
            "observed_at": observed_at, "window": None, "videos": result}


def main():
    try:
        header = credentials(os.environ.pop("BILIBILI_COOKIES", "") or "{}")
        opener = build_opener(NoRedirect())
        owner = read_api(NAV, header, opener)
        if owner.get("isLogin") is not True or str(owner.get("mid")) != OWNER:
            raise ValueError("Creator identity not verified")
        data = read_api(COMPARE, header, opener)
        public_items = json.loads((ROOT / "site/stats.json").read_text(encoding="utf-8"))["top"]
        snapshot = public_projection(data.get("list"), public_items,
                                     datetime.now(timezone.utc).isoformat(timespec="seconds"))
        enrich_watch_time(snapshot["videos"], header, opener)
        available = sum(v["average_watch_seconds"] is not None for v in snapshot["videos"])
        print(f"[creator-metrics] watch-time coverage {available}/{len(snapshot['videos'])}")
        target = ROOT / "site/creator_metrics.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        temporary.replace(target)
        print(f"[creator-metrics] updated {len(snapshot['videos'])} already-public videos")
    except Exception as exc:
        # Never log response bodies, headers, credential values or private rows.
        print(f"[creator-metrics] no refresh: {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
