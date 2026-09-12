import sys,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import produce_cn as P
import headline_policy as H
import stage_context as S


def test_bad_quote_becomes_source_grounded_new_title_and_matching_cover(tmp_path,monkeypatch):
    text='我们做实业要注意人口变化。人口减少会影响消费，消费需求也要考虑。'
    monkeypatch.setattr(P,'llm',lambda *a,**k:'{"title":"林园：人少了没办法，它只消费少"}')
    result=P.copywrite([dict(start=0,end=20,text=text)],[0],'林园','2016年演讲',None,tmp_path,
                      reviewed_title='林园：人少了没办法，它只消费少')
    assert result['title']!='林园：人少了没办法，它只消费少'
    assert result['title_rewrite']['kind']=='editorial_topic'
    assert result['cover_title']==result['title_rewrite']['cover']
    assert not T.error(result['title'],result['title_rewrite'],text)
    monkeypatch.setattr(P,'llm',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Cache should be reusable')))
    cached=P.copywrite([dict(start=0,end=20,text=text)],[0],'林园','2016年演讲',None,tmp_path,
                      reviewed_title='林园：人少了没办法，它只消费少')
    assert cached['title']==result['title']


def test_rewritten_title_cannot_introduce_an_unspoken_subject_or_forecast():
    result=T.generate('人口与消费。人口消费实业。')
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
