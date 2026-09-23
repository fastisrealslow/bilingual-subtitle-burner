import sys,copy,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import produce_cn as P
import headline_policy as H
import stage_context as S


def test_bad_quote_becomes_source_grounded_new_title_and_matching_cover(tmp_path,monkeypatch):
    text='我们做实业要注意人口变化。人口减少会影响消费，消费需求也要考虑。'
    def reader_then_bad_draft(*a, **kwargs):
        if 'c_guest_spans' in kwargs.get('response_schema',{}).get('properties',{}):
            return json.dumps(dict(a_guest_answer='人口变化会影响消费需求，做实业要考虑人口变化。',
                b_question_premise='无主持人提问。',c_guest_spans=[dict(a_start=0,b_end=0)]))
        return '{"title":"林园：人少了没办法，它只消费少"}'
    monkeypatch.setattr(P,'llm',reader_then_bad_draft)
    result=P.copywrite([dict(start=0,end=20,text=text)],[0],'林园','2016年演讲',None,tmp_path,
                      reviewed_title='林园：人少了没办法，它只消费少')
    assert result['title']!='林园：人少了没办法，它只消费少'
    assert result['title_rewrite']['kind']=='editorial_claim'
    assert result['cover_title']==result['title_rewrite']['cover']
    assert not T.error(result['title'],result['title_rewrite'],text)
    monkeypatch.setattr(P,'llm',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Cache should be reusable')))
    cached=P.copywrite([dict(start=0,end=20,text=text)],[0],'林园','2016年演讲',None,tmp_path,
                      reviewed_title='林园：人少了没办法，它只消费少')
    assert cached['title']==result['title']


def test_rewritten_title_cannot_introduce_an_unspoken_subject_or_forecast():
    result=T.generate('人口减少会影响消费，消费需求也要考虑。')
    proof=result['title_rewrite']
    assert T.error(result['title'],proof,'这段只有医药话题')
    assert T.error(result['title']+'明年翻倍',proof,'人口消费实业')


def test_contextual_identity_cannot_claim_biometric_verification():
    proof=dict(version=S.VERSION,passed=True,identity_basis='single_live_presenter_with_matching_stage_portrait',
        biometric_presenter_match=False,source_frames_preserved=True,presenter_presence=[dict(present=True)]*6,
        presenter=dict(motion=dict(passed=True)),stage_portrait=dict(identity_score=.6,motion=dict(moving_by_third=[0,0,0])))
    assert not S.proof_error(proof)
    assert S.proof_error({**proof,'biometric_presenter_match':True})
    wrong=copy.deepcopy(proof);wrong['presenter']['motion']['passed']=False
    assert S.proof_error(wrong)


def test_stage_crop_preserves_actual_68_and_avoids_315_top_overlay():
    # Existing 640x480 source68 remains a wide context, never a face close-up.
    actor=[86.6155,264.9849,15.2261,17.9485]
    photo=[250.1831,215.0773,25.47,32.2599]
    assert S.stage_crop(640,480,actor,photo)==[0,96,640,296]
    # Library315's measured top-left overlay ends at 28.33% of source height.
    source_actor=[260,590,40,45];source_photo=[640,475,75,85]
    crop=S.stage_crop(1920,1080,source_actor,source_photo,[(.04,.05,.09,.2833333333333333)])
    assert crop==[0,314,1920,570]
    assert S.stage_crop(1920,1080,[260,320,40,45],source_photo,[(.04,.05,.09,.32)]) is None
    assert S.stage_crop(1920,1080,source_actor,[640,260,75,85],[(.04,.05,.09,.32)]) is None
