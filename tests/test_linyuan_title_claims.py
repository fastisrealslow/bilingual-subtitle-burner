"""The Sep 13 title must carry the argument, not three incidental keywords."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import headline_policy as H
import produce_cn as P

TEXT='龙头还没有走出来，大家看不清楚。我们就整个行业进行买断，每个投资标的把比例控制一下。把有可能成为龙头的公司都配置一些。消费和医药只是举例。'
TITLE='林园：龙头还没跑出来，为什么先买整个行业？'


def proposals():
    return [dict(title=t,cover_title=c,subject='龙头',evidence=[TEXT.split('。')[0],TEXT.split('。')[1],TEXT.split('。')[2]]) for t,c in [
        (TITLE,'龙头未定，如何布局'),
        ('林园：龙头还没分出来，仓位比例怎么控制？','龙头未定，控制仓位'),
        ('林园：看好一个行业，先买可能成为龙头的公司','龙头未定，如何买入')]]


def model(calls, bad_review=False):
    def call(prompt):
        calls.append(prompt)
        if '独立核对' not in prompt:
            return json.dumps(dict(candidates=proposals()),ensure_ascii=False)
        return json.dumps(dict(reviews=[dict(index=i,appeal=5-i,reason='原文直接支持同一观点，标题与封面未增加新结论',
            **{k: not (bad_review and k=='source_supported') for k in T.CHECKS}) for i in range(3)]))
    return call


def test_three_angles_then_independent_review_choose_central_claim_and_cover(monkeypatch):
    calls=[]
    result=T.generate(TEXT,model=model(calls))
    assert result['title']==TITLE and len(calls)==2
    assert all(unit in calls[0] for unit in T.source_units(TEXT)) and TEXT in calls[1]
    assert len(result['title_candidates'])==3
    assert result['cover_title']=='龙头未定，如何布局'
    assert not T.error(result['title'],result['title_rewrite'],TEXT)
    # FC validation must not depend on runner-only jieba/presentation libraries.
    monkeypatch.setattr(H,'cover_fits',lambda *_:pytest.fail('FC must use bound production evidence'))
    assert not T.error(result['title'],result['title_rewrite'],TEXT)


def test_old_keyword_title_and_changed_claim_cannot_reuse_review():
    result=T.generate(TEXT,model=model([]))
    proof=result['title_rewrite']
    assert T.summary_heading('林园：谈消费需求、医药投资与买入时机')
    assert T.error('林园：谈消费需求、医药投资与买入时机',dict(version=2026091301,kind='editorial_topic'))
    for key,value in [('title',TITLE+'明年翻倍'),('cover','买入这些必然翻倍'),('evidence',['龙头公司保证赚钱'])]:
        bad=deepcopy(proof);bad[key]=value
        assert T.error(bad['title'],bad,TEXT)
    assert T.error(TITLE,proof,'这里只谈医药消费，没有讨论行业配置')


def test_candidate_must_carry_complete_evidence_and_no_new_numbers():
    item=proposals()[0]
    assert not T._candidate_error(item,TEXT,'林园',())
    assert T._candidate_error({**item,'title':TITLE+'10倍'},TEXT+'100倍','林园',())
    assert T._candidate_error({**item,'evidence':['龙头医药消费']},TEXT,'林园',())
    assert T._candidate_error({**item,'subject':'茅台'},TEXT,'林园',())
    assert T._candidate_error({**item,'title':'林园：龙头形成前布局小公司更安全'},TEXT,'林园',())


def test_one_valid_candidate_cannot_skip_comparison_of_three_angles():
    calls=[]
    good=model(calls)
    def incomplete(prompt):
        reply=json.loads(good(prompt))
        for item in reply.get('candidates',[])[1:]:item['cover_title']='龙头'
        return json.dumps(reply,ensure_ascii=False)
    result=T.generate(TEXT,model=incomplete)
    assert len(calls)==3 and result['title_rewrite']['review']['method']=='source_quote'


def test_negative_semantic_review_cannot_pass_as_editorial_rewrite():
    calls=[]
    result=T.generate(TEXT,model=model(calls,bad_review=True))
    assert len(calls)==6
    assert result['title_rewrite']['review']['method']=='source_quote'
    assert result['title']!=TITLE
    assert not T.summary_heading(result['title'])


def test_semantic_rejection_reaches_rewriter_and_each_retry_has_a_new_cache_key():
    calls=[]
    T.generate(TEXT,model=model(calls,bad_review=True))
    proposals_received=calls[::2]
    assert len(proposals_received)==len(set(proposals_received))==3
    for prompt in proposals_received[1:]:
        assert TITLE in prompt
        assert 'source_supported' in prompt
        assert '原文直接支持同一观点，标题与封面未增加新结论' in prompt
        assert '先按原文修正中心观点' in prompt


def test_keywords_without_claim_are_retryable_not_source_rejection(tmp_path,monkeypatch):
    monkeypatch.setattr(P,'llm',lambda *a,**k:'{}')
    with pytest.raises(P.EditorialReviewUnavailable,match='标题文案待重试'):
        P.copywrite([dict(text='人口、消费、医药。',start=0,end=20)],[0],
                    '林园','访谈',None,tmp_path)
    assert not (tmp_path/'copywrite.json').exists()


@pytest.mark.parametrize('suffix',['','_full'])
def test_valid_title_is_cached_with_current_policy_and_same_evidence(tmp_path,monkeypatch,suffix):
    calls=[]
    callback=model(calls)
    def structured(messages,*a,**kwargs):
        assert kwargs['response_schema']['additionalProperties'] is False
        reply=json.loads(callback(messages[0]['content']))
        if reply.get('candidates'):
            reply['focus']=dict(subject='龙头',evidence_ids=list(range(len(T.source_units(TEXT)))),
                claim='行业尚未出现龙头，先配置可能成为龙头的公司并控制比例。')
        for candidate in reply.get('candidates',[]):
            candidate.pop('evidence')
            candidate.pop('subject')
        return json.dumps(reply,ensure_ascii=False)
    monkeypatch.setattr(P,'llm',structured)
    cues=[dict(text=TEXT,start=0,end=150)]
    result=P.copywrite(cues,[0],'林园','访谈',None,tmp_path,suffix=suffix,reviewed_title='林园：谈消费需求、医药投资与买入时机')
    assert result['title']==TITLE and result['copy_identity']['version']==7
    if suffix=='_full':assert '完整访谈' in result['tags'] and '完整访谈原声' in result['desc']
    monkeypatch.setattr(P,'llm',lambda *a,**k:pytest.fail('Current verified copy should be reused'))
    cached=P.copywrite(cues,[0],'林园','访谈',None,tmp_path,suffix=suffix,reviewed_title='林园：谈消费需求、医药投资与买入时机')
    assert cached['title']==TITLE and cached['title_rewrite']==result['title_rewrite']


def test_actual_caption_evidence_is_selected_by_id_and_never_retyped():
    fixture=json.loads((Path(__file__).parent/'fixtures/linyuan_0913_title.json').read_text())
    text=''.join(c['text'] for c in fixture['cues'])
    units=T.source_units(text)
    assert ''.join(units)==text and all(len(T.compact(u))>=8 for u in units)
    i=next(i for i,u in enumerate(units) if '整个行业' in u)
    item=dict(title=TITLE,cover_title='龙头未定，如何布局',subject='龙头',evidence_ids=[i])
    bound=T.bind_evidence(item,units)
    assert bound['evidence']==[units[i]] and bound['evidence'][0] in text
    for ids in [[-1],[len(units)],[True],[i,i]]:
        with pytest.raises(ValueError):T.bind_evidence({**item,'evidence_ids':ids},units)
    with pytest.raises(ValueError):T.bind_evidence({**item,'evidence':['人工补写的原文']},units)
    assert T.proposal_schema(len(units))['properties']['candidates']['minItems']==3
    assert all(T.review_schema(3)['properties']['reviews']['items']['properties'][k]['type']=='boolean' for k in T.CHECKS)


@pytest.mark.parametrize('name',['linyuan_0913_title.json','linyuan_0913_landscape_title.json'])
def test_real_source_subjects_are_exact_options_with_corresponding_evidence(name):
    fixture=json.loads((Path(__file__).parent/'fixtures'/name).read_text())
    text=''.join(c['text'] for c in fixture['cues'])
    units=T.source_units(text)
    catalog=T.subject_catalog(units)
    assert catalog
    for subject, ids in catalog.items():
        assert ids and all(subject in units[i] for i in ids)
    proposal=T.proposal_schema(len(units),catalog)['properties']
    assert 'evidence_ids' in proposal['focus']['properties']
    assert 'subject' not in proposal['candidates']['items']['properties']
    assert '未出龙头公司' not in catalog and '医药消费赛道' not in catalog


def test_source_subject_choice_does_not_approve_a_new_financial_claim():
    item=proposals()[0]
    item['title']='林园：龙头还没形成，布局整个行业更安全'
    assert T._candidate_error(item,TEXT,'林园',())=='标题新增了原文没有的安全性或收益比较结论'


def test_different_title_angles_use_exact_shared_anchors_without_invented_compounds():
    units=T.source_units(TEXT);catalog=T.subject_catalog(units)
    bound=T.bind_candidate(dict(title='林园：为什么先买整个行业，再控制每个标的比例？',
        cover_title='整个行业，怎么配置'),dict(evidence_ids=list(range(len(units)))),units,catalog)
    assert bound['subject'] in bound['title']
    assert any(bound['subject'] in q for q in bound['evidence'])
    assert not T._candidate_error(bound,TEXT,'林园',())
