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


def test_source32_partial_words_keep_historical_proofs_and_actual_char_times():
    entries = [dict(start_sec=0, end_sec=2.8, zh='那请问垄垄断成瘾的行业，在您的选择逻'),
               dict(start_sec=48.08, end_sec=51.6, zh='啊，就了，虽虽然它后来，比如说这个公')]
    old_entries, old = C.clean_entries(entries, policy_version=C.SEPT23_VERSION)
    new_entries, new = C.clean_entries(entries)
    assert '垄垄断' in old['display_text'] and '虽虽然' in old['display_text']
    assert new['display_text'] == old['display_text'].replace('垄垄断', '垄断').replace('虽虽然', '虽然')
    # A deletion must not shift the time of the surviving original character.
    old_atoms = [tuple(c) for e in old_entries for c in e['caption_chars']]
    assert all(tuple(c) in old_atoms for e in new_entries for c in e['caption_chars'])
    for proof in (old, new):
        assert C.replay_edit_proof(proof) == (proof['raw_text'], proof['display_text'])
    forged = deepcopy(old)
    forged['version'] = C.VERSION
    with pytest.raises(ValueError):
        C.replay_edit_proof(forged)


@pytest.mark.parametrize('first,last', [('垄', '垄断'), ('虽', '虽然')])
def test_partial_word_cleanup_does_not_cross_real_pause(first, last):
    entries = [dict(start_sec=0, end_sec=1, zh=first),
               dict(start_sec=2, end_sec=3, zh=last)]
    _, proof = C.clean_entries(entries)
    assert proof['display_text'] == first + last


@pytest.mark.parametrize('version', [None, True, 2026092302, '2026092301'])
def test_unknown_edit_policy_is_never_replayed(version):
    _, proof = C.clean_entries([dict(start_sec=0, end_sec=3, zh='光伏光伏我没参与')])
    proof['version'] = version
    with pytest.raises(ValueError):
        C.replay_edit_proof(proof)
