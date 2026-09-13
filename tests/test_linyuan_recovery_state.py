"""Recovery must keep existing receipts even above GitHub's inline size limit."""
import base64
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import register_selection_recovery as recovery


def test_large_state_reads_the_blob_bound_to_the_cas_sha(monkeypatch):
    state={'published':{'wide':{'bvid':'BV1t3Yq6KEyo'}},'history':'x'*(1024*1024)}
    calls=[]
    def gh(*args):
        calls.append(args)
        if '/contents/' in args[1]:
            return json.dumps({'sha':'exact-state','encoding':'none','content':''})
        assert args[1].endswith('/git/blobs/exact-state')
        return json.dumps({'sha':'exact-state','encoding':'base64',
            'content':base64.b64encode(json.dumps(state).encode()).decode()})
    monkeypatch.setattr(recovery,'gh',gh)
    doc,actual=recovery.read_state()
    assert doc['sha']=='exact-state'
    assert actual==state
    assert len(calls)==2


def test_missing_or_changed_state_blob_blocks_dispatch(monkeypatch):
    responses=iter([{'sha':'expected','content':''},
                    {'sha':'changed','encoding':'base64','content':'e30='}])
    monkeypatch.setattr(recovery,'gh',lambda *args:json.dumps(next(responses)))
    with pytest.raises(ValueError,match='refusing recovery dispatch'):
        recovery.read_state()
