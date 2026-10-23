import copy
import json
from pathlib import Path
import sys
import zipfile
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan/fc'))
import index as F


@pytest.fixture
def case(monkeypatch):
    sources=json.loads((ROOT/'tests/fixtures/linyuan_scene_cover_shot_text.json').read_text())['recovery_sources']
    source=copy.deepcopy(sources['clean_native']['source']);reads=[];writes=[]
    candidate=dict(slug='mother',failed=True,source_quality_rejected=True,ts=1,
        editorial_profile='field-story-v1',source_url='https://www.bilibili.com/video/BVtest',
        source_check_attempts=2,quality_retries=1,
        last_error='封面生成/人物/角标复检失败：现场封面原像素取景未通过：'
        '现场封面匹配人脸原始像素不足96；原始像素中没有避开来源文字且保留完整人物的清晰现场封面',
        production_failure_summary=dict(slug='mother',run_id=123,artifact_id=456,accepted=0))
    state=dict(dispatched=[candidate],published={},daily_publish=dict(count=3))
    run=dict(id=123,conclusion='failure',head_sha='old',updated_at='2026-10-23T10:43:10Z')
    patch=['+def source_frame_edge_text_exclusions(evidence,frame_index):']
    def gh(method,path):
        assert method=='GET'
        if path=='/compare/old...main':return dict(files=[dict(filename='linyuan/produce_cn.py',patch=patch[0])])
        assert path=='/actions/runs/123/artifacts'
        reads.append(path)
        return dict(artifacts=[dict(id=789,name='editorial-mother',size_in_bytes=20000,
            expired=False,workflow_run=dict(id=123))])
    def download(artifact,path,**kwargs):
        assert artifact==789 and kwargs['attempts']==1 and kwargs['max_bytes']==2_000_000
        with zipfile.ZipFile(path,'w') as z:z.writestr('_tmp/source_quality.json',json.dumps(source))
    monkeypatch.setattr(F,'gh',gh);monkeypatch.setattr(F,'download_reviewed_zip',download)
    monkeypatch.setattr(F,'save_state',lambda s:writes.append(copy.deepcopy(s)))
    monkeypatch.setattr(F,'log_event',lambda *a:None)
    return state,candidate,run,source,reads,writes,patch,sources


def test_real_clean_native_source_recovers_once_with_original_lifetime_history(case):
    state,candidate,run,source,reads,writes,patch,sources=case
    assert F._recover_changed_production_rule(state,candidate,run)
    assert candidate['source_check_attempts']==2 and candidate['quality_retries']==1
    assert state['daily_publish']['count']==3
    assert candidate['automatic_rule_recoveries']['scene-cover-frame-text-v1']['source_context_artifact_id']==789
    assert len(reads)==1 and len(writes)==2
    candidate['failed']=True
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert len(reads)==1


def test_actual_tiny_face_audio_card_stays_failed_and_measurement_is_reused(case):
    state,candidate,run,source,reads,writes,patch,sources=case
    source.clear();source.update(sources['tiny_face_card']['source'])
    for _ in range(2):assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and candidate['source_quality_rejected']
    assert not candidate['automatic_rule_recoveries'] and len(reads)==1 and len(writes)==1
    assert candidate['source_check_attempts']==2 and candidate['quality_retries']==1


@pytest.mark.parametrize('change',[
    lambda c:c.update(editorial_profile='baseline'),
    lambda c:c['production_failure_summary'].update(run_id=999),
    lambda c:c['production_failure_summary'].update(accepted=False),
    lambda c:c.update(last_error='封面生成/人物/角标复检失败：现场封面原像素取景未通过：现场封面匹配人脸原始像素不足96'),
])
def test_unrelated_identity_or_unbound_failure_does_not_fetch_or_retry(case,change):
    state,candidate,run,source,reads,writes,patch,sources=case;change(candidate)
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and not reads and not writes


def test_unrelated_producer_touch_is_not_a_crop_repair(case):
    state,candidate,run,source,reads,writes,patch,sources=case
    patch[0]='+ title=copywrite(cues)'
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert not reads and not writes


@pytest.mark.parametrize('bad',[
    dict(passed=False),dict(source_sha256='bad'),dict(duration_sec=1199),
    dict(clean_filter_verified=False),dict(clean_strategy='audio_card'),
    dict(clean_output_resolution=dict(width=854,height=480)),
])
def test_invalid_or_impossible_measured_scene_never_reopens(case,bad):
    state,candidate,run,source,reads,writes,patch,sources=case;source.update(bad)
    assert not F._recover_changed_production_rule(state,candidate,run)
    assert candidate['failed'] and not candidate['automatic_rule_recoveries']
    assert candidate['source_check_attempts']==2
