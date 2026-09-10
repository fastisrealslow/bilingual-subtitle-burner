import base64
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan/fc'))
import reviewed_updates as R


def test_payload_preserves_archive_settings_and_existing_media():
    item = dict(bvid='BV16QYK6qEwm', old_title='old', title='new')
    data = dict(archive=dict(aid=123,bvid=item['bvid'],title='old',tag='tags',
        copyright=2,desc='description',is_only_self=0),
        videos=[dict(filename='old-file',cid=789,title='old',desc='part')])
    original=copy.deepcopy(data)
    result=R.payload_for(data,item,'new-cover')
    assert data==original
    assert result['videos'][0]['filename']=='old-file' and result['videos'][0]['cid']==789
    assert result['desc']=='description' and result['tag']=='tags' and result['is_only_self']==0
    result=R.payload_for(data,item,'new-cover','replacement')
    assert result['videos'][0]['filename']=='replacement' and 'cid' not in result['videos'][0]
    data['archive']['title']='unrelated edit'
    with pytest.raises(ValueError):R.payload_for(data,item,'cover')
    data=original;data['videos'].append(dict(filename='other'))
    with pytest.raises(ValueError):R.payload_for(data,item,'cover')


def environment(monkeypatch, uncertain=False, failed_save=False):
    items=json.loads((R.DIRECTORY/'manifest.json').read_text())['items']
    state={'published':{i['slug']:{'title':i['old_title'],'parts':[dict(bvid=i['bvid'],
        title=i['old_title'],fingerprints={'sha256':i['old_video_sha256']})]} for i in items},
        'daily_publish':{'count':2}}
    stored=[copy.deepcopy(state)]
    archives={i['bvid']:dict(archive=dict(bvid=i['bvid'],aid=n+123,title=i['old_title'],
        cover='https://example.test/old.jpg',desc='keep'),
        videos=[dict(filename='old-'+i['bvid'],cid=n+1,title=i['old_title'])]) for n,i in enumerate(items)}
    calls={'covers':0,'uploads':0,'edits':0}
    class Session:
        def mount(self,*a):pass
        def get(self,url,params,timeout):
            assert url == 'https://member.bilibili.com/x/vupre/web/archive/view'
            return SimpleNamespace(status_code=200, headers={'Content-Type':'application/json'},
                json=lambda:dict(code=0,data=copy.deepcopy(archives[params['bvid']])))
        def post(self,url,timeout,**kwargs):
            if url.endswith('/cover/up'):
                calls['covers']+=1
                raw=base64.b64decode(kwargs['data']['cover'].split(',',1)[1])
                assert any(raw==(R.DIRECTORY/i['cover']).read_bytes() for i in items)
                return SimpleNamespace(json=lambda:dict(code=0,data={'url':f'https://example.test/{calls["covers"]}.jpg'}))
            assert url.endswith('/edit')
            calls['edits']+=1
            payload=kwargs['json'];target=archives[payload['bvid']]
            target['archive'].update({k:v for k,v in payload.items() if k!='videos'})
            target['videos']=copy.deepcopy(payload['videos'])
            if uncertain and calls['edits']==1:raise TimeoutError('response lost after accepted edit')
            return SimpleNamespace(json=lambda:dict(code=0))
    session=Session()
    class Client:
        def __init__(self,*a):self._BiliBili__session=session
        def login_by_cookies(self,*a):pass
        def upload_file(self,*a,**k):
            calls['uploads']+=1;return {'filename':'accepted-video'}
    monkeypatch.setitem(sys.modules,'biliup.plugins.bili_webup',SimpleNamespace(BiliBili=Client,Data=lambda:None))
    monkeypatch.setenv('BILIBILI_COOKIES',json.dumps({'cookies':[{'name':'DedeUserID','value':'123'},
        {'name':'bili_jct','value':'test-csrf'}]}))
    monkeypatch.setattr(R.subprocess,'run',lambda *a,**k:None)
    def save(s):
        if not failed_save:stored[0]=copy.deepcopy(s)
    fc=SimpleNamespace(OWNER_MID=123,mp4_duration=lambda p:120.2,load_state=lambda:copy.deepcopy(stored[0]),
        save_state=save,log_event=lambda *a:None)
    return fc,calls,stored


def test_exact_three_updates_and_rerun_do_not_create_posts(monkeypatch):
    fc,calls,stored=environment(monkeypatch)
    assert R.apply(fc)['status']=='verified'
    assert calls==dict(covers=3,uploads=1,edits=3)
    assert stored[0]['daily_publish']=={'count':2}
    assert len(stored[0]['published'])==3
    assert R.apply(fc)['status']=='already_verified'
    assert calls==dict(covers=3,uploads=1,edits=3)


def test_lost_response_is_reconciled_without_reupload_or_reedit(monkeypatch):
    fc,calls,stored=environment(monkeypatch,uncertain=True)
    with pytest.raises(TimeoutError):R.apply(fc)
    assert R.apply(fc)['status']=='verified'
    assert calls==dict(covers=3,uploads=1,edits=3)


def test_state_save_failure_stops_before_edit(monkeypatch):
    fc,calls,stored=environment(monkeypatch,failed_save=True)
    with pytest.raises(RuntimeError,match='did not persist'):R.apply(fc)
    assert calls==dict(covers=1,uploads=0,edits=0)


def test_asset_hash_mismatch_is_rejected():
    with pytest.raises(ValueError):R.asset('cover-1.jpg','0'*64)
    with pytest.raises(ValueError):R.asset('../cover-1.jpg','0'*64)


@pytest.mark.parametrize('status,payload,expected', [(412,{},'HTTP 412'),
    (404,{},'HTTP 404'), (200,{'code':-101},'-101'), (200,[],'invalid JSON shape')])
def test_creator_detail_reports_failure_before_updates(status,payload,expected):
    session=SimpleNamespace(get=lambda *a,**kw:SimpleNamespace(status_code=status,
        headers={'Content-Type':'application/json'},json=lambda:payload))
    with pytest.raises(RuntimeError,match=expected):R.creator_detail(session,'BV16vYT6ME5s')
