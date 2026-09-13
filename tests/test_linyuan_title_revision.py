"""Only the existing BV's title changes; uncertain edits never create a new post."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import title_revision as R


def setup(monkeypatch):
    part=dict(bvid=R.BVID,title=R.OLD_TITLE,fingerprints=dict(sha256=R.SHA256))
    receipt=dict(bvid=R.BVID,title=R.OLD_TITLE,parts=[part])
    state=dict(published={R.SLUG:receipt},daily_publish=dict(date='2026-09-13',count=1,published_hours=[10]),**{R.STATE_KEY:{}})
    fc=NS(load_state=lambda:state,save_state=lambda _:None,log_event=lambda *a:None)
    platform=dict(archive=dict(aid=123,bvid=R.BVID,title=R.OLD_TITLE,cover='https://example/cover.jpg',state=0,tid=95,desc='原简介'),
                  videos=[dict(filename='existing-file',cid=456,title=R.OLD_TITLE)])
    monkeypatch.setattr(R,'creator_detail',lambda *a:deepcopy(platform))
    return state,fc,platform,receipt,part


def test_title_edit_preserves_video_cover_and_daily_quota(monkeypatch):
    state,fc,platform,receipt,part=setup(monkeypatch)
    calls=[];daily=deepcopy(state['daily_publish'])
    def post(url,**kw):
        calls.append(url);payload=kw['json']
        assert url.endswith('/edit')
        assert payload['cover']==platform['archive']['cover']
        assert payload['videos'][0]['filename']=='existing-file' and payload['videos'][0]['cid']==456
        platform['archive']['title']=payload['title']
        return NS(json=lambda:dict(code=0,message='OK'))
    txns=state[R.STATE_KEY]
    result=R.apply_session(fc,state,receipt,part,txns,{},NS(post=post),'csrf')
    assert result['status']=='verified' and result['new_posts']==0 and result['cid']==456
    assert len(calls)==1 and part['title']==R.TITLE and receipt['title']==R.TITLE
    assert state['daily_publish']==daily and part['fingerprints']['sha256']==R.SHA256
    assert txns[R.BVID]['original_archive']['archive']['title']==R.OLD_TITLE


def test_lost_edit_response_only_reads_back_on_retry(monkeypatch):
    state,fc,platform,receipt,part=setup(monkeypatch)
    calls=[]
    def post(*a,**k):
        calls.append(1)
        raise TimeoutError('Unknown response')
    txns=state[R.STATE_KEY]
    with pytest.raises(TimeoutError):R.apply_session(fc,state,receipt,part,txns,{},NS(post=post),'csrf')
    result=R.apply_session(fc,state,receipt,part,txns,txns[R.BVID],NS(post=post),'csrf')
    assert result['status']=='edit_requested' and len(calls)==1
    platform['archive']['title']=R.TITLE
    result=R.apply_session(fc,state,receipt,part,txns,txns[R.BVID],NS(post=post),'csrf')
    assert result['status']=='verified' and len(calls)==1
