import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('fc_delivery_releases',
    Path(__file__).resolve().parents[1]/'linyuan/fc/index.py')
fc=importlib.util.module_from_spec(spec);spec.loader.exec_module(fc)


def test_full_shared_release_does_not_block_new_batch(monkeypatch):
    name='ly-new.final_1.mp4'
    asset=dict(name=name,size=123,browser_download_url='https://example.com/new.mp4')
    monkeypatch.setattr(fc,'_delivery_release_assets',{f'old-{n}':{} for n in range(1000)})
    monkeypatch.setattr(fc,'_batch_delivery_release_assets',{})
    calls=[]
    def gh(method,path):
        calls.append(path)
        assert path=='/releases/tags/deliver-ly-new'
        return {'assets':[asset]}
    monkeypatch.setattr(fc,'gh',gh)
    assert fc.delivery_release_asset(name)==asset
    assert fc.delivery_release_asset(name)==asset
    assert len(calls)==1


def test_old_batches_remain_readable_from_shared_release(monkeypatch):
    name='ly-old.final.mp4';asset=dict(name=name,size=321)
    monkeypatch.setattr(fc,'_delivery_release_assets',None)
    monkeypatch.setattr(fc,'_batch_delivery_release_assets',{})
    def gh(method,path):
        if path=='/releases/tags/deliver-ly-old':raise RuntimeError('404')
        assert path=='/releases/tags/deliver'
        return {'assets':[asset]}
    monkeypatch.setattr(fc,'gh',gh)
    assert fc.delivery_release_asset(name)==asset
