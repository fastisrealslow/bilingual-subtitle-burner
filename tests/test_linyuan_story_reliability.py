"""Production regressions: partial classifier errors, false green and missing alerts."""
import json
from copy import deepcopy
from pathlib import Path
import sys

import pytest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'linyuan')]
import publishing_experiment as E
import production_status as S


def fixture():
    cues=[dict(start=i*60,end=i*60+50,text='我当年买了这家公司。后来我去看过它的产品。我就一直拿着。') for i in range(3)]
    return cues,[dict(start=i,end=i) for i in range(3)]


def verdict(detail=1,kind='personal_story'):
    return dict(kind=kind,subject_sentence=0,event_sentence=0,detail_sentence=detail)


def test_one_bad_neighbour_does_not_discard_a_valid_story_or_repeat_its_request(tmp_path):
    cues,picks=fixture();calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        if len(calls)==1:return dict(candidate_0=verdict(),candidate_1=verdict(0))
        assert set(schema['properties'])=={'candidate_0'}
        assert 'A repeated claim' in prompt
        return dict(candidate_0=verdict(2))
    result=E.choose_stories(cues,picks[:2],call,tmp_path/'selection.json')
    assert len(calls)==2 and len(result['selected'])==2 and not result['unresolved']
    assert result['selected'][0]['start']==0 and result['selected'][1]['start']==1
    assert json.loads((tmp_path/'selection.json').read_text())==result


@pytest.mark.parametrize('bad',[None,verdict(0),verdict(99),{**verdict(),'kind':'invented'}])
def test_unresolved_candidate_is_not_approved_but_valid_sibling_survives(tmp_path,bad):
    cues,picks=fixture();calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        return dict(candidate_0=verdict(),candidate_1=bad) if len(calls)==1 else dict(candidate_0=bad)
    r=E.choose_stories(cues,picks[:2],call,tmp_path/'selection.json')
    assert len(r['selected'])==1 and r['selected'][0]['start']==0
    assert r['retryable'] and r['unresolved'][0]['index']==1


def test_transport_timeout_is_saved_and_other_batches_are_still_examined(tmp_path):
    cues,picks=fixture();calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        if len(calls)<=2:raise RuntimeError('model timed out')
        return dict(candidate_0=verdict())
    r=E.choose_stories(cues,picks,call,tmp_path/'selection.json')
    assert len(r['selected'])==1 and r['selected'][0]['start']==2
    assert len(r['unresolved'])==2 and r['calls'][0]['error']=='model timed out'


def test_generic_skip_is_not_retried_or_invented_as_experiment(tmp_path):
    cues,picks=fixture();calls=[]
    def call(prompt,schema):
        calls.append(prompt);return {k:verdict(kind='skip') for k in schema['properties']}
    r=E.choose_stories(cues,picks[:2],call,tmp_path/'selection.json')
    assert not r['selected'] and not r['unresolved'] and len(calls)==1
    result=E.production_result(E.TREATMENT,[dict(title='normal')],r)
    assert result['fallback'] and not result['experiment_complete'] and result['experiment_accepted']==0


def test_actual_receipts_not_requested_profile_define_experiment_health():
    now=1792418400;parts=[]
    for day in ('2026-10-18','2026-10-19'):
        for hour in E.HOURS:
            plan=E.assignment(now,day+f' {hour:02d}')
            parts.append(dict(status='published',bvid=day+str(hour),publishing_experiment={
                **plan,'actual':E.BASELINE,'compliant':plan['planned']==E.BASELINE}))
    state=dict(published={'mother':dict(parts=parts)})
    r=E.operational_status(state,dict(portrait=0,landscape=0),now)
    assert r['status']=='needs_attention' and r['consecutive_fallbacks']==4
    assert r['actual_treatment_published']==r['valid_comparison_days']==0
    assert [d['fallback'] for d in r['daily']]==[2,2]
    assert E.operational_status(state,None,now)['alert']=='unknown_stock'


def test_status_never_calls_baseline_stock_a_successful_experiment(monkeypatch):
    now=1792418400;e=dict(slug='mother',editorial_profile=E.TREATMENT,ts=now-60)
    inv=dict(updated_at=now,quality_gate_version=S.fc.QUALITY_GATE_VERSION,
        editorial_policy_version=S.fc.editorial.VERSION,artifacts=[dict(slug='mother',parts=[
            dict(index=0,status='verified',editorial_profile=E.BASELINE,render_mode='direct')])])
    monkeypatch.setattr(S.fc,'inventory_publication_error',lambda *a:None)
    status,detail=S.classify(e,dict(dispatched=[e],published={}),inv,{},now)
    assert status=='experiment_fallback' and '不计入实验库存' in detail


def test_repaired_selector_can_retry_a_completed_old_fallback_once(monkeypatch):
    F=S.fc;now=1792418400
    monkeypatch.setattr(F.time,'time',lambda:now)
    monkeypatch.setattr(F,'source_inventory',lambda *a:dict(inventory_fresh=True))
    monkeypatch.setattr(F,'inventory_record_current',lambda *a:True)
    monkeypatch.setattr(F,'inventory_publication_error',lambda *a:None)
    mother=dict(slug='mother',source_url='https://www.bilibili.com/video/BVtest')
    old=dict(slug='old',source_url=mother['source_url'],experiment_parent_slug='mother',editorial_profile=E.TREATMENT)
    part=dict(index=0,status='verified',source_sha256='a'*64,editorial_profile=E.BASELINE)
    records=[dict(slug=e['slug'],run_id=123,parts=[deepcopy(part)]) for e in (mother,old)]
    state=dict(dispatched=[mother,old]);inv=dict(artifacts=records)
    item=dict(page_url=mother['source_url'],extra=dict(duration=1800))
    assert len(list(F.experiment_reuse_candidates(state,inv,[item])))==1
    old['experiment_selection_revision']=E.SELECTION_REVISION
    assert not list(F.experiment_reuse_candidates(state,inv,[item]))


def test_independent_delivery_gate_rejects_baseline_and_changed_actual_bytes(tmp_path):
    import importlib.util,hashlib
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('story_delivery',root/'scripts/verify_linyuan_story_delivery.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import test_linyuan_publishing_experiment as fixtures
    meta,_,_=fixtures.treatment()
    video=b'actual-video-test';cover=b'actual-cover-test'
    meta.update(final='final.mp4',cover='cover.jpg')
    meta['fingerprints']['sha256']=hashlib.sha256(video).hexdigest()
    meta['cover_proof']['sha256']=hashlib.sha256(cover).hexdigest()
    meta['experiment_proof'].update(video_sha256=meta['fingerprints']['sha256'],cover_sha256=meta['cover_proof']['sha256'])
    (tmp_path/'final.mp4').write_bytes(video);(tmp_path/'cover.jpg').write_bytes(cover)
    (tmp_path/'meta.json').write_text(json.dumps(meta))
    assert module.verify(tmp_path)['experiment_complete']
    (tmp_path/'cover.jpg').write_bytes(b'changed')
    with pytest.raises(ValueError,match='bytes'):module.verify(tmp_path)
    meta['editorial_profile']=E.BASELINE;(tmp_path/'meta.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='NOT experiment success'):module.verify(tmp_path)


def test_scene_cover_finds_real_pixels_between_measured_source_banners():
    from source_geometry import scene_cover_crop
    face=(818,401,273,403);marks=[(0,0,1,.25),(0,.89,1,1)]
    x,y,w,h=scene_cover_crop(face,1920,1080,marks)
    assert w>=960 and h>=540 and 1.6<=w/h<=1.95
    assert y>=1080*.25 and y+h<=1080*.89
    assert x<=818 and x+w>=818+273 and y<=401 and y+h>=804
    # Do not turn a low-resolution / portrait source into a fake clean scene.
    for width,height in [(800,450),(1080,1920)]:
        with pytest.raises(ValueError):scene_cover_crop(face,width,height,[])
    with pytest.raises(ValueError):scene_cover_crop(face,1920,1080,[(0,.3,1,.8)])


def test_long_guest_answer_is_selectable_without_truncating_its_context():
    # Production had one 178-character answer, excluded by a 140-char quote
    # ceiling, leaving only the host question and 嗯好呀 selectable.
    first='我当时听人讲过腾讯，后来没有买。'
    rest='后来又有人跟我介绍产品，我听过以后仍然没有买，'
    long=first+rest*9+'所以这些机会都是存在。'
    cues=[dict(text='有没有哪次看走眼？'),dict(text=long),dict(text='嗯，好呀。')]
    rows=E._selection_rows(cues,[dict(start=0,end=2)])
    assert rows[0]['text']==''.join(c['text'] for c in cues)
    assert ''.join(s['text'] for s in rows[0]['sentences'])==rows[0]['text']
    assert any('腾讯' in s['text'] and len(s['text'])<=140 for s in rows[0]['sentences'])
    assert any('仍然没有买' in s['text'] and len(s['text'])<=140 for s in rows[0]['sentences'])
    response=dict(choices=[dict(index=0,kind='personal_story',subject_quote='腾讯',
        event_quote=first,detail_quote='嗯，好呀。')])
    with pytest.raises(ValueError,match='filler'):E.validate_choices(cues,[dict(start=0,end=2)],response)


def test_preview_identity_requires_explicit_readonly_bundle_mode(tmp_path):
    from compact_delivery import compact
    with pytest.raises(ValueError,match='slug'):compact(tmp_path,'preview-123',validate=lambda *a:None)
    with pytest.raises(ValueError,match='slug'):compact(tmp_path,'ly-production',validate=lambda *a:None,preview_only=True)


def test_old_filler_proof_cannot_become_an_actual_experiment_again():
    import test_linyuan_publishing_experiment as fixtures
    meta,_,_=fixtures.treatment()
    meta['experiment_proof']['selection']['detail_quote']='嗯，好呀。'
    assert E.proof_error(meta) and E.actual_profile(meta)==E.BASELINE
