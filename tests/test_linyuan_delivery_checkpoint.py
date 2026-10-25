"""A later task failure cannot promote unaccepted or mismatched output."""
import json
from pathlib import Path
import sys

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
from delivery_checkpoint import accepted_checkpoint


def write_checkpoint(root, **changes):
    (root/'_tmp').mkdir(exist_ok=True)
    source=dict(passed=True,source_sha256='a'*64)
    row=dict(slug='ly-checkpoint',source_sha256='a'*64,final='final_8.mp4')
    report=dict(slug='ly-checkpoint',accepted=1,accepted_finals=['final_8.mp4'],
                source_context=source,selection_completed=False)
    report.update(changes)
    for name,data in [('_tmp/source_quality.json',source),('meta.json',row),('batch_report.json',report)]:
        (root/name).write_text(json.dumps(data))


def test_incomplete_batch_exposes_only_a_source_bound_manifest_not_media_approval(tmp_path):
    write_checkpoint(tmp_path)
    result=accepted_checkpoint(tmp_path,'ly-checkpoint')
    assert result==dict(accepted=True,parts=1,source_sha256='a'*64,media_approved=False)
    assert not (tmp_path/'_publish_ready').exists()


@pytest.mark.parametrize('changes',[
    dict(slug='other'),dict(accepted=True),dict(accepted=2),
    dict(accepted_finals=['unaccepted.mp4']),
    dict(source_context=dict(passed=True,source_sha256='b'*64)),
    dict(source_context=dict(passed=False,source_sha256='a'*64)),
])
def test_wrong_count_identity_or_source_cannot_open_delivery(tmp_path,changes):
    write_checkpoint(tmp_path,**changes)
    with pytest.raises(ValueError):accepted_checkpoint(tmp_path,'ly-checkpoint')


def test_missing_or_zero_checkpoint_cannot_upload_stale_outputs(tmp_path):
    assert not accepted_checkpoint(tmp_path,'ly-checkpoint')['accepted']
    write_checkpoint(tmp_path,accepted=0,accepted_finals=[])
    with pytest.raises(ValueError):accepted_checkpoint(tmp_path,'ly-checkpoint')
    (tmp_path/'meta.json').unlink()
    assert not accepted_checkpoint(tmp_path,'ly-checkpoint')['accepted']


def test_interrupted_upload_still_requires_compact_full_validation():
    workflow=yaml.safe_load((ROOT/'.github/workflows/linyuan-produce-cn.yml').read_text())
    steps={s.get('id'):s for s in workflow['jobs']['produce']['steps'] if s.get('id')}
    assert 'always()' in steps['delivery_checkpoint']['if']
    assert "steps.source_gate.outcome == 'success'" in steps['delivery_checkpoint']['if']
    assert "steps.delivery_checkpoint.outputs.accepted == 'true'" in steps['archive_accepted']['if']
    assert 'compact_delivery.py' in steps['compact_delivery']['run']
    upload=next(s for s in workflow['jobs']['produce']['steps'] if s.get('name')=='上传成片到 Artifact')
    assert upload['if']=="always() && steps.compact_delivery.outcome == 'success'"
