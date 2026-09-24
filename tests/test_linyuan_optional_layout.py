"""A failed optional format cannot discard or relabel verified source pixels."""
import copy
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import landscape as L
import produce_cn as P


def portrait(tmp_path):
    names = ['final.mp4', 'cover.jpg', 'preview.mp4', 'sheet.jpg', 'sub.ass', 'edit.json']
    for name in names:
        (tmp_path/name).write_bytes(('verified-'+name).encode())
    digest = hashlib.sha256((tmp_path/'final.mp4').read_bytes()).hexdigest()
    meta = dict(render_mode='live_video_card', layout_proof=dict(canvas=dict(width=720,height=1280)),
                final=names[0], cover=names[1], preview_30s=names[2], contact_sheet_6=names[3],
                subtitle_files=[names[4]], subtitle_edit_proofs=[names[5]],
                fingerprints=dict(sha256=digest), corner_review=dict(passed=True,media_sha256=digest))
    for key in ('live_region_verified', 'no_qr_verified', 'no_black_bars_verified',
                'review_assets_verified', 'subtitle_word_boundaries_verified',
                'subtitle_semantic_groups_verified', 'title_quality_verified'):
        meta[key] = True
    return meta


def reject(*args, **kwargs):
    raise P.VisualQualityError('真人动态区仍有原素材字幕或免责声明条')


def test_auto_retains_only_unchanged_verified_portrait(monkeypatch, tmp_path):
    meta = portrait(tmp_path)
    before = copy.deepcopy(meta)
    monkeypatch.setattr(L, 'reframe', reject)
    result = L.optional_reframe(meta, tmp_path, tmp_path/'work')
    proof = result.pop('layout_fallback')
    assert result == before == meta
    assert proof['rejected_layout_accepted'] is False
    assert proof['retained_layout'] == 'portrait'
    assert proof['retained_asset_sha256']['final.mp4'] == meta['fingerprints']['sha256']


def test_explicit_landscape_must_not_be_satisfied_by_portrait(monkeypatch, tmp_path):
    monkeypatch.setattr(L, 'reframe', reject)
    with pytest.raises(P.VisualQualityError, match='原素材字幕'):
        L.optional_reframe(portrait(tmp_path), tmp_path, tmp_path/'work', requested='landscape')


@pytest.mark.parametrize('name', ['final.mp4', 'sub.ass', 'cover.jpg', 'edit.json'])
def test_changed_original_asset_cannot_reuse_old_approval(monkeypatch, tmp_path, name):
    meta = portrait(tmp_path)
    def mutate(*args, **kwargs):
        (tmp_path/name).write_bytes(b'changed')
        reject()
    monkeypatch.setattr(L, 'reframe', mutate)
    with pytest.raises(P.VisualQualityError, match='原交付资产已变化'):
        L.optional_reframe(meta, tmp_path, tmp_path/'work')


@pytest.mark.parametrize('field', ['live_region_verified', 'no_qr_verified', 'title_quality_verified'])
def test_unverified_original_cannot_fall_back(monkeypatch, tmp_path, field):
    meta = portrait(tmp_path); meta[field] = False
    monkeypatch.setattr(L, 'reframe', reject)
    with pytest.raises(P.VisualQualityError, match='缺少已验收'):
        L.optional_reframe(meta, tmp_path, tmp_path/'work')


def test_hash_mismatch_is_not_a_layout_failure(monkeypatch, tmp_path):
    meta = portrait(tmp_path); meta['fingerprints']['sha256'] = 'wrong'
    monkeypatch.setattr(L, 'reframe', reject)
    with pytest.raises(P.VisualQualityError, match='指纹与验收证明不一致'):
        L.optional_reframe(meta, tmp_path, tmp_path/'work')


def test_reframe_cannot_mutate_retained_metadata(monkeypatch, tmp_path):
    meta = portrait(tmp_path); before = copy.deepcopy(meta)
    def mutate(candidate, *args, **kwargs):
        candidate['layout_proof']['canvas']['width'] = 1280
        reject()
    monkeypatch.setattr(L, 'reframe', mutate)
    result = L.optional_reframe(meta, tmp_path, tmp_path/'work')
    result.pop('layout_fallback')
    assert result == meta == before


def test_unclassified_runtime_failure_stays_visible(monkeypatch, tmp_path):
    def failure(*args, **kwargs): raise RuntimeError('unknown failure')
    monkeypatch.setattr(L, 'reframe', failure)
    with pytest.raises(RuntimeError, match='unknown failure'):
        L.optional_reframe(portrait(tmp_path), tmp_path, tmp_path/'work')


def test_successful_landscape_is_still_returned(monkeypatch, tmp_path):
    expected = dict(vertical=False, landscape_reframe=dict(version=2))
    monkeypatch.setattr(L, 'reframe', lambda *a, **kw: expected)
    assert L.optional_reframe(portrait(tmp_path), tmp_path, tmp_path/'work') == expected


@pytest.mark.parametrize('style',['classic','quiet'])
def test_layout_only_replay_can_use_known_landscape_without_old_captions(style):
    spec=L.layout(style)
    meta=dict(render_mode='live_video_card',layout_proof=spec)
    window=L.source_window(meta)
    assert window==spec['live_region']
    assert window['y']+window['height']<=spec['subtitle_region']['y']
    for change in ('live_region','subtitle_region','template'):
        changed=copy.deepcopy(meta)
        if change=='template':changed['layout_proof'][change]='unverified'
        else:changed['layout_proof'][change]['y']+=10
        with pytest.raises(ValueError,match='不是已知'):
            L.source_window(changed)
