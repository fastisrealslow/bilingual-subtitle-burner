"""Mandatory feed assets survive acceptance; incident repair is source-bound."""
import hashlib
import json
import sys
from pathlib import Path

from PIL import Image
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'linyuan'))
from batch_delivery import archive_accepted
import repair_feed_delivery as R


def package(tmp_path):
    meta = dict(slug=R.SLUG,final='final_1.mp4',cover='cover_1.jpg',
        preview_30s='preview_30s_1.mp4',contact_sheet_6='contact_sheet_6_1.jpg',
        cover_proof=dict(version=4,feed_safe_crop=[280,0,1000,720],feed_square='cover_1_feed_square.jpg'))
    for k in ('final','preview_30s','contact_sheet_6'):
        (tmp_path / meta[k]).write_bytes(b'accepted')
    Image.new('RGB',(1280,720),'gray').save(tmp_path / meta['cover'])
    (tmp_path / 'meta.json').write_text(json.dumps(meta))
    return meta


def test_feed_square_is_in_both_delivery_names(tmp_path):
    meta=package(tmp_path)
    (tmp_path / meta['cover_proof']['feed_square']).write_bytes(b'square')
    (tmp_path / 'rejected_feed_square.jpg').write_bytes(b'bad')
    archive_accepted(tmp_path,R.SLUG)
    for name in (meta['cover_proof']['feed_square'],R.SLUG+'.'+meta['cover_proof']['feed_square']):
        assert (tmp_path / '_accepted' / name).read_bytes()==b'square'
    assert not (tmp_path / '_accepted' / 'rejected_feed_square.jpg').exists()


@pytest.mark.parametrize('bad',[None,'missing.jpg','../escape.jpg','nested/file.jpg','wrong.png'])
def test_missing_or_unsafe_feed_fails_before_replacing_previous_delivery(tmp_path,bad):
    meta=package(tmp_path)
    meta['cover_proof']['feed_square']=bad
    (tmp_path / 'meta.json').write_text(json.dumps(meta))
    (tmp_path / '_accepted').mkdir()
    (tmp_path / '_accepted' / 'previous.mp4').write_bytes(b'previous')
    with pytest.raises(ValueError,match='feed square'):
        archive_accepted(tmp_path,R.SLUG)
    assert (tmp_path / '_accepted' / 'previous.mp4').read_bytes()==b'previous'


def test_recovery_runs_full_validation_without_rewriting_media_or_proof(tmp_path,monkeypatch):
    package(tmp_path)
    monkeypatch.setattr(R,'MEDIA_SHA256',hashlib.sha256((tmp_path/'final_1.mp4').read_bytes()).hexdigest())
    monkeypatch.setattr(R,'COVER_SHA256',hashlib.sha256((tmp_path/'cover_1.jpg').read_bytes()).hexdigest())
    original=(tmp_path/'meta.json').read_bytes()
    calls=[]
    def validate(meta,directory):
        calls.append(meta)
        return None if (directory/'cover_1_feed_square.jpg').exists() else '信息流方形封面验收件缺失'
    result=R.repair(tmp_path,validate)
    assert len(calls)==2 and result['full_validation_passed']
    assert (tmp_path/'meta.json').read_bytes()==original
    assert Image.open(tmp_path/'cover_1_feed_square.jpg').size==(360,360)
    assert (tmp_path/'_accepted'/'cover_1_feed_square.jpg').exists()


def test_recovery_rejects_unrelated_or_changed_source(tmp_path):
    package(tmp_path)
    with pytest.raises(ValueError,match='fingerprint mismatch'):
        R.repair(tmp_path,lambda *args:None)
    assert not (tmp_path/'cover_1_feed_square.jpg').exists()


def test_recovery_does_not_waive_a_new_gate_failure(tmp_path,monkeypatch):
    package(tmp_path)
    monkeypatch.setattr(R,'MEDIA_SHA256',hashlib.sha256((tmp_path/'final_1.mp4').read_bytes()).hexdigest())
    monkeypatch.setattr(R,'COVER_SHA256',hashlib.sha256((tmp_path/'cover_1.jpg').read_bytes()).hexdigest())
    with pytest.raises(ValueError,match='only accepts'):
        R.repair(tmp_path,lambda *args:'字幕有识别疑点')
    assert not (tmp_path/'_accepted').exists()
