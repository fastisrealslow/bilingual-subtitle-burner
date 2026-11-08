"""Cost migration must preserve exact delivery and at-most-once publication."""
import base64
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import urllib.error
import zipfile

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'linyuan/fc'),str(ROOT/'linyuan')]
import index as fc
import upload_transport as transport
import reviewed_upload_client as client
import publish_clock
import publish_on_runner
import fc_cost_policy


def delivery(tmp_path):
    directory=tmp_path/'delivery';directory.mkdir()
    for name,data in {'final.mp4':b'actual-video','cover.jpg':b'actual-cover',
                      'subtitle.ass':b'actual-subtitles'}.items():
        (directory/name).write_bytes(data)
    part=dict(final='final.mp4',cover='cover.jpg',subtitle_files=['subtitle.ass'],
        fingerprints={'sha256':transport.digest(directory/'final.mp4')},
        source_sha256='b'*64,segments=[{'start':10,'end':40}],presentation_version=2)
    submission=dict(task_id='ly-upload-12345678-a',slug='ly-cost-test',part_index=0,
        artifact_id=123,created_at=10000,title='林园：实际原话',source='B站',desc='一段真实讲话',
        tags='林园,投资',copyright=3,tid=207)
    bundle=tmp_path/'bundle.zip'
    manifest=transport.pack(directory,part,submission,bundle)
    event=dict(task_id=submission['task_id'],manifest_sha256=hashlib.sha256(transport.canonical(manifest)).hexdigest(),
        bundle_sha256=transport.digest(bundle),bundle_url=f'https://github.com/{fc.REPO}/releases/download/{transport.TAG}/ly-upload-12345678-a.zip')
    intent=dict(title=submission['title'],slug=submission['slug'],part_index=0,artifact_id=123,
        fingerprints=part['fingerprints'],source_sha256=part['source_sha256'],source_segments=part['segments'])
    entry=dict(slug=submission['slug'],source_url='https://www.bilibili.com/video/BV1iC4y1H7JN',
        uploading=True,upload_transport=deepcopy(event),upload_receipt_intent=intent,
        required_presentation_version=2)
    return directory,part,manifest,bundle,event,entry


def test_pack_contains_only_selected_public_delivery_never_cookies(tmp_path):
    directory,part,manifest,bundle,event,entry=delivery(tmp_path)
    (directory/'cookies.json').write_text('secret')
    transport.pack(directory,part,manifest['submission'],bundle)
    with zipfile.ZipFile(bundle) as z:
        assert set(z.namelist())=={'manifest.json','final.mp4','cover.jpg','subtitle.ass'}
    extracted=tmp_path/'extracted';extracted.mkdir()
    assert transport.unpack(bundle,extracted)==manifest
    assert (extracted/'final.mp4').read_bytes()==b'actual-video'


@pytest.mark.parametrize('attack',['traversal','duplicate','secret','checksum','symlink'])
def test_bundle_tampering_is_rejected_before_upload(tmp_path,attack):
    directory,part,manifest,bundle,event,entry=delivery(tmp_path)
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(bundle) as src,zipfile.ZipFile(bad,'w') as dst:
        for item in src.infolist():
            if attack=='checksum' and item.filename=='manifest.json':continue
            dst.writestr(item,src.read(item.filename))
        if attack=='traversal':dst.writestr('../cookies.json','bad')
        if attack=='duplicate':dst.writestr('final.mp4','bad')
        if attack=='secret':dst.writestr('cookies.json','bad')
        if attack=='checksum':
            manifest['files']['final.mp4']='0'*64
            dst.writestr('manifest.json',transport.canonical(manifest))
        if attack=='symlink':
            item=zipfile.ZipInfo('link');item.external_attr=0o120777<<16;dst.writestr(item,'final.mp4')
    extracted=tmp_path/'extracted';extracted.mkdir()
    with pytest.raises(ValueError):transport.unpack(bad,extracted)
    assert not (tmp_path/'cookies.json').exists()


@pytest.mark.parametrize('field',['title','artifact_id','part_index','source_sha256','fingerprints'])
def test_durable_intent_cannot_be_substituted(tmp_path,field):
    *_,manifest,bundle,event,entry=delivery(tmp_path)
    entry['upload_receipt_intent'][field]='tampered'
    fake=SimpleNamespace(load_state=lambda:dict(dispatched=[entry]),REVIEW_PAUSED_SLUGS=set(),COPYRIGHT=3,TID=207)
    with pytest.raises(ValueError,match='durable'):transport.validate_intent(fake,event,manifest,now=10001)


def test_expired_or_quarantined_intent_cannot_upload_late(tmp_path):
    _,_,manifest,_,event,entry=delivery(tmp_path)
    fake=SimpleNamespace(load_state=lambda:dict(dispatched=[entry]),REVIEW_PAUSED_SLUGS=set(),COPYRIGHT=3,TID=207)
    assert transport.validate_intent(fake,event,manifest,now=10001)==entry
    with pytest.raises(ValueError,match='expired'):transport.validate_intent(fake,event,manifest,now=10900)
    entry['publication_quarantined']=True
    with pytest.raises(ValueError,match='durable'):transport.validate_intent(fake,event,manifest,now=10001)


def test_probe_never_reads_state_or_uploads(monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('Probe must not access remote state')
    for name in ('gh','load_state','flush_logs','log_event','publish_handler'):
        monkeypatch.setattr(fc,name,forbidden)
    assert fc.handler(json.dumps(dict(triggerName=transport.TRIGGER,probe=True)),None)==dict(protocol=1,upload_called=False)
    assert transport.LEGACY_SENTINEL in fc.REVIEW_PAUSED_SLUGS


@pytest.fixture
def worker_case(tmp_path,monkeypatch):
    _,part,manifest,bundle,event,entry=delivery(tmp_path)
    monkeypatch.setattr(transport.time,'time',lambda:10001)
    ledger={};calls=[];gates=[]
    def gh(method,path,data=None,**kw):
        if method=='GET':
            if not ledger:raise urllib.error.HTTPError(path,404,'missing',{},None)
            return dict(content=base64.b64encode(transport.canonical(ledger)).decode())
        ledger.clear();ledger.update(json.loads(base64.b64decode(data['content'])))
        return dict(content=dict(sha='claim-sha'))
    fake=SimpleNamespace(REPO=fc.REPO,gh=gh,load_state=lambda:dict(dispatched=[entry]),
        REVIEW_PAUSED_SLUGS=set(),COPYRIGHT=3,TID=207,COOKIES_JSON='{"cookies":[]}',
        mp4_duration=lambda _:30,
        editorial=SimpleNamespace(metadata_error=lambda *a:gates.append('metadata')),
        artifact_quality_error=lambda *a:gates.append('quality'),
        artifact_subtitle_error=lambda *a:gates.append('subtitles'),
        artifact_cover_error=lambda *a:gates.append('cover'))
    class Response:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def raise_for_status(self):pass
        def iter_content(self,*a):yield bundle.read_bytes()
    import requests
    monkeypatch.setattr(requests,'get',lambda *a,**kw:Response())
    monkeypatch.setitem(sys.modules,'biliup',SimpleNamespace())
    def upload(cmd,**kw):
        calls.append(cmd)
        assert Path(cmd[cmd.index('-u')+1]).stat().st_mode & 0o777==0o600
        return subprocess.CompletedProcess(cmd,0,'BV1Xa4j6zEVQ','')
    monkeypatch.setattr(transport.subprocess,'run',upload)
    return fake,event,entry,ledger,calls,gates


def test_exact_domestic_upload_runs_all_gates_and_only_once(worker_case):
    fake,event,entry,ledger,calls,gates=worker_case
    result=transport.worker(fake,event)
    assert result['bvid']=='BV1Xa4j6zEVQ' and result['upload_called'] is True
    assert gates==['metadata','quality','subtitles','cover']
    assert len(calls)==1 and calls[0][-2:]!=['--dtime','0']
    assert transport.worker(fake,event)['bvid']==result['bvid']
    assert len(calls)==1


def test_claimed_unknown_attempt_never_reenters_uploader(worker_case):
    fake,event,entry,ledger,calls,gates=worker_case
    ledger.update(task_id=event['task_id'],claimed_at=10000)
    with pytest.raises(transport.RemoteUploadPending):transport.worker(fake,event)
    assert not calls


def test_real_quality_failure_stops_before_upload(worker_case):
    fake,event,entry,ledger,calls,gates=worker_case
    fake.artifact_subtitle_error=lambda *a:'proof mismatch'
    result=transport.worker(fake,event)
    assert result['upload_called'] is False and result['error_type']=='ValueError'
    assert not calls


def test_intent_rechecked_after_slow_preparation(worker_case):
    fake,event,entry,ledger,calls,gates=worker_case
    def revoke(*a):entry['publication_quarantined']=True
    fake.artifact_cover_error=revoke
    result=transport.worker(fake,event)
    assert result['upload_called'] is False and not calls


def test_remote_timeout_does_not_fabricate_completion(worker_case,monkeypatch):
    fake,event,entry,ledger,calls,gates=worker_case
    def timeout(*a,**kw):raise subprocess.TimeoutExpired('upload',1620)
    monkeypatch.setattr(transport.subprocess,'run',timeout)
    result=transport.worker(fake,event)
    assert result['upload_called'] is True and not result.get('bvid')
    assert 'upload_process_result' not in entry and entry['uploading'] is True


def test_clock_dispatch_is_fixed_and_does_not_publish():
    calls=[]
    def gh(method,path,data=None,**kw):
        calls.append((method,path,data))
        return {'workflow_runs':[]} if method=='GET' else None
    result=publish_clock.request(gh)
    assert result['published']==0 and calls[-1][2]=={'ref':'main','inputs':{'preview':'false'}}
    assert calls[-1][1]=='/actions/workflows/linyuan-publish-runner.yml/dispatches'


def test_busy_clock_does_not_start_duplicate_job():
    def gh(method,path,**kw):
        assert method=='GET'
        return dict(workflow_runs=[dict(path='.github/workflows/'+publish_clock.WORKFLOW,
            head_branch='main',head_repository={'full_name':fc.REPO},status='in_progress')])
    assert publish_clock.request(gh)['reason']=='publisher_already_active'


def test_runner_preview_never_invokes_fc_or_writes_intent(monkeypatch):
    monkeypatch.setattr(fc,'load_state',lambda:{})
    monkeypatch.setattr(fc,'catchup_deficit',lambda _:1)
    monkeypatch.setattr(fc,'source_inventory',lambda _:{'verified':2})
    monkeypatch.setattr(publish_on_runner,'capability',lambda:pytest.fail('preview invoked FC'))
    assert publish_on_runner.run(preview=True)==dict(preview=True,due=1,inventory={'verified':2},published=0)


def test_rollout_stays_disabled_until_live_acceptance():
    assert fc_cost_policy.RUNNER_PUBLISH_ENABLED is False
    assert fc.MAX_PUBLISH_PER_DAY==4 and sorted(fc.PUBLISH_HOURS)==[10,14,16,21]
    assert fc_cost_policy.estimate_compute(100)==pytest.approx(65.0075)
