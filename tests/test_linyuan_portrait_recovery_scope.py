"""A portrait-only route must not spend ordinary source retry slots."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan/fc'))
import index as F


@pytest.mark.parametrize('profile,layout,expected',[
    ('baseline','portrait',False),('baseline','landscape',False),
    ('field-story-v1','landscape',False),('field-story-v1','portrait',True),
    (None,None,False),
])
def test_portrait_only_code_matches_only_the_affected_requested_profile(monkeypatch,profile,layout,expected):
    entry=dict(slug='old',source_url='https://example.com/source',ts=1,failed=True,
        source_quality_rejected=True,last_error='原画无法通过真人画面清理门禁',
        editorial_profile=profile,output_layout=layout,source_check_attempts=2,quality_retries=1)
    state=dict(dispatched=[entry],published={})
    run=dict(id=123,conclusion='failure',head_sha='old',updated_at='2026-10-22T22:00:00Z')
    patch_text='\n'.join(['+def _produce_with_layout_preference(*args,**kwargs):',
        '+    if portrait_story:','+        crop_w,crop_h=AUDIO_CARD_WIDTH,AUDIO_CARD_HEIGHT'])
    monkeypatch.setattr(F,'gh',lambda *a,**k:dict(files=[dict(filename='linyuan/produce_cn.py',patch=patch_text)]))
    writes=[]
    monkeypatch.setattr(F,'save_state',lambda s:writes.append(True))
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    assert F._recover_changed_production_rule(state,entry,run) is expected
    assert bool(writes)==expected
    assert entry['source_check_attempts']==2 and entry['quality_retries']==1
