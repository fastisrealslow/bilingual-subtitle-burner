import base64
import copy
import importlib.util
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
spec=importlib.util.spec_from_file_location('state_merge_fc',Path(__file__).resolve().parents[1]/'linyuan/fc/index.py')
fc=importlib.util.module_from_spec(spec);spec.loader.exec_module(fc)


def initial():
    return dict(dispatched=[dict(slug='upgrade',ts=1,failed=True,failure_stage='stock-upgrade',stock_upgrade_status='rendering')],published={})


def test_transient_state_read_preserves_daily_quota_and_receipts(monkeypatch):
    import io
    state=initial();state.update(daily_publish=dict(date='2026-09-13',count=4),
                                published={'done':{'bvid':'BVexisting'}})
    calls=[]
    def request(req,**kwargs):
        calls.append(req.method)
        if len(calls)==1:raise TimeoutError('network timeout')
        response=io.BytesIO(json.dumps(state).encode());response.headers={}
        return response
    monkeypatch.setattr(fc.urllib.request,'urlopen',request)
    monkeypatch.setattr(fc.time,'sleep',lambda _:None)
    assert fc.load_state()==state
    assert calls==['GET','GET']


def test_truncated_catalogue_read_retries_but_mutation_never_replays(monkeypatch):
    import io
    from http.client import IncompleteRead
    calls=[]
    class Truncated(io.BytesIO):
        def read(self,*a):raise IncompleteRead(b'partial',26452)
    def request(req,**kwargs):
        calls.append(req.method)
        response=Truncated() if len(calls)==1 else io.BytesIO(b'{"items":[]}')
        response.headers={};return response
    monkeypatch.setattr(fc.urllib.request,'urlopen',request)
    monkeypatch.setattr(fc.time,'sleep',lambda _:None)
    assert fc.gh('GET','/catalogue')=={'items':[]}
    assert calls==['GET','GET']
    calls.clear()
    with pytest.raises(IncompleteRead):fc.gh('POST','/dispatch',{})
    assert calls==['POST']


@pytest.mark.parametrize('response',[b'',b'{}',b'{"dispatched":[],"published":[]}'])
def test_unavailable_or_invalid_history_cannot_become_an_empty_queue(monkeypatch,response):
    monkeypatch.setattr(fc,'gh',lambda *a,**kw:response)
    monkeypatch.setattr(fc.time,'sleep',lambda _:None)
    with pytest.raises(RuntimeError,match='禁止按空历史继续'):fc.load_state()


def test_fc_does_not_erase_concurrent_admission_or_new_dispatch():
    base=initial();local=copy.deepcopy(base);remote=copy.deepcopy(base)
    local['last_check']=2;local['dispatched'].append(dict(slug='normal',ts=2))
    remote['dispatched'][0].update(failed=False,stock_upgrade_status='verified',parts_total=1)
    remote['dispatched'][0].pop('failure_stage')
    remote['dispatched'].append(dict(slug='other',ts=3))
    merged=fc.merge_state_changes(base,local,remote)
    assert merged['last_check']==2
    assert {r['slug'] for r in merged['dispatched']}=={'upgrade','normal','other'}
    assert merged['dispatched'][0]==remote['dispatched'][0]


def test_new_editorial_hold_survives_stale_publisher_failure_update():
    base=initial();base['dispatched'][0].update(stock_upgrade_status='verified',failed=False)
    local=copy.deepcopy(base);remote=copy.deepcopy(base)
    local['dispatched'][0].update(failed=True,failure_stage='old-result')
    remote['dispatched'][0].update(failed=True,failure_stage='stock-review',stock_upgrade_status='held',stock_upgrade_hold_reason='incomplete source')
    assert fc.merge_state_changes(base,local,remote)['dispatched'][0]==remote['dispatched'][0]


def test_parallel_publication_receipts_and_progress_are_retained():
    base=dict(dispatched=[dict(slug='batch',ts=1,processed_part_indices=[])],published={'batch':{'parts':[]}})
    local=copy.deepcopy(base);remote=copy.deepcopy(base)
    local['published']['batch']['parts']=[dict(part_index=0,bvid='BVone',status='published')]
    remote['published']['batch']['parts']=[dict(part_index=1,bvid='BVtwo',status='published')]
    local['dispatched'][0]['processed_part_indices']=[0]
    remote['dispatched'][0]['processed_part_indices']=[1]
    merged=fc.merge_state_changes(base,local,remote)
    assert {p['bvid'] for p in merged['published']['batch']['parts']}=={'BVone','BVtwo'}
    assert fc.processed_part_indices(merged['dispatched'][0])=={0,1}
    assert merged['dispatched'][0]['published_parts']==2


def test_remote_only_change_and_local_deletion_are_distinguished():
    base={'untouched':{'value':1},'obsolete':1};local={'untouched':{'value':1}}
    remote={'untouched':{'value':2},'obsolete':1,'added':3}
    assert fc.merge_state_changes(base,local,remote)=={'untouched':{'value':2},'added':3}


def test_save_retries_merge_again_and_keeps_existing_entry_references(monkeypatch):
    base=initial();st=fc.StateSnapshot(base);entry=st['dispatched'][0]
    st['last_check']=2
    stored=copy.deepcopy(base);stored['dispatched'][0].update(failed=False,stock_upgrade_status='verified')
    attempts=[]
    def gh(method,path,payload=None,**kwargs):
        nonlocal stored
        if fc.STATE_KEY not in path:return {'sha':'mirror'}
        if method=='GET':return {'sha':str(len(attempts)), 'content':base64.b64encode(json.dumps(stored).encode()).decode()}
        attempts.append(payload)
        if len(attempts)==1:
            stored['published']['external']={'bvid':'BVconcurrent'}
            raise RuntimeError('409 conflict')
        stored=json.loads(base64.b64decode(payload['content']))
    monkeypatch.setattr(fc,'gh',gh);monkeypatch.setattr(fc.time,'sleep',lambda _:None)
    fc.save_state(st)
    assert stored['published']['external']['bvid']=='BVconcurrent'
    assert stored['dispatched'][0]['stock_upgrade_status']=='verified'
    assert stored['last_check']==2
    entry['processed_part_indices']=[0]
    fc.save_state(st)
    assert fc.processed_part_indices(stored['dispatched'][0])=={0}
    assert stored['dispatched'][0]['stock_upgrade_status']=='verified'


def test_unavailable_latest_state_never_causes_blind_put(monkeypatch):
    calls=[]
    def gh(method,path,*args,**kwargs):
        calls.append(method)
        raise RuntimeError('temporary GET failure')
    monkeypatch.setattr(fc,'gh',gh);monkeypatch.setattr(fc.time,'sleep',lambda _:None)
    fc.save_state(fc.StateSnapshot(initial()),retries=1)
    assert calls==['GET']


def test_contiguous_completion_never_regresses_on_parallel_save():
    base=dict(dispatched=[dict(slug='batch',ts=1,published_parts=1,processed_part_indices=[])])
    local=copy.deepcopy(base);remote=copy.deepcopy(base)
    local['dispatched'][0]['published_parts']=2
    remote['dispatched'][0]['published_parts']=3
    merged=fc.merge_state_changes(base,local,remote)
    assert merged['dispatched'][0]['published_parts']==3


def test_state_above_github_inline_limit_reads_exact_blob_before_cas(monkeypatch):
    stored=initial()
    stored['large_evidence']='x'*(1024*1024)
    st=fc.StateSnapshot(stored)
    st['published']['wide']={'bvid':'BV1t3Yq6KEyo'}
    calls=[]
    def gh(method,path,payload=None,**kwargs):
        calls.append((method,path))
        if method=='GET' and fc.STATE_KEY in path:
            return {'sha':'large-sha','content':'','encoding':'none'}
        if method=='GET' and path=='/git/blobs/large-sha':
            return {'sha':'large-sha','encoding':'base64',
                    'content':base64.b64encode(json.dumps(stored).encode()).decode()}
        if method=='PUT' and fc.STATE_KEY in path:
            assert payload['sha']=='large-sha'
            saved=json.loads(base64.b64decode(payload['content']))
            assert saved['large_evidence']==stored['large_evidence']
            assert saved['published']['wide']['bvid']=='BV1t3Yq6KEyo'
        return {'sha':'mirror'}
    monkeypatch.setattr(fc,'gh',gh)
    assert fc.save_state(st) is True
    assert ('GET','/git/blobs/large-sha') in calls


def test_mismatched_large_state_blob_never_writes(monkeypatch):
    calls=[]
    def gh(method,path,*args,**kwargs):
        calls.append(method)
        return {'sha':'other' if '/git/blobs/' in path else 'expected',
                'content':'','encoding':'none'}
    monkeypatch.setattr(fc,'gh',gh)
    monkeypatch.setattr(fc.time,'sleep',lambda _:None)
    assert fc.save_state(fc.StateSnapshot(initial()),retries=1) is False
    assert calls==['GET','GET']
