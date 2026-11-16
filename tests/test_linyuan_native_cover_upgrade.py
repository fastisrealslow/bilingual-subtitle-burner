import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import produce_cn as P
import upgrade_native_cover_delivery as U


def bundle(tmp_path):
    original=tmp_path/'original';original.mkdir()
    mother=tmp_path/'mother.mp4';mother.write_bytes(b'bound original mother')
    (original/'final.mp4').write_bytes(b'accepted video')
    (original/'subtitle.ass').write_bytes(b'accepted subtitle')
    (original/'cover.jpg').write_bytes(b'old accepted cover')
    row=dict(slug='ly-test',speaker='林园',title='原审定标题',cover_title='原审定文案',
        final='final.mp4',cover='cover.jpg',subtitle_files=['subtitle.ass'],
        source_sha256=U.sha(mother),fingerprints=dict(sha256=U.sha(original/'final.mp4')),
        segments=[dict(start=100,end=180)],editorial_profile='baseline',
        cover_proof=dict(style='dark'))
    (original/'meta.json').write_text(json.dumps([row],ensure_ascii=False))
    return original,mother,row


def fake_renderer(monkeypatch,seen=None,missing=False):
    monkeypatch.setattr(P,'_download_speaker_reference',lambda speaker,stage:stage/'reference.jpg')
    def render(source,tracked,start,end,title,speaker,path,**kwargs):
        assert tracked is None and kwargs['style']=='scene'
        assert kwargs['allow_editorial_fallback'] is False
        if seen is not None:seen.append((source,start,end,title))
        path.write_bytes(b'new strict scene')
        proof=dict(style='scene',sha256=U.sha(path),no_added_text=True,no_black_bars=True,
            no_qr=True,face_fully_visible=True,feed_safe_face=True)
        if missing:proof.pop('no_qr')
        Path(str(path)+'.proof.json').write_text(json.dumps(proof))
        return 'scene',None
    monkeypatch.setattr(P,'make_live_clip_cover',render)


def test_native_backfill_keeps_original_bundle_and_baseline_cohort(monkeypatch,tmp_path):
    original,mother,row=bundle(tmp_path);before={p.name:U.sha(p) for p in original.iterdir()}
    seen=[];fake_renderer(monkeypatch,seen)
    output=tmp_path/'new-delivery'
    report=U.upgrade(original,output,mother,'ly-test',10,20,validate=lambda *a:None)
    updated=json.loads((output/'meta.json').read_text())[0]
    assert report['changed'] and report['original_delivery_unchanged']
    assert not report['published'] and report['experiment_membership_unchanged']
    assert before=={p.name:U.sha(p) for p in original.iterdir()}
    assert seen==[(mother,100,180,row['cover_title'])]
    assert updated['editorial_profile']=='baseline' and 'experiment_proof' not in updated
    assert updated['title']==row['title'] and updated['cover_title']==row['cover_title']
    assert updated['segments']==row['segments']
    for name in ('final.mp4','subtitle.ass'):assert U.sha(output/name)==before[name]
    assert updated['native_cover_upgrade']['original_artifact_id']==20


@pytest.mark.parametrize('issue',['wrong_source','bad_video_hash','unsafe_cover','unrelated_validation'])
def test_bad_original_cannot_be_rescued_as_cover_change(tmp_path,issue):
    original,mother,row=bundle(tmp_path)
    if issue=='wrong_source':row['source_sha256']='0'*64
    if issue=='bad_video_hash':row['fingerprints']['sha256']='0'*64
    if issue=='unsafe_cover':row['cover']='../outside.jpg'
    (original/'meta.json').write_text(json.dumps([row]))
    output=tmp_path/'new-delivery'
    with pytest.raises(ValueError):
        U.upgrade(original,output,mother,'ly-test',10,20,
                  validate=lambda *a:'subtitle alignment failed' if issue=='unrelated_validation' else None)
    assert not output.exists()


def test_failed_scene_is_not_upgraded_or_relabelled(monkeypatch,tmp_path):
    original,mother,row=bundle(tmp_path)
    monkeypatch.setattr(P,'_download_speaker_reference',lambda speaker,stage:stage/'reference.jpg')
    def fail(*args,**kwargs):raise P.VisualQualityError('Native face too small')
    monkeypatch.setattr(P,'make_live_clip_cover',fail)
    output=tmp_path/'new-delivery'
    report=U.upgrade(original,output,mother,'ly-test',10,20,validate=lambda *a:None)
    assert not report['changed'] and report['skipped'][0]['reason']=='Native face too small'
    assert json.loads((output/'meta.json').read_text())==[row]
    assert (output/'cover.jpg').read_bytes()==(original/'cover.jpg').read_bytes()


def test_incomplete_scene_proof_cannot_enter_inventory(monkeypatch,tmp_path):
    original,mother,row=bundle(tmp_path);fake_renderer(monkeypatch,missing=True)
    report=U.upgrade(original,tmp_path/'new',mother,'ly-test',10,20,validate=lambda *a:None)
    assert not report['changed'] and 'proof is incomplete' in report['skipped'][0]['reason']


def test_new_delivery_is_fully_revalidated_and_measured_mutations_not_saved(monkeypatch,tmp_path):
    original,mother,row=bundle(tmp_path);fake_renderer(monkeypatch)
    calls=[]
    def validate(meta,directory):
        calls.append((copy.deepcopy(meta),directory));meta['measurement_only']=True
    output=tmp_path/'new'
    U.upgrade(original,output,mother,'ly-test',10,20,validate=validate)
    assert len(calls)==3 and calls[0][0]['cover_proof']['style']=='dark'
    assert calls[-1][0]['cover_proof']['style']=='scene'
    assert 'measurement_only' not in json.loads((output/'meta.json').read_text())[0]


def test_existing_destination_is_never_overwritten(tmp_path):
    original,mother,row=bundle(tmp_path);output=tmp_path/'new';output.mkdir()
    with pytest.raises(ValueError,match='must be new'):
        U.upgrade(original,output,mother,'ly-test',10,20,validate=lambda *a:None)


def test_failed_full_revalidation_never_emits_delivery_or_changes_original(monkeypatch,tmp_path):
    original,mother,row=bundle(tmp_path);fake_renderer(monkeypatch)
    before={p.name:U.sha(p) for p in original.iterdir()};output=tmp_path/'new'
    def validate(meta,directory):
        return 'feed crop not safe' if meta['cover_proof']['style']=='scene' else None
    with pytest.raises(ValueError,match='fails full validation'):
        U.upgrade(original,output,mother,'ly-test',10,20,validate=validate)
    assert not output.exists() and before=={p.name:U.sha(p) for p in original.iterdir()}


def test_existing_story_scene_is_not_rebuilt_or_changed(monkeypatch,tmp_path):
    original,mother,row=bundle(tmp_path)
    row.update(editorial_profile='field-story-v1',experiment_proof=dict(original='preserved'),
               cover_proof=dict(style='scene'))
    (original/'meta.json').write_text(json.dumps([row]))
    def unexpected(*a,**k):raise AssertionError('Already scene must not render again')
    monkeypatch.setattr(P,'make_live_clip_cover',unexpected)
    output=tmp_path/'new'
    report=U.upgrade(original,output,mother,'ly-test',10,20,validate=lambda *a:None)
    assert not report['changed'] and json.loads((output/'meta.json').read_text())==[row]


def test_cli_uses_explicit_bound_inputs_without_publishing(monkeypatch,capsys):
    seen=[]
    monkeypatch.setattr(U,'upgrade',lambda *a:(seen.append(a) or dict(changed=True,published=False)))
    U.main(['--directory','accepted','--output','new','--mother','mother.mp4','--slug','ly-test',
            '--run-id','10','--artifact-id','20'])
    assert seen==[('accepted','new','mother.mp4','ly-test',10,20)]
    assert json.loads(capsys.readouterr().out)==dict(changed=True,published=False)
