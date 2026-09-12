"""The repair must edit the original archive once, including after a lost reply."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import zipfile
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import stage_revision as R


def environment(monkeypatch,lost_reply=False):
    digest=lambda b:hashlib.sha256(b).hexdigest()
    item=dict(batch='wide-stage-20260912',bvid=R.BVID,slug=R.SLUG,source_sha256=R.SOURCE_SHA,
        old_video_sha256=R.OLD_SHA,old_title='old',title='new',artifact_id=1,
        video_sha256=digest(b'video'),cover_sha256=digest(b'cover'))
    segments=[dict(start=.56,end=128.44)]
    meta=dict(title='new',render_mode='stage_context',final='final.mp4',cover='cover.jpg',
        source_sha256=R.SOURCE_SHA,segments=segments,desc='new description',
        fingerprints=dict(sha256=item['video_sha256']),resolution=dict(width=1280,height=720),duration_sec=127.88)
    stored=[dict(published={R.SLUG:dict(parts=[dict(bvid=R.BVID,title='old',
        fingerprints=dict(sha256=R.OLD_SHA),source_segments=segments)])},daily_publish=dict(count=2))]
    current=dict(archive=dict(bvid=R.BVID,aid=1,title='old',cover='https://example.test/old.jpg',state=0),
                 videos=[dict(filename='old-video',cid=2,title='old')])
    calls=dict(covers=0,uploads=0,edits=0)
    class Session:
        def mount(self,*a):pass
        def get(self,*a,**k):
            return NS(status_code=200,headers={},json=lambda:dict(code=0,data=copy.deepcopy(current)))
        def post(self,url,**kwargs):
            if url.endswith('/cover/up'):
                calls['covers']+=1
                return NS(json=lambda:dict(code=0,data=dict(url='https://example.test/new.jpg')))
            assert url.endswith('/edit')
            calls['edits']+=1;payload=kwargs['json']
            current['archive'].update({k:v for k,v in payload.items() if k!='videos'})
            current['videos']=copy.deepcopy(payload['videos'])
            if lost_reply and calls['edits']==1:raise TimeoutError('accepted but reply lost')
            return NS(json=lambda:dict(code=0))
    class Client:
        def __init__(self,*a):self._BiliBili__session=Session()
        def login_by_cookies(self,*a):pass
        def upload_file(self,*a,**k):
            calls['uploads']+=1;return dict(filename='new-video')
    def download(id,path,attempts):
        with zipfile.ZipFile(path,'w') as z:
            for name,data in [('meta.json',json.dumps(meta)),('final.mp4',b'video'),('cover.jpg',b'cover')]:z.writestr(name,data)
    def save(s):stored[0]=copy.deepcopy(s)
    fc=NS(OWNER_MID=123,gh=lambda *a,**k:json.dumps(item),load_state=lambda:copy.deepcopy(stored[0]),
        save_state=save,download_reviewed_zip=download,log_event=lambda *a:None,
        artifact_quality_error=lambda m:None,artifact_subtitle_error=lambda *a:None,
        artifact_cover_error=lambda *a:None,mp4_duration=lambda p:127.88,
        editorial=NS(intervals=lambda x:x,metadata_error=lambda *a:None))
    monkeypatch.setitem(sys.modules,'biliup.plugins.bili_webup',NS(BiliBili=Client,Data=lambda:None))
    monkeypatch.setenv('BILIBILI_COOKIES',json.dumps(dict(cookies=[dict(name='DedeUserID',value='123'),dict(name='bili_jct',value='test')])))
    monkeypatch.setattr(R.subprocess,'run',lambda *a,**k:None)
    return fc,stored,calls,item


def test_same_archive_repair_is_idempotent(monkeypatch):
    fc,stored,calls,item=environment(monkeypatch)
    assert R.apply(fc)['status']=='verified'
    assert R.apply(fc)['status']=='already_verified'
    assert calls==dict(covers=1,uploads=1,edits=1)
    assert stored[0]['daily_publish']==dict(count=2)
    assert list(stored[0]['published'])==[R.SLUG]
    assert stored[0]['published'][R.SLUG]['parts'][0]['bvid']==R.BVID


def test_lost_edit_reply_is_reconciled_without_second_edit(monkeypatch):
    fc,stored,calls,item=environment(monkeypatch,lost_reply=True)
    with pytest.raises(TimeoutError):R.apply(fc)
    assert R.apply(fc)['status']=='verified'
    assert calls==dict(covers=1,uploads=1,edits=1)


def test_changed_original_or_manifest_is_rejected_before_upload(monkeypatch):
    fc,stored,calls,item=environment(monkeypatch)
    stored[0]['published'][R.SLUG]['parts'][0]['fingerprints']['sha256']='changed'
    with pytest.raises(ValueError,match='receipt changed'):R.apply(fc)
    item['bvid']='another'
    with pytest.raises(ValueError,match='Unrecognized'):R.apply(fc)
    assert calls==dict(covers=0,uploads=0,edits=0)
