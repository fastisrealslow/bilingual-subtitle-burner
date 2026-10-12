import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import collection_registry as r


def test_atomic_local_round_trip_and_corruption_fails_closed(tmp_path,monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    store=r.Store(tmp_path/'registry.json')
    value=store.load();value['collections']['key']=dict(status='create_requested')
    store.save(value);assert r.Store(store.path).load()==value
    store.path.write_text('{broken')
    with pytest.raises(r.RegistryError,match='unreadable'):store.load()


def test_wrong_owner_cannot_become_an_empty_registry(tmp_path,monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS',raising=False)
    store=r.Store(tmp_path/'registry.json')
    store.path.write_text(json.dumps({**r.empty_registry(),'owner_mid':42}))
    with pytest.raises(r.RegistryError,match='owner_mismatch'):store.load()


def test_ci_put_is_fixed_ledger_only_and_uses_exact_file_lease(tmp_path,monkeypatch):
    import base64, hashlib
    monkeypatch.setenv('GITHUB_ACTIONS','true');monkeypatch.setenv('GITHUB_REPOSITORY',r.REPO)
    calls=[]
    def command(args,**options):
        calls.append((args,options))
        if '--method' not in args:
            return SimpleNamespace(returncode=0,stdout=json.dumps(dict(sha='oldblob',type='file',encoding='base64',
                content=base64.b64encode(json.dumps(r.empty_registry()).encode()).decode())),stderr='')
        payload=json.loads(options['input'])
        assert payload['sha']=='oldblob' and payload['branch']=='main'
        assert args[args.index('PUT')+1]==f'repos/{r.REPO}/contents/{r.KEY}'
        raw=base64.b64decode(payload['content'])
        sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        return SimpleNamespace(returncode=0,stdout=json.dumps({'content':{'sha':sha}}),stderr='')
    monkeypatch.setattr(r.subprocess,'run',command)
    store=r.Store(tmp_path/'registry.json');value=store.load();store.save(value)
    assert len(store.sha)==40 and len(calls)==2
    store.save(value)
    assert len(calls)==2, 'Identical verified ledger must not create repeated commits'


@pytest.mark.parametrize('stderr,allowed', [('not authorized (HTTP 401)',False),
    ('missing (HTTP 404)',True),('timed out',False)])
def test_only_confirmed_missing_ledger_can_initialize(tmp_path,monkeypatch,stderr,allowed):
    monkeypatch.setenv('GITHUB_ACTIONS','true');monkeypatch.setenv('GITHUB_REPOSITORY',r.REPO)
    monkeypatch.setattr(r.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stderr=stderr,stdout=''))
    store=r.Store(tmp_path/'registry.json')
    if allowed:assert store.load()==r.empty_registry()
    else:
        with pytest.raises(r.RegistryError,match='read_failed'):store.load()
