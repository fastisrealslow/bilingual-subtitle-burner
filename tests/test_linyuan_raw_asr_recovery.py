import io
import json
from pathlib import Path
import sys
import zipfile
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import recover_mother_asr as recovery


def report(source='mother',complete=True):
    return dict(source_video_sha256=source,source_pcm_sha256='pcm',duration=2,
        device='cpu',networking_during_inference=False,model_id='Qwen/Qwen3-ASR-0.6B',model_revision='asr',
        alignment=dict(device='cpu',networking_during_inference=False,
            model_id='Qwen/Qwen3-ForcedAligner-0.6B',model_revision='align'),
        chunks=[dict(core_start=0,core_end=2 if complete else 1,text='原话',
            words=[dict(start=0,end=1,text='原话')])])


def archive(row):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('_tmp/asr_raw_chunks.json',json.dumps([row]))
        z.writestr('_tmp/editorial_review.json','{"approved":true}')
        z.writestr('_tmp/copywrite.json','{"title":"manual answer"}')
    return buffer.getvalue()


@pytest.mark.parametrize('first',[report('other'),report(complete=False)])
def test_recovery_skips_foreign_or_partial_alignment_and_never_imports_copy(first,tmp_path,monkeypatch):
    monkeypatch.setenv('GITHUB_REPOSITORY','owner/repo')
    monkeypatch.setattr(recovery,'candidates',lambda key:iter([
        dict(id=1,size_in_bytes=100),dict(id=2,size_in_bytes=100)]))
    records={1:archive(first),2:archive(report())}
    def download(args,**kwargs):kwargs['stdout'].write(records[int(args[-1].split('/')[-2])])
    monkeypatch.setattr(recovery.subprocess,'run',download)
    result=recovery.restore(dict(source_sha256='mother',model_revisions=dict(asr='asr',aligner='align')),tmp_path,'key')
    assert result==tmp_path/'recovered_raw_asr/2'
    assert [p.name for p in result.rglob('*') if p.is_file()]==['aligned.json']
    assert not list(tmp_path.rglob('copywrite.json'))
    assert not list(tmp_path.rglob('editorial_review.json'))
    assert json.loads((tmp_path/'raw_asr_recovery.json').read_text())['editorial_answers_imported'] is False


def test_unavailable_artifact_service_falls_back_without_approval_or_partial_files(tmp_path,monkeypatch):
    def missing(_):raise OSError('network unavailable')
    monkeypatch.setattr(recovery,'candidates',missing)
    assert recovery.restore(dict(source_sha256='mother'),tmp_path,'key') is None
    assert not list(tmp_path.iterdir())
