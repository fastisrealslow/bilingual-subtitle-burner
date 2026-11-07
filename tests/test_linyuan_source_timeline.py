from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import source_timeline as T
import editorial_policy as E
import produce_cn as P
import production_diagnostics as D
import verified_story_title_recovery as R
from test_linyuan_documented_company_recovery import proof as cpu_proof

PROOF=json.loads(T.REGISTRY.read_text())['proofs'][0]
OLD,NEW=PROOF['video_sha256']
URL='https://www.bilibili.com/video/BV1iC4y1H7JN'


def raw(sha):
    return dict(version=1,source_video_sha256=sha,source_pcm_sha256='c'*64,
        audio_preprocessing='ffmpeg-source-clock-v2',duration=60,core_range=[0,60],
        model_revision='asr',alignment=dict(model_revision='aligner',elapsed_seconds=10),
        elapsed_seconds=20,peak_rss_mb=100,chunks=[dict(core_start=0,core_end=30,
            words=[dict(text='原话',start=1,end=2)]),dict(core_start=30,core_end=60,words=[])])


def verify(a,b):return T.verify(a,b,source_key='source:p1',old_run=1,old_artifact=2,new_run=3,new_artifact=4)


def test_exact_full_pcm_and_every_word_clock_may_ignore_only_runtime_telemetry():
    a=raw('a'*64);b=raw('b'*64);b['elapsed_seconds']=100;b['alignment']['elapsed_seconds']=200
    before=deepcopy((a,b));p=verify(a,b)
    assert T.valid(p) and p['visual_approved'] is p['media_approved'] is False and (a,b)==before


@pytest.mark.parametrize('change',['pcm','word','clock','duration','gap','model'])
def test_audio_text_clock_model_or_partial_coverage_differences_remain_closed(change):
    a=raw('a'*64);b=raw('b'*64)
    if change=='pcm':b['source_pcm_sha256']='d'*64
    elif change=='word':b['chunks'][0]['words'][0]['text']='改写'
    elif change=='clock':b['chunks'][0]['words'][0]['start']=1.2
    elif change=='duration':b['duration']=61
    elif change=='gap':b['chunks'][1]['core_start']=31
    elif change=='model':b['alignment']['model_revision']='other'
    with pytest.raises(ValueError):verify(a,b)


def history(segments=None):
    return dict(dispatched=[],published=dict(old=dict(source_url=URL,parts=[dict(bvid='actual_receipt',
        source_sha256=OLD,source_segments=segments or [dict(start=978.76,end=1007.8)])])))


def test_proven_time_ranges_remain_unpublished_only_when_they_do_not_overlap():
    m=dict(source_sha256=NEW,segments=[dict(start=1009.48,end=1144.68)])
    assert E.source_reuse_error(m,URL,history()) is None
    assert E.source_version_error(dict(source_sha256=NEW,duration_sec=2700),URL,history()) is None
    m['segments']=[dict(start=990,end=1010)]
    assert '重叠' in E.source_reuse_error(m,URL,history())
    m['segments']=[dict(start=2800,end=2900)]
    assert '哈希已改变' in E.source_reuse_error(m,URL,history())
    m['source_sha256']='f'*64
    assert '哈希已改变' in E.source_reuse_error(m,URL,history())
    assert not T.related('BV1iC4y1H7JN:p2',OLD,NEW,[(0,10)],[(20,30)])


def test_unknown_historical_ranges_are_not_repaired_by_audio_identity():
    state=history();state['published']['old']['parts'][0].pop('source_segments')
    assert '缺少可核对' in E.source_reuse_error(dict(source_sha256=NEW,
        segments=[dict(start=1009,end=1144)]),URL,state)


@pytest.mark.parametrize('known',[False,True])
def test_real_cli_checks_source_version_before_transcription(tmp_path,monkeypatch,known):
    state=tmp_path/'state.json';state.write_text(json.dumps(history()))
    src=tmp_path/'fixture.mp4';src.write_bytes(b'flow-control fixture, not approved media')
    monkeypatch.setenv('PUBLICATION_STATE_PATH',str(state));monkeypatch.setenv('SOURCE_ORIGIN_URL',URL)
    monkeypatch.setattr(P,'BASE',tmp_path);monkeypatch.setattr(P,'TEXT_BACKEND','local')
    monkeypatch.setattr(P,'load_key',lambda:None)
    source=dict(passed=True,source_sha256=NEW if known else 'f'*64,duration_sec=2700,
        resolution=dict(width=1280,height=720),visual_identity={})
    monkeypatch.setattr(P,'run_source_quality_gate',lambda *a:source)
    class ReachedRecognition(RuntimeError):pass
    def transcribe(*a):raise ReachedRecognition
    monkeypatch.setattr(P,'transcribe',transcribe)
    monkeypatch.setattr(sys,'argv',['produce','--source',str(src),'--slug','version-test'])
    if known:
        with pytest.raises(ReachedRecognition):P.main()
    else:
        assert P.main()==2
        r=json.loads((tmp_path/'deliver/version-test/batch_report.json').read_text())
        assert r['failure_stage']=='source-version' and r['retryable'] is False and r['accepted']==0


def test_source_wide_version_conflict_precedes_a_partial_model_timeout():
    rows=[dict(stage='editorial-service',retryable=True,reason='长演讲候选推理尚未完成'),
        dict(stage='part-quality',reason='同一来源URL的母片内容哈希已改变，须核对旧段对应关系')]
    assert D.ordered_failures(rows)[0]==rows[1]
    assert D.failure_category(rows[1]['reason'])=='publication_history'


def failed_state():
    return dict(dispatched=[dict(slug=R.ORIGINAL,source_url=URL,editorial_profile=R.TREATMENT,
        experiment_mother_sha256=OLD,experiment_selection_revision=2026102201),
        dict(slug=R.ASR_CHILD,source_url=URL),dict(slug=R.DOCUMENTARY_CHILD,failed=True,source_url=URL,
            source_check_run_id=42268135807,source_check_attempts=2,production_failure_summary=dict(
                slug=R.DOCUMENTARY_CHILD,accepted=0,run_id=42268135807,
                source_context=dict(passed=True,source_url=URL,source_sha256=NEW,duration_sec=2700),
                failures=[dict(reason='同一来源URL的母片内容哈希已改变，须核对旧段对应关系')]))],published={})


def test_proven_clock_repair_has_one_distinct_normal_child_and_preserves_failed_attempts(monkeypatch):
    monkeypatch.setattr(R.F.experiment,'preparing',lambda:True)
    state=failed_state();before=deepcopy(state);stock=dict(inventory_fresh=True,experiment_ready={})
    child,error=R.plan(state,stock,[],[],documentary_proof=cpu_proof(),timeline_proof=PROOF)
    assert error is None and child['slug']==R.TIMELINE_CHILD and child['source_sha256']==NEW
    assert child['experiment_mother_sha256']==NEW and state==before
    assert child['audio_timeline_recovery_evidence']['media_approved'] is False
    assert not {'selected_parts','segments','quality_retries','source_check_attempts'}&child.keys()
    state['dispatched'].append(child)
    assert R.plan(state,stock,[],[],documentary_proof=cpu_proof(),timeline_proof=PROOF)[1]=='already_attempted_or_unresolved'


def test_unmeasured_version_or_wrong_failed_parent_cannot_borrow_the_new_retry(monkeypatch):
    monkeypatch.setattr(R.F.experiment,'preparing',lambda:True)
    state=failed_state();stock=dict(inventory_fresh=True,experiment_ready={})
    state['dispatched'][-1]['production_failure_summary']['source_context']['source_sha256']='f'*64
    assert R.plan(state,stock,[],[],documentary_proof=cpu_proof(),timeline_proof=PROOF)[1]=='audio_timeline_repair_not_verified'


def test_changed_clock_evidence_invalidates_cached_inventory_approvals(tmp_path,monkeypatch):
    import source_supply as S
    path=tmp_path/'timeline.json';path.write_bytes(T.REGISTRY.read_bytes())
    monkeypatch.setattr(T,'REGISTRY',path)
    old=S.validation_signature()
    path.write_text(json.dumps(dict(version=1,proofs=[])))
    assert old!=S.validation_signature()
