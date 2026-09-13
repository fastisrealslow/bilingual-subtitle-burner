"""Run 801 lost an already completed source cache and redownloaded it."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import mother_media_cache as m


def fixture(directory, source='source-a'):
    directory.mkdir(exist_ok=True)
    content=b'complete-source-content'
    (directory/'video.mp4').write_bytes(content)
    (directory/'video.mp4.source.json').write_text(json.dumps(dict(
        identity=dict(source=source),sha256=hashlib.sha256(content).hexdigest(),size=len(content))))


def test_only_exact_complete_source_is_restored_without_old_approvals(tmp_path):
    original,target=tmp_path/'artifact',tmp_path/'target'
    fixture(original)
    (original/'source_quality.json').write_text('{"passed":true}')
    assert not m.transfer(original,target,'different-source')
    assert not target.exists()
    assert m.transfer(original,target,'source-a')
    assert {p.name for p in target.iterdir()}==set(m.FILES)
    assert m.verified(target,'source-a')


def test_corrupt_artifact_does_not_replace_partial_download(tmp_path):
    original,target=tmp_path/'artifact',tmp_path/'target'
    fixture(original)
    (original/'video.mp4').write_bytes(b'damaged-source-content!')
    target.mkdir();checkpoint=target/'video.m4s.part';checkpoint.write_bytes(b'resume-me')
    assert not m.transfer(original,target,'source-a')
    assert checkpoint.read_bytes()==b'resume-me'
    assert not (target/'video.mp4').exists()


def test_missing_artifact_service_keeps_resumable_download(monkeypatch,tmp_path):
    partial=tmp_path/'video.m4s.part';partial.write_bytes(b'partial')
    def timeout(*args,**kwargs):raise subprocess.TimeoutExpired('gh',60)
    monkeypatch.setattr(m.subprocess,'run',timeout)
    assert m.restore('source-a',tmp_path,'owner/repo') is False
    assert partial.read_bytes()==b'partial'


def test_evicted_cache_recovers_latest_matching_artifact(monkeypatch,tmp_path):
    artifact=tmp_path/'artifact';fixture(artifact)
    name='mother-media-'+hashlib.sha256(b'source-a').hexdigest()
    def run(command,**kwargs):
        if command[1]=='api':
            return subprocess.CompletedProcess(command,0,stdout=json.dumps({'artifacts':[
                dict(id=3,name=name,expired=True,workflow_run=dict(id=10)),
                dict(id=2,name='unrelated',expired=False,workflow_run=dict(id=11)),
                dict(id=1,name=name,expired=False,workflow_run=dict(id=12))]}))
        assert command[3]=='12'
        import shutil
        for file in m.FILES:shutil.copyfile(artifact/file,Path(command[-1])/file)
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(m.subprocess,'run',run)
    assert m.restore('source-a',tmp_path/'restored','owner/repo')
    assert m.verified(tmp_path/'restored','source-a')


def test_existing_valid_media_does_not_trigger_another_download(monkeypatch,tmp_path):
    fixture(tmp_path)
    def forbidden(*args,**kwargs):raise AssertionError('no network needed')
    monkeypatch.setattr(m.subprocess,'run',forbidden)
    assert m.restore('source-a',tmp_path,'owner/repo')
