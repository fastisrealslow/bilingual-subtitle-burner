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


def test_registered_recovery_keeps_requested_parts_for_future_automatic_retries(monkeypatch):
    source='https://www.bilibili.com/video/BV1hj411X7BL'
    origin=dict(slug='original',source_url=source,title='原始访谈',ts=1)
    state=dict(dispatched=[origin],published={'already-published':{'bvid':'BV1t3Yq6KEyo'}})
    monkeypatch.setattr(recovery,'read_state',lambda:({'sha':'state-before'},json.loads(json.dumps(state))))
    calls=[]
    monkeypatch.setattr(recovery,'gh',lambda *a,**kw:calls.append((a,kw)))
    monkeypatch.setattr(sys,'argv',['register','--origin-slug','original','--expected-source',source,
        '--suffix','reserve','--selected-parts','1,3','--evidence-run','818'])
    recovery.main()
    assert 'selected_parts=1,3' in calls[0][0]
    written=json.loads(base64.b64decode(calls[1][1]['body']['content']))
    assert written['dispatched'][-1]['selected_parts']=='1,3'
    assert written['published']==state['published']
