"""Independently validate unchanged files from the completed fallback trial.

The producer succeeded; only its diagnostic slug failed compact packaging.
This does not rename metadata, weaken slug rules, rerender, register or publish.
"""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))

SOURCE='fc8b5dc5ebf11a64b701bc871c60bc8af8eaf57599968aa6c5478d9c93cfb7f2'
FINAL='87b447aa79554d7eb2b8b6c89ebb5a35ffd4eb66c8828edc6da299e36998fdf6'
SLUG='preview-story-backups-39965419893'


def verify(archive,output):
    from artifact_range import delivery_files
    from publishing_experiment import actual_profile,BASELINE
    from source_supply import validate_part
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        # The original debug artifact has one authoritative root metadata file.
        raw=bundle.read('meta.json');data=json.loads(raw)
        rows=data if isinstance(data,list) else [data]
        assert len(rows)==1
        row=rows[0]
        assert row['slug']==SLUG and row['source_sha256']==SOURCE
        assert row['automatic_only'] is True and actual_profile(row)==BASELINE
        assert row['fingerprints']['sha256']==FINAL
        names={'meta.json',*delivery_files(row)}
        for name in names:
            assert Path(name).name==name and name not in {'.','..'}
            info=bundle.getinfo(name)
            assert not stat.S_ISLNK(info.external_attr>>16) and info.file_size<256*1024*1024
            with bundle.open(info) as src,(output/name).open('wb') as dst:
                shutil.copyfileobj(src,dst)
    media=output/row['final']
    assert hashlib.sha256(media.read_bytes()).hexdigest()==FINAL
    issue=validate_part(row,output)
    if issue:raise ValueError('Actual original delivery failed independent validation: '+issue)
    assert (output/'meta.json').read_bytes()==raw
    assert hashlib.sha256(media.read_bytes()).hexdigest()==FINAL
    result=dict(passed=True,producer_run_id=39965419893,source_old_zero_run_id=39944885125,
        source_sha256=SOURCE,old_accepted=0,new_accepted=1,treatment_accepted=0,
        actual_profile=BASELINE,title=row['title'],resolution=row['resolution'],
        final_sha256=FINAL,full_media_validation_passed=True,
        metadata_unchanged=True,media_unchanged=True,diagnostic_only=True,
        registered_stock=False,published=False)
    (output/'independent-validation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
    return result


if __name__=='__main__':verify(sys.argv[1],sys.argv[2])
