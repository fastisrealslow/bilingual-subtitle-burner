import copy
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import media_revision as revision
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import media_repair


def test_revision_render_preserves_live_receipts_and_other_dedup_history():
    state=dict(published={revision.SLUG:dict(parts=[dict(bvid=revision.BVID,
        source_sha256=revision.SOURCE_SHA,fingerprints=dict(sha256=revision.OLD_SHA))]),
        'another':dict(bvid='other')},daily_publish=dict(count=1))
    original=copy.deepcopy(state)
    local=revision.render_history(state,revision.BVID,revision.SLUG)
    assert state==original and local['published']=={'another':dict(bvid='other')}
    assert local['daily_publish']==dict(count=1)
    with pytest.raises(ValueError):revision.render_history(state,'other',revision.SLUG)
    state['published'][revision.SLUG]['parts'][0]['fingerprints']['sha256']='newer'
    with pytest.raises(ValueError):revision.render_history(state,revision.BVID,revision.SLUG)


def test_repair_payload_preserves_archive_metadata_and_replaces_exactly_one_file():
    data=dict(archive=dict(aid=media_repair.AID,bvid=media_repair.BVID,title='原标题',
        copyright=2,source='公开访谈',tag='林园,价值投资',is_only_self=0),
        videos=[dict(cid=123,filename='old',title='原分集名',desc='原说明')])
    original=copy.deepcopy(data)
    payload=media_repair.replacement_payload(data,'new-file')
    assert data==original
    assert payload['aid']==media_repair.AID and payload['title']=='原标题'
    assert payload['tag']=='林园,价值投资' and payload['is_only_self']==0
    assert payload['videos']==[dict(filename='new-file',title='原分集名',desc='原说明')]
    assert 'cid' not in payload['videos'][0]
    with pytest.raises(ValueError):media_repair.replacement_payload(data,'old')
    data['videos'].append(dict(filename='another'))
    with pytest.raises(ValueError):media_repair.replacement_payload(data,'new-file')
    data['videos'].pop();data['archive']['aid']=1
    with pytest.raises(ValueError):media_repair.replacement_payload(data,'new-file')


def test_repair_known_outcome_never_uploads_a_second_post():
    import json
    from types import SimpleNamespace
    sha='a'*64
    manifest=dict(bvid=media_repair.BVID,old_sha256=media_repair.OLD_SHA,new_sha256=sha)
    state=dict(published={media_repair.SLUG:dict(parts=[dict(bvid=media_repair.BVID)])},
        media_revisions={media_repair.BVID:dict(status='verified',new_sha256=sha)},
        daily_publish=dict(count=1))
    fc=SimpleNamespace(gh=lambda *a,**k:json.dumps(manifest),load_state=lambda:state)
    assert media_repair.repair_known_media({},fc)['new_posts']==0
    assert state['daily_publish']['count']==1
