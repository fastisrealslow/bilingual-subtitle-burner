import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from curated_editorial import source_ranges


def test_reviewed_ranges_bind_source_boundaries_and_duration(tmp_path):
    path=tmp_path/'profile.json'
    path.write_text(json.dumps({'sources':{'source':[
        dict(start=1,end=122,opening='独立话题',ending='完整结论',topic='讨论')
    ]}}))
    cues=[dict(start=1,end=10,text='独立话题'),dict(start=12,end=122,text='完整结论')]
    assert source_ranges(cues,'other',path) is None
    assert source_ranges(cues,'source',path)[0][:2]==(0,1)
    cues[-1]['end']=42
    with pytest.raises(ValueError,match='duration or sentence boundary'):
        source_ranges(cues,'source',path)
    cues[-1].update(end=122,text='半句话')
    with pytest.raises(ValueError,match='opening or ending'):
        source_ranges(cues,'source',path)
