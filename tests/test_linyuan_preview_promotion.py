import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('promote_linyuan_preview',ROOT/'scripts/promote_linyuan_preview.py')
p=importlib.util.module_from_spec(spec);spec.loader.exec_module(p)
import editorial_policy


def records():
    request=dict(run_id=123,artifact_id=456,commit='a'*40,sha256=hashlib.sha256(b'actual video').hexdigest())
    run=dict(id=123,head_sha='a'*40,head_branch='codex/automatic-preview',
        head_repository=dict(full_name='owner/repo'),conclusion='success',
        path='.github/workflows/linyuan-automatic-preview.yml')
    artifact=dict(id=456,name='preview-deliver-preview-123',expired=False,
        workflow_run=dict(id=123),size_in_bytes=100)
    return request,run,artifact


@pytest.mark.parametrize('field,value',[('head_sha','b'*40),('conclusion','failure'),
    ('head_branch','main'),('path','.github/workflows/other.yml')])
def test_changed_or_failed_producer_is_not_promoted(field,value):
    request,run,artifact=records();run[field]=value
    with pytest.raises(ValueError):p.validate_origin(request,run,artifact,'owner/repo')


def test_request_cannot_supply_title_or_change_source():
    request,run,artifact=records()
    assert p.validate_origin(p.request_fields(request),run,artifact,'owner/repo')=='preview-123'
    for key in ('title','source_url','skip_checks'):
        with pytest.raises(ValueError):p.request_fields({**request,key:'override'})


def test_promotion_requires_real_review_hash_and_final_validator(tmp_path):
    request,_,_=records();(tmp_path/'final.mp4').write_bytes(b'actual video')
    review=dict(version=editorial_policy.VERSION,automatic_only=True,review_protocol=2,
        standalone_opening=True,complete_argument=True,reasoning_present=True,
        natural_ending=True,requires_audio_review=False,transcript_sha256='source',summary='实际观点与理由')
    row=dict(slug='preview-123',automatic_only=True,final='final.mp4',fingerprints=dict(sha256=request['sha256']),
        editorial_review=review,title_rewrite=dict(review=dict(method='cpu_text_review')))
    def save(data):(tmp_path/'meta.json').write_text(json.dumps(data))
    save(row);calls=[]
    assert p.verify_directory(tmp_path,request,'preview-123',lambda *a:calls.append(a) or None)==row
    assert len(calls)==1
    with pytest.raises(ValueError,match='subtitle mismatch'):
        p.verify_directory(tmp_path,request,'preview-123',lambda *a:'subtitle mismatch')
    for changed in [{**row,'automatic_only':False},{**row,'reviewed_title_record':{'manual':True}},
                    {**row,'editorial_review':{**review,'status':'skipped','review_protocol':'model-review-disabled-v1'}}]:
        save(changed)
        with pytest.raises(ValueError):p.verify_directory(tmp_path,request,'preview-123',lambda *a:None)
    save(row);(tmp_path/'final.mp4').write_bytes(b'changed')
    with pytest.raises(ValueError,match='bytes'):
        p.verify_directory(tmp_path,request,'preview-123',lambda *a:None)
