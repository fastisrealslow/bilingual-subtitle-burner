"""A full-interview add-on must neither reselect highlights nor lose good clips."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as p


@pytest.mark.parametrize('has_clip,full_fails',[(False,False),(True,False),(False,True),(True,True)])
def test_actual_full_entry_range_retry_classification_and_batch_isolation(tmp_path,has_clip,full_fails):
    source=tmp_path/'source.mp4';source.touch()
    cues=[dict(start=i*30,end=(i+1)*30,text='同一访谈原声。') for i in range(6)]
    report=dict(passed=True,resolution=dict(width=1280,height=720),visual_identity={})
    full_calls=[]
    def render(src,work,out,rows,*args,**kw):
        is_full=kw.get('pick_cache_suffix')=='_full'
        if is_full:
            full_calls.append(kw)
            picks=kw['preselected_picks']
            assert [(r['start'],r['end']) for r in picks]==[(0,len(cues)-1)]
            assert p.editorial.range_seconds(rows,picks[0])==180
            assert not (work/'highlights_full.json').exists()
            if full_fails:raise p.LocalTextUnavailable('CPU服务未完成')
        name='final_full.mp4' if is_full else 'final.mp4'
        (out/name).write_bytes(b'accepted-test-media')
        return dict(final=name,title='完整访谈' if is_full else '已通过切片',duration_sec=180,
                    resolution=dict(width=1280,height=720),render_mode='direct',watermark_verified=True)
    with patch.object(p,'BASE',tmp_path),patch.object(p,'load_key',return_value=''), \
         patch.object(p,'run_source_quality_gate',return_value=report), \
         patch.object(p,'transcribe',return_value=cues), \
         patch.object(p,'pick_highlights',return_value=[dict(start=0,end=5,score=8)] if has_clip else []), \
         patch.object(p,'_produce_one',side_effect=render), \
         patch.object(sys,'argv',['produce','--source',str(source),'--slug','full-case',
                                 '--split-highlights','--include-full']):
        result=p.main()
    assert len(full_calls)==1
    assert result==(2 if full_fails and not has_clip else 0)
    batch=json.loads((tmp_path/'deliver/full-case/batch_report.json').read_text())
    assert batch['accepted']==int(has_clip)+int(not full_fails)
    if full_fails:
        failure=batch['rejected'][-1]
        assert failure['part']=='full' and failure['retryable'] is True
        assert batch['retryable'] is (not has_clip)
    if has_clip:
        metadata=json.loads((tmp_path/'deliver/full-case/meta.json').read_text())
        assert 'final.mp4' in json.dumps(metadata)
