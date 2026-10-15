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
    public=dict(aid=r.AID,bvid=r.BVID,owner=dict(mid=r.OWNER_MID),ugc_season=dict(id=r.SEASON_ID))
    class Session:
        public_view=public
        def get(self,url,*a,**kw):
            value=self.public_view if '/web-interface/view' in url else current
            return NS(status_code=200,headers={},json=lambda:dict(code=0,data=copy.deepcopy(value)))
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
    return fc,state,current,calls,Client()


@pytest.mark.parametrize('lost',[False,True])
def test_only_original_archive_changed_and_lost_reply_never_reposts(tmp_path,monkeypatch,lost):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch,lost)
    assert r.apply(fc,tmp_path,client,'test-csrf',lambda *a:None)['status']=='verified'
    assert r.apply(fc,tmp_path,client,'test-csrf',lambda *a:None)['status']=='already_verified'
    assert calls==dict(uploads=1,edits=1)
    assert state['daily_publish']==dict(count=3)
    assert state['published'][r.SLUG]['bvid']=='BVother'
    assert state['published'][r.SLUG]['title']=='Other part'
    assert current['archive']['title']==r.TITLE


def test_changed_original_is_rejected_before_upload(tmp_path,monkeypatch):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch)
    current['videos'][0]['cid']=123
    with pytest.raises(ValueError,match='already changed'):r.apply(fc,tmp_path,client,'test',lambda *a:None)
    assert calls==dict(uploads=0,edits=0)


def test_real_creator_response_without_season_keeps_existing_collection(tmp_path,monkeypatch):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch)
    current['archive'].pop('season_id');current['in_season']=True
    assert r.apply(fc,tmp_path,client,'test',lambda *a:None)['status']=='verified'
    assert current['archive']['season_id']==r.SEASON_ID


def test_collection_changed_during_upload_never_edits(tmp_path,monkeypatch):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch)
    client._BiliBili__session.public_view['ugc_season']['id']=123
    with pytest.raises(ValueError,match='collection membership'):
        r.apply(fc,tmp_path,client,'test',lambda *a:None)
    assert calls==dict(uploads=1,edits=0)


def test_unverified_video_never_reaches_login_edit_or_upload(tmp_path,monkeypatch):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='fingerprint'):
        r.apply(fc,tmp_path,client,'test',lambda *a:'Actual MP4 fingerprint mismatch')
    assert calls==dict(uploads=0,edits=0)


@pytest.mark.parametrize('status',['in_progress','completed'])
def test_prior_live_upload_is_never_raced_and_finished_pre_edit_upload_can_resume(tmp_path,monkeypatch,status):
    fc,state,current,calls,client=environment(tmp_path,monkeypatch)
    state[r.STATE_KEY]={r.BVID:dict(status='upload_requested',workflow_run_id='7',
        old_sha256=r.OLD_SHA,new_sha256=r.sha(tmp_path/'final.mp4'),
        created_at=1,original_archive=copy.deepcopy(current))}
    fc.gh=lambda *a,**kw:dict(status=status,conclusion='failure' if status=='completed' else None)
    result=r.apply(fc,tmp_path,client,'test',lambda *a:None)
    if status=='in_progress':
        assert result['status']=='awaiting_prior_upload' and calls==dict(uploads=0,edits=0)
    else:
        assert result['status']=='verified' and calls==dict(uploads=1,edits=1)
        assert state[r.STATE_KEY][r.BVID]['abandoned_upload_attempts'][0]['no_edit_requested'] is True


def test_deployed_route_takes_shared_lease_and_only_exact_target(monkeypatch):
    path=Path(__file__).resolve().parents[1]/'linyuan/fc/index.py'
    spec=importlib.util.spec_from_file_location('tracking_route_fc',path)
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module);spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules,'tracking_revision',NS(apply=lambda fc:dict(status='verified',bvid=r.BVID,new_posts=0)))
    monkeypatch.setattr(module,'flush_logs',lambda:None)
    def lease(kind,action):
        assert kind=='publish'
        return action()
    monkeypatch.setattr(module,'run_with_lease',lease)
    result=module.handler(json.dumps(dict(triggerName='repair-reported-tracking-20261015')),None)
    assert result==dict(status='verified',bvid=r.BVID,new_posts=0)
