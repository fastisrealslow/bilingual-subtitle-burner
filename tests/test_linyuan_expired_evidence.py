"""Missing historical cache must not abort a pinned fresh CPU recognition."""
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import prepare_asr_runtime as runtime
from audio_preprocessing import policy


@pytest.fixture
def choice(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GITHUB_REPOSITORY','owner/repo')
    monkeypatch.setenv('GITHUB_ENV',str(tmp_path/'env'))
    return dict(backend='qwen3',source_sha256='a'*64,evidence_run_id=34035892469,
                model_revisions=dict(asr='asr-revision',aligner='aligner-revision'))


def report(choice):
    return dict(source_video_sha256=choice['source_sha256'],device='cpu',
        networking_during_inference=False,model_id='Qwen/Qwen3-ASR-0.6B',
        model_revision=choice['model_revisions']['asr'],
        audio_preprocessing=policy(choice['source_sha256']),chunks=[{}],
        alignment=dict(device='cpu',networking_during_inference=False,
            model_id='Qwen/Qwen3-ForcedAligner-0.6B',
            model_revision=choice['model_revisions']['aligner']))


@pytest.mark.parametrize('failure',[subprocess.CalledProcessError(1,['gh']),
    subprocess.TimeoutExpired(['gh'],300),OSError('unavailable')])
def test_missing_or_timed_out_partial_download_is_not_exposed(choice,monkeypatch,failure,capsys):
    def download(cmd,**kwargs):
        target=Path(cmd[cmd.index('--dir')+1]);target.mkdir(parents=True)
        (target/'aligned.json').write_text(json.dumps(report(choice)))
        raise failure
    monkeypatch.setattr(runtime.subprocess,'run',download)
    assert runtime.immutable_run_evidence(choice) is None
    assert not Path('env').exists()
    assert 'unavailable' not in capsys.readouterr().out


def test_successful_empty_download_is_still_a_cache_miss(choice,monkeypatch):
    monkeypatch.setattr(runtime.subprocess,'run',lambda *a,**kw: None)
    assert runtime.immutable_run_evidence(choice) is None


@pytest.mark.parametrize('wrong',['source','model','context','alignment'])
def test_present_wrong_evidence_still_fails_closed(choice,monkeypatch,wrong):
    def download(cmd,**kwargs):
        target=Path(cmd[cmd.index('--dir')+1]);target.mkdir(parents=True)
        item=report(choice)
        if wrong=='source':item['source_video_sha256']='b'*64
        if wrong=='model':item['model_revision']='different'
        if wrong=='context':item['recognition_context']={'profile':'different'}
        if wrong=='alignment':item['alignment']['device']='cuda'
        (target/'aligned.json').write_text(json.dumps(item))
    monkeypatch.setattr(runtime.subprocess,'run',download)
    with pytest.raises(ValueError,match='incompatible'):
        runtime.immutable_run_evidence(choice)


def test_valid_present_evidence_still_requires_actual_pcm_validation(choice,monkeypatch):
    def download(cmd,**kwargs):
        assert kwargs['capture_output'] and kwargs['check']
        target=Path(cmd[cmd.index('--dir')+1]);target.mkdir(parents=True)
        (target/'aligned.json').write_text(json.dumps(report(choice)))
    monkeypatch.setattr(runtime.subprocess,'run',download)
    directory=runtime.immutable_run_evidence(choice)
    from qwen_asr_evidence import load_reports,validated_words
    with pytest.raises(ValueError,match='provenance'):
        validated_words(load_reports(directory),'actual-pcm',choice['source_sha256'],36)


def test_prepare_missing_cache_keeps_pinned_model_directories_and_no_evidence_env(choice,monkeypatch):
    monkeypatch.setattr(runtime,'configuration',lambda _: choice)
    def download(cmd,**kw):raise subprocess.CalledProcessError(1,cmd)
    monkeypatch.setattr(runtime.subprocess,'run',download)
    models=[]
    def snapshot(model,revision):
        models.append((model,revision));return '/pinned/'+revision
    monkeypatch.setitem(sys.modules,'huggingface_hub',types.SimpleNamespace(snapshot_download=snapshot))
    monkeypatch.setattr(sys,'argv',['prepare_asr_runtime.py','prepare','--source-report','source.json'])
    runtime.main()
    env=Path('env').read_text()
    assert models==[('Qwen/Qwen3-ASR-0.6B','asr-revision'),
                    ('Qwen/Qwen3-ForcedAligner-0.6B','aligner-revision')]
    assert 'QWEN3_ASR_DIR=/pinned/asr-revision' in env
    assert 'QWEN3_ALIGNER_DIR=/pinned/aligner-revision' in env
    assert 'QWEN3_EVIDENCE_DIR' not in env and 'ASR_EVIDENCE_READY=true' not in env


@pytest.mark.parametrize('run',[True,0,-1,'34035892469'])
def test_invalid_immutable_run_is_not_swallowed(choice,run):
    with pytest.raises(ValueError,match='Invalid immutable'):
        runtime.immutable_run_evidence({**choice,'evidence_run_id':run})
