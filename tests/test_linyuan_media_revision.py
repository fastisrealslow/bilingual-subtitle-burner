import copy
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import media_revision as revision


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
