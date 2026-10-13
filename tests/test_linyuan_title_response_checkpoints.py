import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import reuse_title_verification as reuse


def evidence(root,model='qwen3.5:9b',name=None):
    folder=root/'linyuan/.llm_cache';folder.mkdir(parents=True)
    path=folder/(name or 'a'*64+'.json')
    path.write_text(json.dumps({'backend':'local','model':model,'content':'实际CPU原始响应'}))
    (root/'title-claim-verification.json').write_text('[{"passed":true}]')
    return path


def test_keyed_raw_responses_do_not_restore_old_approval_or_overwrite_conflict(tmp_path):
    source=tmp_path/'source';target=tmp_path/'current/.llm_cache'
    original=evidence(source)
    assert reuse.copy_response_checkpoints(source,target,'qwen3.5:9b')==1
    assert (target/original.name).read_bytes()==original.read_bytes()
    assert not (target.parent/'title-claim-verification.json').exists()
    assert reuse.copy_response_checkpoints(source,target,'qwen3.5:9b')==1
    (target/original.name).write_text('different response for same key')
    with pytest.raises(ValueError,match='Conflicting'):
        reuse.copy_response_checkpoints(source,target,'qwen3.5:9b')


def test_wrong_model_or_unkeyed_answer_file_never_becomes_checkpoint(tmp_path):
    original=evidence(tmp_path/'wrong-model',model='qwen3:8b')
    with pytest.raises(ValueError,match='model differs'):
        reuse.copy_response_checkpoints(original.parents[2],tmp_path/'out','qwen3.5:9b')
    evidence(tmp_path/'answers',name='manual-answer.json')
    with pytest.raises(ValueError,match='exact request cache key'):
        reuse.copy_response_checkpoints(tmp_path/'answers',tmp_path/'out','qwen3.5:9b')


def test_current_llm_request_hash_misses_old_response_when_prompt_schema_or_budget_changes(tmp_path,monkeypatch):
    import produce_cn as p
    monkeypatch.setattr(p,'BASE',tmp_path)
    monkeypatch.setattr(p,'TEXT_BACKEND','local')
    monkeypatch.setattr(p,'LOCAL_LLM_MODEL','qwen3.5:9b')
    messages=[{'role':'user','content':'Exact original request'}]
    schema={'type':'object'}
    payload={'backend':'local','model':p.LOCAL_LLM_MODEL,'runtime_version':4,
             'm':messages,'t':0,'mt':100,'schema':schema}
    key=hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    cache=tmp_path/'.llm_cache';cache.mkdir()
    (cache/(key+'.json')).write_text(json.dumps({'model':p.LOCAL_LLM_MODEL,'backend':'local','content':'original computation'}))
    monkeypatch.setenv('TEXT_RUNTIME_UNAVAILABLE','true')
    assert p.llm(messages,'',temperature=0,max_tokens=100,response_schema=schema)=='original computation'
    for changed,kwargs in (([{'role':'user','content':'Changed request'}],{}),
                           (messages,{'max_tokens':101}),
                           (messages,{'response_schema':{'type':'array'}})):
        options=dict(temperature=0,max_tokens=100,response_schema=schema);options.update(kwargs)
        with pytest.raises(p.LocalTextUnavailable):p.llm(changed,'',**options)


def test_changed_code_resumes_raw_computation_but_never_skips_current_checks(tmp_path,monkeypatch):
    # Execute main() against a trusted finished main deployment with changed
    # title file SHAs. Approval reuse is forbidden, even if its old rows passed.
    from datetime import datetime,timezone
    monkeypatch.chdir(tmp_path)
    paths=list(reuse.TITLE_FILES)+[reuse.WORKFLOW]
    listing='\n'.join('100644 blob '+('a'*40)+'\t'+p for p in paths)
    monkeypatch.setattr(reuse.subprocess,'check_output',lambda *a,**kw:listing)
    for name,value in [('GITHUB_REPOSITORY','owner/repo'),('GITHUB_RUN_ID','99'),
                       ('RUN_TEXT_MODEL','qwen3.5:9b'),('GITHUB_OUTPUT',str(tmp_path/'out')),
                       ('GITHUB_ENV',str(tmp_path/'env'))]:monkeypatch.setenv(name,value)
    monkeypatch.setattr(reuse,'validate_results',lambda rows:None)
    def gh(*args):
        if args[0]=='run':
            folder=Path(args[args.index('--dir')+1]);evidence(folder);return ''
        path=args[1]
        if '/actions/artifacts?' in path:return json.dumps({'artifacts':[{
            'id':1,'created_at':datetime.now(timezone.utc).isoformat(),'expired':False,
            'workflow_run':{'id':7,'head_sha':'old'}}]})
        if '/git/trees/' in path:return json.dumps({'tree':[{'path':p,'type':'blob','sha':
            ('b'*40 if p=='linyuan/produce_cn.py' else 'a'*40)} for p in paths]})
        if path.endswith('/jobs?per_page=100'):return json.dumps({'jobs':[{'name':'editorial-check','conclusion':'success'}]})
        return json.dumps({'head_branch':'main','head_sha':'old','head_repository':{'full_name':'owner/repo'},
                           'path':'.github/workflows/fc-production-deploy.yml'})
    monkeypatch.setattr(reuse,'gh',gh)
    reuse.main()
    assert 'resumed=true' in (tmp_path/'out').read_text()
    assert 'reused=true' not in (tmp_path/'out').read_text()
    proof=json.loads((tmp_path/'title-proof-reuse.json').read_text())
    assert proof['approved'] is False and proof['current_checks_required'] and not proof['code_identical']
    assert not (tmp_path/'title-claim-verification.json').exists()
