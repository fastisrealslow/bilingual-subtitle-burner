"""Regressions for partial candidate batches, ordering, and cover geometry."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import production_diagnostics as D
import editorial_cover as C


def test_tied_model_scores_do_not_choose_generic_first_by_position():
    source='买医药股要先看经营，经营不好的医药股我不买。'
    items=[dict(title='林园：坚持投资理念，医药股需要长期研究', subject='医药股', evidence=[source]),
           dict(title='林园：医药股经营不好，再便宜我也不买', subject='医药股', evidence=[source])]
    verdicts=[dict(index=i, appeal=5) for i in range(2)]
    assert T.select_reviewed_candidate(verdicts, items)['index']==1
    assert T.select_reviewed_candidate(verdicts, list(reversed(items)))['index']==0


def test_conflicting_duplicate_reviews_cannot_approve_a_candidate():
    source='医药股经营不好就不能买，买入之前要仔细了解经营情况。'
    item=dict(title='林园：医药股经营不好，再便宜也不能买',
        cover_title='医药股经营不好就不买',subject='医药股',evidence=[source])
    calls=[]
    def model(prompt):
        calls.append(prompt)
        if '独立核对' not in prompt:
            return json.dumps(dict(candidates=[item, {**item,'title':'林园：医药股买入之前，要仔细了解经营情况'},
                {**item,'title':'林园：医药股能不能买，先看经营情况'}]))
        return json.dumps(dict(reviews=[dict(index=0,appeal=5,reason='原文有明确对应的嘉宾观点，完整保留条件',
            **{k:True for k in T.CHECKS})]*3))
    try:
        result=T.generate(source, model=model)
    except ValueError:
        return
    assert result['title_rewrite']['review']['method']!='cpu_text_review'


def test_diagnostics_do_not_count_complete_tasks_as_successful_clips():
    snapshot=dict(updated_at=123, inventory=dict(verified_live=4,publishable_now=0),tasks=[
        dict(slug='a',status='failed',detail='人物 VLM 校验不可用：HTTP Error 402',signature=dict(ts=2)),
        dict(slug='b',status='failed',detail='原始素材短边 360 < 480',signature=dict(ts=2)),
        dict(slug='c',status='complete',signature=dict(ts=2)),
        dict(slug='c',status='running',signature=dict(ts=1))])
    report=D.summarize(snapshot)
    assert report['tasks']==3
    assert report['failures']==dict(service_or_timeout=1,resolution=1)
    assert report['inventory_snapshot']['verified_live']==4
    assert 'success_rate' not in report
    with pytest.raises(ValueError, match='时区'):
        D.summarize(snapshot,'2026-09-18')


def test_cover_preserves_face_and_reviewed_words_at_list_size(tmp_path):
    from PIL import Image
    try:
        font=C.font_path()
    except ValueError:
        pytest.skip('Chinese font required for actual rendering')
    image=Image.new('RGB',(1280,720),'#789798')
    face=(780,160,160,220)
    headline='医药股经营不好，便宜也不买'
    path=tmp_path/'cover.jpg'
    proof=C.render(image,path,face,headline,'林园',font)
    assert ''.join(proof['headline_lines'])==headline.replace('，','')
    assert Image.open(tmp_path/proof['thumbnail']).size==(160,90)
    assert proof['font_px']>=96 and proof['thumbnail_font_px']>=12
    f=proof['face_box']
    for box in proof['text_boxes']:
        assert box[2]<=f[0] or box[0]>=f[2] or box[3]<=f[1] or box[1]>=f[3]
    assert proof['face_fully_visible']
    with pytest.raises(ValueError, match='人脸'):
        C.portrait_crop((1280,720),(0,0,1300,700))


def test_auto_cover_reuses_verified_frame_but_explicit_scene_stays_strict(tmp_path, monkeypatch):
    from PIL import Image
    import produce_cn as P
    import presentation as V
    try:
        C.font_path()
    except ValueError:
        pytest.skip('Chinese font required')
    captures=[]
    def extract(cmd, **kwargs):
        captures.append(cmd)
        Image.new('RGB',(720,1280),'#789798').save(cmd[-1])
    monkeypatch.setattr(P.subprocess,'run',extract)
    monkeypatch.setattr(P,'detect_corner_logos_in_images',lambda _: [])
    monkeypatch.setattr(P,'select_verified_cover_face',lambda frames,ref:
        (frames[0],(230,160,180,240),dict(engine='test_identity_stub')))
    def no_scene(*args):
        raise ValueError('竖图不适合现场横版封面')
    monkeypatch.setattr(V,'save_scene_cover',no_scene)
    path=tmp_path/'cover.jpg'
    P.make_cover('source.mp4',0,120,'医药股经营不好，便宜也不买','林园',path,
                 style='scene',allow_editorial_fallback=True)
    proof=json.loads(Path(str(path)+'.proof.json').read_text())
    assert proof['style']=='editorial' and '竖图' in proof['scene_fallback_reason']
    assert len(captures)==7  # one sampling pass, no second extraction on fallback
    with pytest.raises(P.VisualQualityError,match='竖图'):
        P.make_cover('source.mp4',0,120,'医药股经营不好，便宜也不买','林园',tmp_path/'strict.jpg',style='scene')
