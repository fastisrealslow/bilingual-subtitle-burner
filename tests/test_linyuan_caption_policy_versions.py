"""New display edits must not rewrite historical subtitle evidence."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import caption_readability as C


def test_new_lexical_restarts_preserve_negatives_and_emphasis():
    raw = '没有没有，光伏光伏我没有参与。所以所以，赚了钱，赚了钱。'
    entries = [dict(start_sec=0, end_sec=12, zh=raw)]
    _, old = C.clean_entries(entries, policy_version=C.LEGACY_VERSION)
    _, new = C.clean_entries(entries)
    assert old['display_text'] == raw
    assert new['display_text'] == '没有没有，光伏我没有参与。所以，赚了钱，赚了钱。'
    for proof in (old, new):
        assert C.replay_edit_proof(proof) == (raw, proof['display_text'])
    # Relabeling an old proof as new cannot silently change its displayed text.
    forged = deepcopy(old)
    forged['version'] = C.VERSION
    with pytest.raises(ValueError):
        C.replay_edit_proof(forged)


def test_new_restart_does_not_cross_pause_or_full_stop():
    for word in ('光伏', '所以'):
        entries = [dict(start_sec=0, end_sec=1, zh=word),
                   dict(start_sec=2, end_sec=3, zh=word+'行业')]
        _, proof = C.clean_entries(entries)
        assert proof['display_text'] == word + word + '行业'
        _, proof = C.clean_entries([dict(start_sec=0, end_sec=3, zh=word+'。'+word)])
        assert proof['display_text'] == word+'。'+word


@pytest.mark.parametrize('version', [None, True, 2026092302, '2026092301'])
def test_unknown_edit_policy_is_never_replayed(version):
    _, proof = C.clean_entries([dict(start_sec=0, end_sec=3, zh='光伏光伏我没参与')])
    proof['version'] = version
    with pytest.raises(ValueError):
        C.replay_edit_proof(proof)
