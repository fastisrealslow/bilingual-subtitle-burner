import json
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_research as research


def test_retry_is_persistent_and_success_does_not_redownload():
    job={}
    research.failed(job,TimeoutError(),1000)
    restored=json.loads(json.dumps(job))
    assert not research.due(restored,1001)
    assert research.due(restored,22600)
    restored['status']='inspected'
    assert not research.due(restored,10**10)


def test_contiguous_audio_match_is_candidate_not_original_proof():
    rng=random.Random(14)
    source=[rng.getrandbits(32) for _ in range(600)]
    ref=source[99:299]
    found=research.audio_candidate(ref,source)
    assert found and found['mother_fingerprint_offset']==99
    assert found['status']=='audio_match_candidate'
    assert found['original_publisher_confirmed'] is False
    assert research.audio_candidate([rng.getrandbits(32) for _ in range(200)],source) is None
    assert research.audio_candidate([123]*200,source) is None


def test_timeout_kills_the_spawned_process_group(monkeypatch):
    import subprocess
    calls=[]
    class Process:
        pid=42
        def communicate(self,timeout=None):
            if timeout is not None:raise subprocess.TimeoutExpired('download',timeout)
            return b'',b''
    monkeypatch.setattr(research.subprocess,'Popen',lambda *a,**kw:Process())
    monkeypatch.setattr(research.os,'killpg',lambda *args:calls.append(args))
    import pytest
    with pytest.raises(subprocess.TimeoutExpired):research.bounded_run(['download'],1)
    assert calls==[(42,research.signal.SIGKILL)]
