import copy
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import replace_reported_tracking as r


def environment(tmp_path,monkeypatch,lost_reply=False):
    video=tmp_path/'final.mp4';video.write_bytes(b'repaired video')
    meta=dict(title=r.TITLE,source_sha256=r.SOURCE_SHA,final=video.name,
        tracking_revision=dict(old_sha256=r.OLD_SHA),segments=[dict(start=70.52,end=172.44)],
        desc='讲话时间：2020-12-14（待核实）',fingerprints=dict(sha256=r.sha(video)),
        duration_sec=101.92,resolution=dict(width=720,height=1280))
    (tmp_path/'meta.json').write_text(json.dumps(meta))
    part=dict(bvid=r.BVID,title=r.TITLE,source_sha256=r.SOURCE_SHA,
        fingerprints=dict(sha256=r.OLD_SHA),source_segments=meta['segments'])
    state=dict(published={r.SLUG:dict(bvid='BVother',title='Other part',
        parts=[dict(bvid='BVother'),part])},daily_publish=dict(count=3))
    current=dict(archive=dict(aid=r.AID,bvid=r.BVID,title=r.TITLE,cover='https://example.test/cover.jpg',
        copyright=3,creation_statement=dict(id=0),season_id=9324935),
        videos=[dict(cid=r.OLD_CID,filename='old-file',title=r.TITLE)])
    calls=dict(uploads=0,edits=0)
    class Session:
        def get(self,*a,**kw):return NS(status_code=200,headers={},json=lambda:dict(code=0,data=copy.deepcopy(current)))
        def post(self,url,**kw):
            assert url.endswith('/edit');calls['edits']+=1
            payload=kw['json']
            assert payload['season_id']==9324935 and payload['copyright']==3
            current['archive'].update({k:v for k,v in payload.items() if k!='videos'})
            current['videos']=copy.deepcopy(payload['videos']);current['videos'][0]['cid']=999
            if lost_reply:raise TimeoutError('Accepted but reply lost')
            return NS(json=lambda:dict(code=0))
    class Client:
        _BiliBili__session=Session()
        def upload_file(self,*a,**kw):calls['uploads']+=1;return dict(filename='new-file')
    def save(value):
        state.clear();state.update(copy.deepcopy(value));return True
    fc=NS(load_state=lambda:copy.deepcopy(state),save_state=save,
        editorial=NS(intervals=lambda v:v))
    monkeypatch.setattr(r,'validate_part',lambda *a:None)
    return fc,state,current,calls,Client()


@pytest.mark.parametrize('lost',[False,True])
def test_only_original_archive_changed_and_lost_reply_never_reposts(tmp_path,monkeypatch,lost):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch,lost)
    assert r.apply(fc,tmp_path,client,'test-csrf')['status']=='verified'
    assert r.apply(fc,tmp_path,client,'test-csrf')['status']=='already_verified'
    assert calls==dict(uploads=1,edits=1)
    assert state['daily_publish']==dict(count=3)
    assert state['published'][r.SLUG]['bvid']=='BVother'
    assert state['published'][r.SLUG]['title']=='Other part'
    assert current['archive']['title']==r.TITLE


def test_changed_original_is_rejected_before_upload(tmp_path,monkeypatch):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch)
    current['videos'][0]['cid']=123
    with pytest.raises(ValueError,match='already changed'):r.apply(fc,tmp_path,client,'test')
    assert calls==dict(uploads=0,edits=0)


def test_unverified_video_never_reaches_login_edit_or_upload(tmp_path,monkeypatch):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch)
    monkeypatch.setattr(r,'validate_part',lambda *a:'Actual MP4 fingerprint mismatch')
    with pytest.raises(ValueError,match='fingerprint'):r.apply(fc,tmp_path,client,'test')
    assert calls==dict(uploads=0,edits=0)
