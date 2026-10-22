"""A failed portrait candidate cannot remain in deliverable-root MP4 globs."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
from batch_delivery import quarantine_part


@pytest.mark.parametrize('suffix',['','_2'])
def test_both_native_and_portrait_failure_names_are_isolated(tmp_path,suffix):
    names=[]
    for variant in (suffix,suffix+'_portrait'):
        names.extend([f'final{variant}.mp4',f'preview_30s{variant}.mp4',
            f'contact_sheet_6{variant}.jpg',
            f'cover{variant}.jpg' if variant else 'cover_16x9.jpg'])
    for name in names:
        (tmp_path/name).write_bytes(name.encode())
        (tmp_path/(name+'.proof.json')).write_text('{}')
    neighbour=tmp_path/'final_3_portrait.mp4';neighbour.write_bytes(b'accepted different part')
    quarantine_part(tmp_path,suffix)
    target=tmp_path/'_tmp/rejected'/(suffix.strip('_') or '1')
    assert neighbour.read_bytes()==b'accepted different part'
    assert sorted(p.name for p in tmp_path.glob('*.mp4'))==[neighbour.name]
    for name in names:
        assert (target/name).read_bytes()==name.encode()
        assert (target/(name+'.proof.json')).is_file()
        assert not (tmp_path/name).exists()


def test_explicit_portrait_suffix_never_moves_a_different_native_part(tmp_path):
    failed=tmp_path/'final_2_portrait.mp4';failed.write_bytes(b'bad')
    native=tmp_path/'final_2.mp4';native.write_bytes(b'other explicit namespace')
    quarantine_part(tmp_path,'_2_portrait')
    assert native.read_bytes()==b'other explicit namespace'
    assert not failed.exists()
    assert (tmp_path/'_tmp/rejected/2_portrait'/failed.name).read_bytes()==b'bad'
