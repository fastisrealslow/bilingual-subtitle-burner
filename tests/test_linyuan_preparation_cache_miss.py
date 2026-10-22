"""The old installer-only debug ZIP must not prevent fresh source ASR."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import restore_production_evidence as R


@pytest.fixture
def case(tmp_path,monkeypatch):
    old=tmp_path/'old';old.mkdir()
    work=tmp_path/'work';work.mkdir()
    (work/'source_quality.json').write_text(json.dumps(dict(passed=True,source_sha256='a'*64)))
    (old/'batch_report.json').write_text(json.dumps(dict(accepted=0,retryable=True)))
    artifacts=[dict(name='debug-fresh',expired=False),dict(name='production-reject-fresh',expired=False)]
    steps=[dict(name='安装 ffmpeg 与中文字体',conclusion='failure')]+[
        dict(name=name,conclusion='skipped') for name in ('取源','素材质量门禁','出片')]
    jobs=[dict(conclusion='failure',steps=steps)]
    calls=[]
    def api(command):
        calls.append(command)
        assert command==['gh','api','repos/owner/repo/actions/runs/123/jobs']
        return json.dumps(dict(jobs=jobs)).encode()
    monkeypatch.setattr(R.subprocess,'check_output',api)
    return old,work,artifacts,steps,jobs,calls


def test_verified_install_only_empty_zip_is_cache_miss_and_changes_nothing(case):
    old,work,artifacts,steps,jobs,calls=case
    before={p.name:p.read_bytes() for p in work.iterdir()}
    assert R.preparation_only_cache_miss(old,work,123,artifacts,'owner/repo')
    assert before=={p.name:p.read_bytes() for p in work.iterdir()}
    assert len(calls)==1


@pytest.mark.parametrize('change',['no_jobs','multiple_jobs','wrong_failure','source_success',
    'missing_source_step','second_failure','success_job','unknown_step_status'])
def test_missing_or_non_install_failure_cannot_be_mistaken_for_empty_preparation(case,change):
    old,work,artifacts,steps,jobs,calls=case
    if change=='no_jobs':jobs.clear()
    if change=='multiple_jobs':jobs.append(dict(conclusion='failure',steps=steps))
    if change=='wrong_failure':steps[0]['name']='出片'
    if change=='source_success':steps[1]['conclusion']='success'
    if change=='missing_source_step':steps.pop(1)
    if change=='second_failure':steps.append(dict(name='下载模型',conclusion='failure'))
    if change=='success_job':jobs[0]['conclusion']='success'
    if change=='unknown_step_status':steps[1]['conclusion']=None
    assert not R.preparation_only_cache_miss(old,work,123,artifacts,'owner/repo')


@pytest.mark.parametrize('name',['source_quality.json','asr_raw_chunks.json','cues_raw.json',
    'copywrite.json','highlights_block_1.json','semantic-1.json','recognition.json',
    'aligned.json','meta.json','video.mp4'])
def test_any_unbound_source_or_transcript_data_remains_fail_closed(case,name):
    old,work,artifacts,steps,jobs,calls=case
    sub=old/'nested';sub.mkdir();(sub/name).write_text('{}')
    assert not R.preparation_only_cache_miss(old,work,123,artifacts,'owner/repo')
    assert not calls


@pytest.mark.parametrize('prefix',['deliver-','source-reject-','mother-media-','raw-asr-',
                                  'editorial-','render-evidence-'])
def test_source_artifact_inventory_disallows_empty_cache_shortcut(case,prefix):
    old,work,artifacts,steps,jobs,calls=case
    artifacts.append(dict(name=prefix+'fresh',expired=False))
    assert not R.preparation_only_cache_miss(old,work,123,artifacts,'owner/repo')
    assert not calls


@pytest.mark.parametrize('current',[dict(passed=False,source_sha256='a'*64),
    dict(passed=True),dict(passed=True,source_sha256='not-a-sha'),dict(passed=True,source_sha256=[])] )
def test_new_source_must_really_have_passed_hash_bound_gate(case,current):
    old,work,artifacts,steps,jobs,calls=case
    (work/'source_quality.json').write_text(json.dumps(current))
    with pytest.raises(ValueError,match='本次母片尚未'):
        R.preparation_only_cache_miss(old,work,123,artifacts,'owner/repo')
    assert not calls


def test_main_recovers_empty_setup_archive_without_importing_approvals(case,monkeypatch):
    old,work,artifacts,steps,jobs,calls=case
    monkeypatch.setattr(sys,'argv',['restore','--run-id','123','--work',str(work)])
    def api(command):
        if command[-1].endswith('/artifacts'):return json.dumps(dict(artifacts=artifacts)).encode()
        assert command[-1].endswith('/jobs');return json.dumps(dict(jobs=jobs)).encode()
    monkeypatch.setattr(R.subprocess,'check_output',api)
    def download(command,**kwargs):
        target=Path(command[command.index('--dir')+1]);(target/'batch_report.json').write_text('{}')
    monkeypatch.setattr(R.subprocess,'run',download)
    monkeypatch.setattr(R,'restore',lambda *a,**k:pytest.fail('no source evidence to restore'))
    before=(work/'source_quality.json').read_bytes()
    assert R.main() is None
    assert [p.name for p in work.iterdir()]==['source_quality.json']
    assert (work/'source_quality.json').read_bytes()==before
