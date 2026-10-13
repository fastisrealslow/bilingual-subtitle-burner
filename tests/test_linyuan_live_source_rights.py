import io
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import source_admission_guard as guard

URL='https://www.bilibili.com/video/BV18Zj9zcE4V'


def opener(flag=1,bvid='BV18Zj9zcE4V',code=0):
    def open_url(request,timeout):
        assert request.full_url=='https://api.bilibili.com/x/web-interface/view?bvid=BV18Zj9zcE4V'
        assert timeout==10
        return io.BytesIO(json.dumps({'code':code,'data':{'bvid':bvid,'rights':{'no_reprint':flag}}}).encode())
    return open_url


def test_new_live_ban_blocks_old_unknown_seed_or_cached_media():
    reason,proof=guard.live_bilibili_restriction(URL+'?p=2',opener())
    assert '禁止转载' in reason and proof['no_reprint']==1 and not proof['rights_clearance']
    state={'dispatched':[{'slug':'old','ts':1,'source_url':URL}]}
    assert guard.source_urls('https://example.com/cached.mp4','old',state)==['https://example.com/cached.mp4',URL]


@pytest.mark.parametrize('flag',[0,None,False,'1'])
def test_unrestricted_or_missing_flag_is_never_permission(flag):
    reason,proof=guard.live_bilibili_restriction(URL,opener(flag))
    assert reason is None and not proof['rights_clearance']


@pytest.mark.parametrize('bvid,code',[('other',0),('BV18Zj9zcE4V',-412)])
def test_wrong_or_unavailable_metadata_never_invents_a_verdict(bvid,code):
    reason,proof=guard.live_bilibili_restriction(URL,opener(bvid=bvid,code=code))
    assert reason is None and proof['status']=='unknown' and not proof['rights_clearance']


def test_transient_error_and_nested_urls_are_not_false_source_bans():
    def unavailable(*a,**kw):raise TimeoutError('temporary')
    reason,proof=guard.live_bilibili_restriction(URL,unavailable)
    assert reason is None and proof['status']=='unknown' and proof['error_type']=='TimeoutError'
    assert guard.live_bilibili_restriction('https://example.com/?url='+URL,
        lambda *a,**kw:pytest.fail('nested external URL fetched'))== (None,{'status':'not_bilibili_page'})


def test_main_blocks_staging_before_media_cache_and_retains_live_observation(tmp_path,monkeypatch):
    monkeypatch.setattr(guard,'BASE',tmp_path)
    (tmp_path/'.automation').mkdir()
    (tmp_path/'bilibili_seeds.json').write_text('{"seeds":[]}')
    (tmp_path/'source_restrictions.json').write_text('{"pages":[]}')
    (tmp_path/'.automation/fc_state.json').write_text(json.dumps({'dispatched':[
        {'slug':'old','ts':1,'source_url':URL}]}))
    monkeypatch.setenv('RUN_SOURCE','https://example.com/cached.mp4');monkeypatch.setenv('RUN_SLUG','old')
    monkeypatch.setattr(guard.urllib.request,'urlopen',opener())
    with pytest.raises(SystemExit,match='禁止转载'):guard.main()
    report=json.loads((tmp_path/'deliver/old/_tmp/source_quality.json').read_text())
    assert report['failure_stage']=='source-admission' and not report['passed'] and not report['retryable']
    observations=json.loads((tmp_path/'deliver/old/_tmp/source_admission_observation.json').read_text())
    assert observations[0]['source_bvid']=='BV18Zj9zcE4V' and not observations[0]['rights_clearance']


def test_scheduler_excludes_known_stale_flagged_seeds_without_deleting_history():
    sys.path.insert(0,str(ROOT/'linyuan/fc'))
    import index as fc
    rows=[{'id':bvid,'url':'https://www.bilibili.com/video/'+bvid,
           'title':'林园直播完整访谈','source':'bilibili_series','author':'投资大佬说',
           'extra':{'duration':1600,'no_reprint':0,'direct_dispatch':True}}
          for bvid in ('BV11WL3z8EqR','BV1K9VizFETj','BV13Ej9zZEM9')]
    state={'dispatched':[],'published':{},'rejected':[{'slug':'historical-failure'}]}
    result=fc.source_admission_audit(rows,state)
    assert result['candidate_count']==0 and result['excluded']['reference_only']==3
    assert len(rows)==3 and state['rejected']==[{'slug':'historical-failure'}]


def test_fc_archive_includes_exact_registry_and_guard(tmp_path):
    import importlib.util,zipfile
    spec=importlib.util.spec_from_file_location('rights_package',ROOT/'linyuan/fc/package_code.py')
    package=importlib.util.module_from_spec(spec);spec.loader.exec_module(package)
    with zipfile.ZipFile(package.build(tmp_path/'fc.zip')) as archive:
        assert archive.read('source_restrictions.json')==(ROOT/'linyuan/source_restrictions.json').read_bytes()
        assert archive.read('source_admission_guard.py')==(ROOT/'linyuan/source_admission_guard.py').read_bytes()


def test_old_encoded_inventory_cannot_bypass_a_known_source_ban():
    sys.path.insert(0,str(ROOT/'linyuan/fc'))
    import index as fc
    entry={'slug':'old','source_url':'https://www.bilibili.com/video/BV1tTi5BrEES'}
    assert '禁止转载' in fc.inventory_publication_error({'status':'verified'},entry,{'published':{}})
