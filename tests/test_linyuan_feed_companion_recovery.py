import hashlib
import json
from pathlib import Path
import sys

from PIL import Image
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import recover_feed_companions as r


def test_automatic_recovery_checks_trusted_origin_and_skips_fixed_archive_code():
    workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/linyuan-feed-companion-watch.yml').read_text()
    assert "run['head_branch']=='main'" in workflow
    assert "run['path']=='.github/workflows/linyuan-produce-cn.yml'" in workflow
    assert "run['head_repository']['full_name']==os.environ['GITHUB_REPOSITORY']" in workflow
    assert "old_archive['sha']!=current_archive" in workflow
    assert "if: steps.repair.outputs.changed == 'true'" in workflow
    assert 'linyuan-source-inventory.yml --ref main' in workflow


def package(directory,count=1):
    rows=[]
    for i in range(count):
        meta=dict(slug='ly-test',final=f'final_{i}.mp4',cover=f'cover_{i}.jpg',
            preview_30s=f'preview_{i}.mp4',contact_sheet_6=f'contact_{i}.jpg',
            subtitle_files=[f'subtitles_{i}.ass'],subtitle_edit_proofs=[f'proof_{i}.json'],
            cover_proof=dict(version=4,feed_safe_crop=[280,0,1000,720],
                             feed_square=f'cover_{i}_feed_square.jpg'))
        for name in [meta['final'],meta['preview_30s'],meta['contact_sheet_6'],
                     *meta['subtitle_files'],*meta['subtitle_edit_proofs']]:
            (directory/name).write_bytes(b'original accepted evidence')
        Image.new('RGB',(1280,720),'gray').save(directory/meta['cover'])
        meta['fingerprints']=dict(sha256=hashlib.sha256((directory/meta['final']).read_bytes()).hexdigest())
        rows.append(meta)
    (directory/'meta.json').write_text(json.dumps(rows))
    return rows


def validate(meta,directory):
    return None if (directory/meta['cover_proof']['feed_square']).is_file() else '信息流方形封面验收件缺失'


def test_repair_preserves_full_multi_part_evidence_and_revalidates_every_part(tmp_path):
    rows=package(tmp_path,2);raw=(tmp_path/'meta.json').read_bytes();calls=[]
    def inspect(meta,directory):calls.append(meta['final']);return validate(meta,directory)
    result=r.repair(tmp_path,'ly-test',1,2,inspect)
    assert result['changed'] and len(result['restored'])==2
    assert calls==['final_0.mp4','final_1.mp4']*2
    assert (tmp_path/'meta.json').read_bytes()==raw
    for row in rows:
        assert (tmp_path/'_accepted'/row['cover_proof']['feed_square']).is_file()
        assert Image.open(tmp_path/row['cover_proof']['feed_square']).size==(360,360)


def test_valid_delivery_is_not_reuploaded(tmp_path):
    rows=package(tmp_path)
    Image.new('RGB',(360,360)).save(tmp_path/rows[0]['cover_proof']['feed_square'])
    assert r.repair(tmp_path,'ly-test',1,2,validate)['changed'] is False
    assert not (tmp_path/'_accepted').exists()


@pytest.mark.parametrize('field,value',[('slug','ly-other'),('final','../outside.mp4')])
def test_origin_and_paths_fail_closed_before_restoring(tmp_path,field,value):
    rows=package(tmp_path);rows[0][field]=value
    (tmp_path/'meta.json').write_text(json.dumps(rows))
    with pytest.raises(ValueError):r.repair(tmp_path,'ly-test',1,2,validate)
    assert not list(tmp_path.glob('*_feed_square.jpg'))


def test_altered_video_or_other_quality_error_cannot_be_repaired(tmp_path):
    rows=package(tmp_path)
    with pytest.raises(ValueError,match='missing-feed-only'):
        r.repair(tmp_path,'ly-test',1,2,lambda *a:'字幕不同步')
    (tmp_path/rows[0]['final']).write_bytes(b'changed')
    with pytest.raises(ValueError,match='fingerprint'):
        r.repair(tmp_path,'ly-test',1,2,validate)
    assert not (tmp_path/'_accepted').exists()


def test_new_validation_failure_does_not_archive_repaired_files(tmp_path):
    package(tmp_path);calls=[]
    def inspect(*args):
        calls.append(1)
        return '信息流方形封面验收件缺失' if len(calls)==1 else '新人物门禁未通过'
    with pytest.raises(ValueError,match='full validation'):
        r.repair(tmp_path,'ly-test',1,2,inspect)
    assert not (tmp_path/'_accepted').exists()
