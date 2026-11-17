"""Bounded, read-only Bilibili comment audit. Never sends or edits comments."""
import json, os, re, sys, time
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlencode

# Deliberately no POST, upload modules, FC calls, or credential output.
OWNER = "275211725"
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None
opener = build_opener(NoRedirect())
def emit(kind, **data):
    # JSON escaping prevents comment text from becoming workflow commands.
    print(json.dumps(dict(kind=kind, **data), ensure_ascii=True), flush=True)
def select_candidates(state, limit=24):
    # Only own publication ledger; never recursively include sources/dispatched.
    publications = sorted((state.get("published") or {}).values(),
                          key=lambda row: float(row.get("ts") or 0), reverse=True)
    bvids = []
    for publication in publications:
        for part in publication.get("parts") or []:
            bvid = part.get("bvid")
            if isinstance(bvid, str) and re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
                bvids.append(bvid)
        bvid = publication.get("bvid")
        if isinstance(bvid, str) and re.fullmatch(r"BV[A-Za-z0-9]{10}", bvid):
            bvids.append(bvid)
    return list(dict.fromkeys(bvids))[:limit]

def main():
    try:
        raw = os.environ.pop("BILIBILI_COOKIES", "")
        if not raw:
            emit("blocked", reason="existing_ci_secret_unavailable")
            sys.exit(1)
        payload = json.loads(raw)
        entries = (payload.get("cookie_info") or {}).get("cookies") or payload.get("cookies") or []
        cookies = {str(c["name"]): str(c["value"]) for c in entries}
        if cookies.get("DedeUserID") != OWNER or not cookies.get("bili_jct"):
            emit("blocked", reason="cookie_owner_mismatch_or_missing_csrf")
            sys.exit(1)
        if any("\r" in x or "\n" in x for pair in cookies.items() for x in pair):
            raise ValueError("invalid_cookie")
        cookie_header = "; ".join(k+"="+v for k,v in cookies.items())
        def get(path, **params):
            assert path in ("/x/web-interface/nav", "/x/web-interface/view", "/x/v2/reply", "/x/v2/reply/reply")
            req = Request("https://api.bilibili.com"+path+("?" + urlencode(params) if params else ""),
                headers={"Cookie":cookie_header,"User-Agent":"Mozilla/5.0",
                         "Referer":"https://www.bilibili.com/"}, method="GET")
            time.sleep(5)
            emit("get_entered", endpoint=path)
            with opener.open(req, timeout=15) as response:
                result = json.load(response)
            if result.get("code") != 0:
                emit("api_error", endpoint=path, code=result.get("code"))
                raise ValueError("api_nonzero")
            return result.get("data") or {}
        nav = get("/x/web-interface/nav")
        if not nav or not nav.get("isLogin") or str(nav.get("mid")) != OWNER:
            emit("blocked", reason="authenticated_owner_not_verified")
            sys.exit(1)
        emit("account_verified", owner=OWNER)
        state = json.loads(Path(os.environ.get("COMMENT_STATE_PATH", "site/fc_state.json")).read_text())
        candidates = select_candidates(state)
        emit("ledger_scan", videos=len(candidates), source="main/site/fc_state.json", candidates=candidates)
        found = 0
        for bvid in candidates:
            video = get("/x/web-interface/view", bvid=bvid)
            if not video or str((video.get("owner") or {}).get("mid")) != OWNER:
                continue
            aid = video.get("aid")
            if not isinstance(aid,int) or aid <= 0: continue
            if (video.get("stat") or {}).get("reply") == 0:
                emit("zero_comments", bvid=bvid)
                continue
            comments = get("/x/v2/reply", type=1,oid=aid,pn=1,ps=20,sort=0)
            if comments is None: break
            rows = comments.get("replies") or []
            emit("video", bvid=bvid, aid=aid, title=video.get("title"), comments_on_page=len(rows), total_comments=(comments.get("page") or {}).get("count"))
            for row in rows:
                message = (row.get("content") or {}).get("message","")
                children = row.get("replies") or []
                # Preview can omit an existing owner reply; inspect bounded thread detail.
                if int(row.get("rcount") or 0) and not any(str(x.get("mid")) == OWNER for x in children):
                    thread = get("/x/v2/reply/reply", type=1, oid=aid, root=str(row.get("rpid")), pn=1, ps=20)
                    children = thread.get("replies") or []
                    emit("thread", bvid=bvid, root=str(row.get("rpid")),
                         total=(thread.get("page") or {}).get("count"), loaded=len(children),
                         owner_replies=[{"rpid": str(x.get("rpid")), "text": (x.get("content") or {}).get("message", "")[:1200]} for x in children if str(x.get("mid")) == OWNER])
                emit("comment", bvid=bvid, aid=aid, rpid=str(row.get("rpid")),
                     text=message[:1200], author_is_owner=str(row.get("mid"))==OWNER,
                     replies=int(row.get("rcount") or 0),
                     visible_owner_reply=any(str(x.get("mid"))==OWNER for x in children))
                found += 1
            if found >= 40: break
            time.sleep(0.5)
        emit("complete", comments_read=found, writes=0)
    except Exception as exc:
        # Exception strings can include URLs or request details: never print.
        emit("blocked", reason=type(exc).__name__, http_status=getattr(exc,"code",None))
        sys.exit(1)


if __name__ == "__main__":
    main()
