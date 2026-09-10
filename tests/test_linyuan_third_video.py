import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan/fc'))
import reviewed_third_video as R


def environment(monkeypatch, tmp_path, uncertain=False, failed_save=False):
    item = dict(batch=R.BATCH,bvid=R.BVID,slug=R.SLUG,old_video_sha256=R.OLD_SHA,
        old_title='accepted',title='accepted',video='third-video.mp4',video_sha256='new-sha',
        fingerprints={'sha256':'new-sha','transcript_ngrams':['preserved']},
        source_url='original-source',source_range=[1267.56,1501.56],duration_sec=234)
    (tmp_path/'third-video-manifest.json').write_text(json.dumps(item))
    monkeypatch.setattr(R,'DIRECTORY',tmp_path)
    monkeypatch.setattr(R,'validated_asset',lambda *a:tmp_path/'third-video.mp4')
    original = dict(archive=dict(aid=123,bvid=R.BVID,title='accepted',cover='https://example.test/cover.jpg',
        desc='keep description',tag='keep tags',copyright=2,source='keep source',is_only_self=0),
        videos=[dict(filename='old-file',cid=456,title='accepted',desc='keep part')])
    archive=[copy.deepcopy(original)]
    state=dict(published={R.SLUG:dict(parts=[dict(bvid=R.BVID,title='accepted',
        fingerprints={'sha256':R.OLD_SHA},source_segments=[{'start':1267.56,'end':1505.4}])]),
        'other':{'parts':[{'bvid':'another','fingerprints':{'sha256':'other'}}]}},
        reviewed_updates_0910={R.BVID:dict(status='verified',cover=original['archive']['cover'],original_archive=copy.deepcopy(original)),
                              'other':{'status':'verified'}},
        daily_publish={'count':3},dispatched=[{'slug':R.SLUG,'processed_part_indices':[0]}])
    stored=[copy.deepcopy(state)]
    calls=dict(uploads=0,edits=0)
    class Session:
        def mount(self,*a):pass
        def get(self,url,params,timeout):
            assert params['bvid']==R.BVID
            return SimpleNamespace(status_code=200,headers={},json=lambda:dict(code=0,data=copy.deepcopy(archive[0])))
        def post(self,url,timeout,**kwargs):
            assert url.endswith('/edit')
            payload=kwargs['json']
            assert payload['bvid']==R.BVID and payload['title']=='accepted'
            assert payload['cover']==original['archive']['cover']
            assert payload['desc']=='keep description' and payload['tag']=='keep tags'
            assert 'cid' not in payload['videos'][0]
            calls['edits']+=1
            archive[0]['videos']=copy.deepcopy(payload['videos'])
            if uncertain:raise TimeoutError('accepted, but response lost')
            return SimpleNamespace(json=lambda:dict(code=0,message='OK'))
    class Client:
        def __init__(self,*a):self._BiliBili__session=Session()
        def login_by_cookies(self,*a):pass
        def upload_file(self,*a,**kwargs):
            calls['uploads']+=1
            return {'filename':'replacement'}
    monkeypatch.setitem(sys.modules,'biliup.plugins.bili_webup',SimpleNamespace(BiliBili=Client,Data=lambda:None))
    monkeypatch.setenv('BILIBILI_COOKIES',json.dumps({'cookies':[{'name':'DedeUserID','value':'123'},
        {'name':'bili_jct','value':'test'}]}))
    def save(s):
        if not failed_save:stored[0]=copy.deepcopy(s)
    fc=SimpleNamespace(OWNER_MID=123,load_state=lambda:copy.deepcopy(stored[0]),save_state=save,
                       log_event=lambda *a:None)
    return fc,stored,calls,state,archive


def test_only_third_video_replaced_once_preserves_dedup_history(monkeypatch,tmp_path):
    fc,stored,calls,before,_=environment(monkeypatch,tmp_path)
    assert R.apply(fc)['status']=='verified'
    assert R.apply(fc)['status']=='already_verified'
    assert calls==dict(uploads=1,edits=1)
    for key in ('daily_publish','dispatched','reviewed_updates_0910'):
        assert stored[0][key]==before[key]
    assert stored[0]['published']['other']==before['published']['other']
    part=stored[0]['published'][R.SLUG]['parts'][0]
    assert part['source_segments']==before['published'][R.SLUG]['parts'][0]['source_segments']
    assert part['editorial_revision_history'][0]['fingerprints']['sha256']==R.OLD_SHA
    assert part['fingerprints']['sha256']=='new-sha'


def test_lost_edit_response_reconciles_without_second_submission(monkeypatch,tmp_path):
    fc,stored,calls,_,_=environment(monkeypatch,tmp_path,uncertain=True)
    with pytest.raises(TimeoutError):R.apply(fc)
    assert stored[0][R.STATE_KEY][R.BVID]['status']=='edit_requested'
    assert R.apply(fc)['status']=='verified'
    assert calls==dict(uploads=1,edits=1)


def test_no_upload_when_receipt_cannot_persist(monkeypatch,tmp_path):
    fc,_,calls,_,_=environment(monkeypatch,tmp_path,failed_save=True)
    with pytest.raises(RuntimeError,match='did not persist'):R.apply(fc)
    assert calls==dict(uploads=0,edits=0)


def test_external_video_revision_is_never_overwritten(monkeypatch,tmp_path):
    fc,stored,calls,_,_=environment(monkeypatch,tmp_path)
    stored[0]['published'][R.SLUG]['parts'][0]['fingerprints']['sha256']='another-revision'
    with pytest.raises(ValueError,match='receipt changed'):R.apply(fc)
    assert calls==dict(uploads=0,edits=0)
