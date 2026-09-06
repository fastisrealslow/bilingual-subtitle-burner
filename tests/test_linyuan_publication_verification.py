"""A public upload must still match the reviewed long video's duration."""
import importlib.util
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan/fc'))
sys.path.insert(0,str(ROOT/'linyuan'))
spec=importlib.util.spec_from_file_location('long_publication',ROOT/'linyuan/fc/publish_fresh_six.py')
worker=importlib.util.module_from_spec(spec);spec.loader.exec_module(worker)


def test_platform_short_or_missing_duration_cannot_complete_long_six():
    assert not worker.duration_matches_review(30,135.621333)
    assert not worker.duration_matches_review(None,135.621333)
    assert not worker.duration_matches_review(165,None)
    assert not worker.duration_matches_review(136000,135.621333)
    assert worker.duration_matches_review(136,135.621333)


def test_public_short_receipt_remains_unverified(monkeypatch):
    import io,json
    class Opener:
        def open(self,*args,**kwargs):
            return io.BytesIO(json.dumps({'code':0,'data':{'state':0,'bvid':'BVexact',
                'duration':30,'owner':{'mid':worker.fc.OWNER_MID}}}).encode())
    monkeypatch.setattr(worker,'bilibili_opener',lambda:Opener())
    monkeypatch.delenv('BILIBILI_COOKIES',raising=False)
    monkeypatch.setattr(worker.fc,'FRESH_SIX_APPROVED',{'reviewed':{'duration_sec':135.621333}})
    result=worker.runner_publication_status({'reviewed':{'bvid':'BVexact','title':'long argument'}})
    assert result['public_count']==1
    assert result['verified_count']==0
