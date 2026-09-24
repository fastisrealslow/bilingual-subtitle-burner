import copy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import source100_ab as ab


def fixture():
    plan, manifest = ab.inputs()
    sample = manifest['samples'][0]
    row = dict(sample=sample, status='passed', stage='final-accepted',
               finals=[dict(file='final.mp4')], source_sha256='same-mother',
               publication_snapshot_sha256=plan['publication_sha256'],
               tested_sha=plan['baseline'], run_id='1', diagnostic_subset=False)
    return plan, manifest, row


def test_failures_missing_reports_and_unpaired_bytes_stay_in_denominator():
    plan, manifest, baseline = fixture()
    optimized = dict(baseline, tested_sha=plan['optimized'], status='rejected', finals=[])
    result = ab.compare(plan, manifest, [('baseline', baseline), ('optimized', optimized)])
    assert result['variants']['baseline']['passed'] == 1
    assert result['variants']['baseline']['unresolved'] == 99
    assert result['variants']['optimized']['rejected'] == 1
    assert result['variants']['optimized']['unresolved'] == 99
    assert result['paired_counts']['regression'] == 1
    optimized['source_sha256'] = 'changed-mother'
    result = ab.compare(plan, manifest, [('baseline', baseline), ('optimized', optimized)])
    assert result['paired_counts'] == {'unpaired_source_bytes': 100}


@pytest.mark.parametrize('field,value', [
    ('tested_sha', 'wrong-revision'), ('diagnostic_subset', True),
    ('publication_snapshot_sha256', 'wrong-history'), ('finals', []),
    ('validation_error', 'invalid final'), ('source_sha256', None),
])
def test_invalid_provenance_or_missing_final_cannot_count_as_success(field, value):
    plan, manifest, row = fixture()
    row[field] = value
    with pytest.raises(ValueError):
        ab.compare(plan, manifest, [('baseline', row)])


def test_duplicate_reports_and_mixed_runs_cannot_form_best_of_score():
    plan, manifest, row = fixture()
    with pytest.raises(ValueError, match='duplicate'):
        ab.compare(plan, manifest, [('baseline', row), ('baseline', row)])
    other = dict(row, tested_sha=plan['optimized'], run_id='2')
    with pytest.raises(ValueError, match='combine runs'):
        ab.compare(plan, manifest, [('baseline', row), ('optimized', other)])
    other = copy.deepcopy(row)
    other['sample']['source_url'] = 'https://example.com/replacement'
    with pytest.raises(ValueError, match='replaced'):
        ab.compare(plan, manifest, [('baseline', other)])


def test_matrix_pairs_all_original_inputs_without_replacement():
    plan, manifest = ab.inputs()
    rows = ab.matrix(plan, manifest)['include']
    assert len(rows) == 200
    for a, b in zip(rows[::2], rows[1::2]):
        assert a['id'] == b['id'] and a['source_url'] == b['source_url']
        assert a['code_ref'] == plan['baseline'] and b['code_ref'] == plan['optimized']
