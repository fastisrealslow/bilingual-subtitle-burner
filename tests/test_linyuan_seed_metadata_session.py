"""A bounded metadata pass uses one normal session and preserves provider holds."""
import io
import json
from pathlib import Path
import sys
from unittest.mock import Mock
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import ci_fetch_bilibili as media
import monitor_v2 as monitor
import seed_campaign as campaign


def test_metadata_session_is_lazy_reused_and_never_forwards_to_another_host(monkeypatch):
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append((request.full_url,timeout))
            return io.BytesIO(b'{"code":0}')
    factory=Mock(return_value=Opener());monkeypatch.setattr(media,'opener',factory)
    request=campaign.metadata_session();factory.assert_not_called()
    request('https://api.bilibili.com/x/web-interface/view?bvid=BV1NpBFYkEKx',timeout=12)
    request('https://api.bilibili.com/x/web-interface/wbi/search/type?keyword=test',timeout=15)
    assert factory.call_count==1 and [t for _,t in calls]==[12,15]
    with pytest.raises(ValueError):request('https://unrelated.example/api')
    with pytest.raises(ValueError):request('http://api.bilibili.com/')
    assert len(calls)==2


def test_search_uses_injected_session_for_each_page_without_fallback(monkeypatch):
    source=monitor.BilibiliSearchSource(dict(pages=2),{})
    request=Mock(return_value=json.dumps(dict(code=0,data=dict(result=[]))))
    source.http_get=request
    monkeypatch.setattr(monitor,'http_get',Mock(side_effect=AssertionError('no bare fallback')))
    monkeypatch.setattr(monitor.time,'sleep',lambda _:None)
    assert source._fetch_via_api('林园')==[] and request.call_count==2
    request.side_effect=RuntimeError('B站 API page=1 code=-352')
    with pytest.raises(RuntimeError):source._fetch_via_api('林园')
    assert request.call_count==3
