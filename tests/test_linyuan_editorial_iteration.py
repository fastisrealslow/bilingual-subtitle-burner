"""Regressions for partial candidate batches, ordering, and cover geometry."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import production_diagnostics as D
import editorial_cover as C


@pytest.mark.parametrize('size',[(1280,634),(720,1280),(1280,720)])
def test_footer_subtitles_stay_outside_source_and_ass_uses_same_canvas(tmp_path,size):
    import presentation as V
    import produce_cn as P
    w,h=size
    layout=V.footer_layout_for(w,h)
    region=layout['subtitle_region']
    assert region['y']>=h and region['y']+region['height']<=layout['canvas']['height']
    assert layout['source_region']==dict(x=0,y=0,width=w,height=h)
    assert region['height']>=2*layout['subtitle_font_px']*1.448
    assert V.footer_filter(layout).startswith(f'pad={w}:{layout["canvas"]["height"]}:0:0:')
    path=tmp_path/'footer.ass'
    entries=[dict(start_sec=0,end_sec=3,zh='医药股经营不好就不能买入',en='')]
    prepared=P.make_ass(entries,path,w,h,layout_override=layout)
    text=path.read_text(encoding='utf-8-sig')
    assert f'PlayResY: {layout["canvas"]["height"]}' in text
    assert f'pos({w//2},{region["y"]+region["height"]//2})' in text
    assert ''.join(prepared[0]['lines'])==entries[0]['zh']


def test_lookup_of_fund_value_does_not_support_reassurance_about_drawdown():
    source='投资者自己知道净值，不知道也可以到网上去查阅。'
    item=dict(title='林园：产品净值有回撤，但投资者自己能查，不用怕',
        cover_title='产品净值，投资者自己能查',subject='净值',evidence=[source])
    assert '没有' in T._candidate_error(item,source,'林园',(),check_layout=False)


def test_quote_fallback_keeps_guest_attribution_and_full_source_fingerprint():
    host='消费医药现在最值得买入，我们看好长期机会。'
    guest='医药股经营不好，我不会买入。'
    units=[host,guest]
    def model(prompt,schema):
        if 'c_guest_spans' in schema.get('properties',{}):
            return json.dumps(dict(a_guest_answer='嘉宾明确说经营不好的医药股不会买入。',
                b_question_premise='主持人建议买入消费医药，未获嘉宾确认。',
                c_guest_spans=[dict(a_start=1,b_end=1)]))
        return '{}'
    result=T.generate(''.join(units),structured_model=model,source_cues=units,
                      preferred='林园：'+host.rstrip('。'))
    assert result['title_rewrite']['review']['method']=='source_quote'
    assert result['title_rewrite']['review']['attribution']=='reader_guest_passage'
    assert T.compact(result['title'].split('：')[1]) in T.compact(guest)
    assert not T.error(result['title'],result['title_rewrite'],''.join(units))


def test_quote_fallback_cannot_join_across_host_turn(monkeypatch):
    import headline_policy as H
    units=['医药股经营不好，','主持人的另一个问题。','我是不会买的。']
    # Even a preferred/cache candidate cannot bridge two distinct guest turns.
    quote='医药股经营不好，我是不会买的'
    monkeypatch.setattr(H,'title_candidates',lambda *a: ['林园：'+quote])
    with pytest.raises(ValueError,match='未提炼'):
        T._extractive(''.join(units),'林园',(),guest_passages=[units[0],units[2]])


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
        (frames[0],(230,160,180,240),dict(engine='test_identity_stub',sharpness=100)))
    def no_scene(*args):
        raise ValueError('竖图不适合现场横版封面')
    monkeypatch.setattr(V,'save_scene_cover',no_scene)
    path=tmp_path/'cover.jpg'
    P.make_cover('source.mp4',0,120,'医药股经营不好，便宜也不买','林园',path,
                 style='scene',allow_editorial_fallback=True)
    proof=json.loads(Path(str(path)+'.proof.json').read_text())
    assert proof['style']=='editorial' and '竖图' in proof['scene_fallback_reason']
    assert proof['source_identity']['sharpness']==100
    assert len(captures)==7  # one sampling pass, no second extraction on fallback
    with pytest.raises(P.VisualQualityError,match='竖图'):
        P.make_cover('source.mp4',0,120,'医药股经营不好，便宜也不买','林园',tmp_path/'strict.jpg',style='scene')
    monkeypatch.setattr(P,'select_verified_cover_face',lambda frames,ref:
        (frames[0],(230,160,180,240),dict(engine='test_identity_stub',sharpness=10)))
    with pytest.raises(P.VisualQualityError,match='清晰度不足'):
        P.make_cover('source.mp4',0,120,'医药股经营不好，便宜也不买','林园',tmp_path/'blur.jpg',
                     style='scene',allow_editorial_fallback=True)
