import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import produce_cn as P

s=importlib.util.spec_from_file_location('audit_control',ROOT/'scripts/check_linyuan_transcript_style_cpu.py')
C=importlib.util.module_from_spec(s);s.loader.exec_module(C)


def setup(monkeypatch,tmp_path,done='stop'):
    monkeypatch.setattr(P,'BASE',tmp_path)
    monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'LOCAL_LLM_MODEL','qwen3.5:9b')
    monkeypatch.setattr(P,'LOCAL_LLM_URL','http://127.0.0.1:11434/api/chat')
    monkeypatch.delenv('TEXT_RUNTIME_UNAVAILABLE',raising=False)
    requests=[]
    def local_reply(request,**kwargs):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps(dict(done_reason=done,message=dict(content='{"issues":[],"explanation":"unit stub, not model evidence"}'))).encode())
    monkeypatch.setattr(P.urllib.request,'urlopen',local_reply)
    return requests


def test_control_uses_real_production_seed_and_no_response_cache(monkeypatch,tmp_path):
    requests=setup(monkeypatch,tmp_path)
    schema=dict(type='object',properties=dict(issues=dict(type='array')))
    for prompt in ['original source','original source','changed source']:
        _,payload,_=C.production_audit_request(P,prompt,schema)
        assert payload['options']['temperature']==0 and payload['options']['num_predict']==1200
        key=hashlib.sha256(json.dumps(dict(backend='local',model='qwen3.5:9b',runtime_version=4,
            m=[dict(role='user',content=prompt)],t=0,mt=1200,schema=schema),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        assert payload['options']['seed']==int(key[:8],16)&0x7fffffff
    assert len(requests)==3
    assert requests[0]==requests[1] and requests[1]['options']['seed']!=requests[2]['options']['seed']


def test_control_retains_actual_production_output_truncation_rejection(monkeypatch,tmp_path):
    requests=setup(monkeypatch,tmp_path,done='length')
    with pytest.raises(P.LocalTextUnavailable,match='长度上限'):
        C.production_audit_request(P,'original source',dict(type='object'))
    assert len(requests)==1 and not list((tmp_path/'.llm_cache').glob('*.json'))
