"""Keep source-title evidence verifiable after conservative subtitle editing."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import caption_readability as C
import title_rewrite as T
from fc import index as FC
from batch_delivery import archive_accepted


def delivery(root):
    raw = '我们我们不是只看股价。我们在财务框架里考虑，行业增长不能保证每家公司都赚钱。'
    _, proof = C.clean_entries([dict(start_sec=0, end_sec=30, zh=raw)])
    title = '林园：我们不是只看股价，行业增长不能保证每家公司都赚钱'
    item = dict(title=title, cover_title='行业增长，不保证都赚钱', subject='行业', evidence=[raw])
    package = T._package(item, raw, dict(method='cpu_text_review', appeal=4,
        reason='原文明确区分行业增长与每家公司的盈利，标题保持原文限定',
        **{key: True for key in T.CHECKS}), [item])
    name = 'subtitle_edit_proof_2-1.json'
    (root/name).write_text(json.dumps(proof, ensure_ascii=False))
    (root/'subtitles.ass').write_text(
        '[Events]\nDialogue: 0,0:00:00.00,0:00:30.00,Default,,0,0,0,,' + proof['display_text'] + '\n')
    meta = dict(title=title, title_rewrite=package['title_rewrite'],
        subtitle_files=['subtitles.ass'], subtitle_text_sha256=FC.editorial.text_digest(proof['display_text']),
        subtitle_edit_proof_version=1, subtitle_edit_proofs=[name])
    return meta, proof


def test_real_ass_and_replayed_source_support_title_after_stutter_removal(tmp_path):
    meta, proof = delivery(tmp_path)
    legacy = dict(meta); legacy.pop('subtitle_edit_proof_version')
    assert '不能回溯' in FC.artifact_subtitle_error(legacy, tmp_path)
    assert FC.artifact_subtitle_error(meta, tmp_path) is None
    assert C.replay_edit_proof(proof) == (proof['raw_text'], proof['display_text'])


@pytest.mark.parametrize('attack', ['negation', 'raw', 'hash', 'missing', 'escape', 'no_entries'])
def test_edit_proof_cannot_hide_changed_source_or_captions(tmp_path, attack):
    meta, proof = delivery(tmp_path)
    path = tmp_path/meta['subtitle_edit_proofs'][0]
    if attack == 'negation':
        proof['display_text'] = proof['display_text'].replace('不能', '能')
        (tmp_path/'subtitles.ass').write_text('Dialogue: 0,0:00:00.00,0:00:30.00,Default,,0,0,0,,'+proof['display_text'])
        meta['subtitle_text_sha256'] = FC.editorial.text_digest(proof['display_text'])
        path.write_text(json.dumps(proof))
    elif attack == 'raw':
        _, proof = C.clean_entries([dict(start_sec=0,end_sec=30,zh=proof['display_text'])])
        path.write_text(json.dumps(proof))
    elif attack == 'hash':
        meta['title_rewrite']['source_sha256'] = '0'*64
    elif attack == 'missing':
        path.unlink()
    elif attack == 'escape':
        meta['subtitle_edit_proofs'] = ['../'+path.name]
    else:
        proof.pop('raw_entries'); path.write_text(json.dumps(proof))
    assert '证明无法重放' in FC.artifact_subtitle_error(meta, tmp_path)


def test_delivery_archive_includes_the_replayable_proof(tmp_path):
    meta, _ = delivery(tmp_path)
    meta.update(final='final_2.mp4', cover='cover_2.jpg', preview_30s='preview_2.mp4', contact_sheet_6='contact_2.jpg')
    (tmp_path/'final_2.mp4').write_bytes(b'video')
    (tmp_path/'cover_2.jpg').write_bytes(b'cover')
    (tmp_path/'preview_2.mp4').write_bytes(b'preview')
    (tmp_path/'contact_2.jpg').write_bytes(b'contact')
    (tmp_path/'meta.json').write_text(json.dumps([meta]))
    archive_accepted(tmp_path, 'case')
    folder = tmp_path/'_accepted'
    assert json.loads((folder/meta['subtitle_edit_proofs'][0]).read_text())['replay_version'] == 1
    assert FC.artifact_subtitle_error(meta, folder) is None


def test_actual_bottom_disclaimer_is_excluded_with_full_face_intact():
    from produce_cn import source_edge_text_exclusions
    from live_tracking import crop_box
    fixture = Path(__file__).parent/'fixtures/linyuan_801_crop.json'
    case = json.loads(fixture.read_text())['frames'][0]
    rows = [dict(text='本场直播文字实录，可关注证券市场红周刊公众号获取！',
        confidence=.9772869, rect=[.2369791667,.9435185185,.7651041667,.9768518519])]
    bands = source_edge_text_exclusions(rows)
    assert len(bands) == 1 and bands[0][0] == 0 and bands[0][2:] == (1,1)
    x,y,w,h = crop_box(case['face'],1920,1080,exclusions=case['marks']+bands)
    fx,fy,fw,fh = case['face']
    assert y+h < bands[0][1]*1080
    assert x+8 <= fx and fx+fw <= x+w-8 and y+fh*.22 <= fy and fy+fh <= y+h-2
    assert not source_edge_text_exclusions([{**rows[0], 'confidence':.3}])
    assert not source_edge_text_exclusions([{**rows[0], 'rect':[.2,.4,.8,.5]}])
