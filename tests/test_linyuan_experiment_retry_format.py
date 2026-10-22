import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'/'fc'))
import index as fc


def inputs():
    parent=dict(slug='parent')
    entry=dict(slug='child',editorial_profile=fc.experiment.TREATMENT,output_layout='landscape',
        experiment_parent_slug='parent',experiment_mother_sha256='a'*64,
        source_check_run_id=123,source_check_attempts=1,
        production_failure_summary=dict(slug='child',run_id=123,artifact_id=456,accepted=0))
    part=dict(status='published',bvid='BVactual',source_segments=[dict(start=1,end=30)],
        source_sha256='a'*64,resolution=dict(width=720,height=1280),render_mode='live_video_card')
    state=dict(dispatched=[parent,entry],published=dict(parent=dict(parts=[part])))
    return entry,state,dict(portrait=2,landscape=0)


def test_completed_empty_batch_uses_same_mother_portrait_without_reset_or_mutation():
    entry,state,needs=inputs();before=copy.deepcopy((entry,state,needs))
    assert fc.experiment_retry_layout(entry,state,needs,[])=='portrait'
    assert (entry,state,needs)==before


@pytest.mark.parametrize('changes',[
    dict(editorial_profile='baseline'),dict(output_layout='auto'),dict(source_check_run_id=None),
    dict(source_check_run_id=999),dict(experiment_mother_sha256='b'*64),
    dict(experiment_parent_slug='missing'),dict(production_failure_summary={}),
])
def test_no_unbound_or_unrelated_retry_can_change_layout(changes):
    entry,state,needs=inputs();entry.update(changes)
    assert fc.experiment_retry_layout(entry,state,needs,[])==entry.get('output_layout','auto')


@pytest.mark.parametrize('changes',[
    dict(accepted=1),dict(accepted=False),dict(slug='other'),dict(run_id=999),
    dict(artifact_id=0),dict(artifact_id=None),
])
def test_any_existing_output_or_invalid_failure_receipt_keeps_original(changes):
    entry,state,needs=inputs();entry['production_failure_summary'].update(changes)
    assert fc.experiment_retry_layout(entry,state,needs,[])=='landscape'


def test_no_running_task_change_and_no_portrait_overproduction():
    entry,state,needs=inputs()
    for status in ('queued','in_progress'):
        run=dict(status=status,display_title='中文源出片 · child')
        assert fc.experiment_retry_layout(entry,state,needs,[run])=='landscape'
    assert fc.experiment_retry_layout(entry,state,dict(portrait=0,landscape=2),[])=='landscape'


def test_unproven_or_mixed_previous_geometry_cannot_retarget_retry():
    entry,state,needs=inputs()
    row=state['published']['parent']['parts'][0]
    row['source_sha256']='b'*64
    assert fc.experiment_retry_layout(entry,state,needs,[])=='landscape'
    row['source_sha256']='a'*64
    state['published']['parent']['parts'].append({**row,'resolution':dict(width=1280,height=720)})
    assert fc.experiment_retry_layout(entry,state,needs,[])=='landscape'
