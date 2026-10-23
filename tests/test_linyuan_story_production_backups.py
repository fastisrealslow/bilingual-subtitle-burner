from copy import deepcopy
import json
import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import publishing_experiment as E
import story_production_backups as B


def test_original_whole_answers_survive_without_leaking_story_proof_or_mutating_inputs():
    original = [dict(start=2, end=8, story_selection=dict(kind='personal_story')),
                dict(start=15, end=20)]
    selected = [original[0]]; before = deepcopy(original)
    planned, first = B.plan(original, selected)
    assert original == before and first == 1
    assert [(p['start'], p['end']) for p in planned[first:]] == [(2, 8), (15, 20)]
    assert not any(p.get('story_selection') for p in planned[first:])
    assert B.plan(original, []) == (original, None)


@pytest.mark.parametrize('previous', [None, E.TREATMENT])
def test_baseline_writer_scope_restores_requested_profile_even_after_failure(monkeypatch, previous):
    if previous is None: monkeypatch.delenv('LINYUAN_EDITORIAL_PROFILE', raising=False)
    else: monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE', previous)
    with pytest.raises(RuntimeError):
        with B.writer_profile(True):
            assert E.profile() == E.BASELINE
            raise RuntimeError('Independent quality rejection')
    assert os.environ.get('LINYUAN_EDITORIAL_PROFILE') == previous


@pytest.mark.parametrize('story_passes,baseline_passes,expected_calls,accepted', [
    (True, True, [E.TREATMENT], 1),
    (False, True, [E.TREATMENT, E.BASELINE], 1),
    (False, False, [E.TREATMENT, E.BASELINE, E.BASELINE], 0),
])
def test_actual_cli_tries_baseline_only_after_all_story_quality_failures_and_never_counts_it_as_t(
        tmp_path, monkeypatch, story_passes, baseline_passes, expected_calls, accepted):
    import produce_cn as P
    import source_selection as S
    import visual_selection as V
    monkeypatch.setenv('LINYUAN_AUTOMATIC_ONLY', 'true')
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST', 'true')
    monkeypatch.setenv('LINYUAN_EDITORIAL_PROFILE', E.TREATMENT)
    monkeypatch.delenv('SOURCE_VISUAL_CHAPTERS', raising=False)
    monkeypatch.delenv('PUBLICATION_STATE_PATH', raising=False)
    monkeypatch.setattr(P, 'BASE', tmp_path)
    monkeypatch.setattr(P, 'TEXT_BACKEND', 'local')
    monkeypatch.setattr(P, 'load_key', lambda: None)
    cues = [dict(start=0, end=35, text='我当年买了这家公司。后来我去看过它的产品。'),
            dict(start=50, end=90, text='这是一条不同的完整普通观点，所有字幕和画面检查都还要执行。')]
    picks = [dict(start=i, end=i, score=7, reason='Test-only complete candidate') for i in range(2)]
    story = {**picks[0], 'story_selection': dict(kind='personal_story')}
    report = dict(passed=True, source_sha256='a' * 64, duration_sec=1400,
                  visual_identity={}, resolution=dict(width=1920, height=1080))
    monkeypatch.setattr(P, 'run_source_quality_gate', lambda *a: report)
    monkeypatch.setattr(P, 'transcribe', lambda *a: cues)
    monkeypatch.setattr(S, 'select', lambda *a, **k: picks)
    monkeypatch.setattr(V, 'rank', lambda src, c, p, *a: p)
    monkeypatch.setattr(P, '_download_speaker_reference', lambda *a: None)
    monkeypatch.setattr(P, '_local_face_models', lambda: None)
    monkeypatch.setattr(P, '_ocr', lambda: None)
    monkeypatch.setattr(P, 'story_selection_inputs', lambda *a: (picks, []))
    monkeypatch.setattr(E, 'choose_stories', lambda *a: dict(selected=[story], unresolved=[]))
    calls = []
    def produce(src, work, out, seg, *a, **kw):
        current = E.profile(); calls.append(current)
        if (current == E.TREATMENT and not story_passes) or (current == E.BASELINE and not baseline_passes):
            raise P.VisualQualityError('Test-only independent subtitle/picture gate failure')
        assert bool(kw['preselected_picks'][0].get('story_selection')) == (current == E.TREATMENT)
        # This is test metadata only, never an artifact or actual media approval.
        return dict(final='test-only.mp4', title='测试元数据', editorial_profile=current,
                    render_mode='live_video_card', resolution=dict(width=720, height=1280), duration_sec=35)
    monkeypatch.setattr(P, 'produce_part_with_budget', produce)
    video = tmp_path / 'source.mp4'; video.write_bytes(b'Not real media')
    monkeypatch.setattr(P.sys, 'argv', ['produce_cn', '--source', str(video), '--slug', 'backups',
                                      '--split-highlights', '--require-live-video'])
    assert P.main() == (0 if accepted else 2)
    batch = json.loads((tmp_path / 'deliver/backups/batch_report.json').read_text())
    assert calls == expected_calls and batch['accepted'] == accepted
    if not story_passes:
        result = batch['experiment_production']
        assert result['requested'] == E.TREATMENT
        assert result['experiment_accepted'] == 0 and not result['experiment_complete']
        assert result['baseline_accepted'] == accepted
        assert result['fallback'] is bool(accepted)
    assert E.profile() == E.TREATMENT
    assert not list(tmp_path.rglob('final*.mp4'))
