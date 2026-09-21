"""Real title58/unit and source46/recap failures, with unchanged raw evidence."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import title_rewrite as T
import source_selection as S


@pytest.mark.parametrize('source,wrong', [('十二个月后可能突破。','十二月后市场突破'),('12个月后可能突破。','12月后市场突破')])
def test_duration_cannot_become_calendar_month(source,wrong):
    assert '日历' in T.forecast_copy_error('林园：'+wrong,wrong,source)
    assert T.forecast_copy_error('林园：十二月市场表现','十二月市场表现','十二月市场表现。十二个月后另有机会。') is None


def test_actual_point_forecast_cover_cannot_drop_qualifier_or_antecedent():
    source='这个点位很多年一直难突破。它进入牛市这个时间我不好预测。我个人判断十二个月可能性非常大。'
    assert '不确定性' in T.forecast_copy_error('林园：十二个月后市场可能突破','十二个月后市场突破',source)
    assert '指代' in T.forecast_copy_error('林园：十二个月后可能到这个点','牛市何时到来不好预测',source)
    assert T.forecast_copy_error('林园：牛市启动时间我不好预测','牛市何时到来不好预测',source) is None
    assert '具体事件' in T.forecast_copy_error('林园：十二个月可能性大，但得耐心等','十二个月可能性大',source)


def test_standalone_product_subject_is_not_forced_to_list_all_disease_names():
    assert T.unresolved_subject_error('林园：我们看好的不是治疗这三种病的药物','我看好的不是这三种病的药物')
    assert T.unresolved_subject_error('林园：看好三种病的并发症相关产品','我看好的是并发症相关产品') is None


def test_actual_34_efficacy_and_valuation_inventions_fail_even_with_positive_review():
    case=json.loads((Path(__file__).resolve().parents[1]/'linyuan/simulations/benchmark-20260921/content-stage-corpus.json').read_text())[2]
    source=''.join(c['text'] for c in case['cues'])
    evidence=[''.join(c['text'] for c in case['cues'][7:])]
    for title in ('林园：空间估值在百倍到五百倍之间，但药物产品效果不确定',
                  '林园：市场空间很大，但药物疗效还需验证'):
        item=dict(title=title,cover_title='药物产品效果还需验证',subject='药物',evidence=evidence)
        package=T._package(item,source,dict(method='cpu_text_review',appeal=5,
            reason='实际模型只核对前半句就错误放行了未被原文支持的后半句',**{k:True for k in T.CHECKS}),[item])
        assert T.error(title,package['title_rewrite'],source)
        assert '疗效' in T.unsupported_hedge_error(title,item['cover_title'],evidence)
    assert '估值' in T.unsupported_hedge_error('空间估值在百倍到五百倍之间','市场空间很大',evidence)
    assert T.unsupported_hedge_error('药物疗效还需验证','药物疗效还需验证',['这些药物的效果还没有确定，仍然需要验证。']) is None


def test_extractive_fallback_cannot_bypass_missing_subject_guard(monkeypatch):
    import headline_policy as H
    quote='要有时间，不排除十二个月'
    monkeypatch.setattr(H,'title_candidates',lambda *_:['林园：'+quote])
    monkeypatch.setattr(H,'cover_copy',lambda *_:{'text':quote})
    with pytest.raises(ValueError):T._extractive(quote,'林园',())
    item=dict(title='林园：'+quote,cover_title=quote,subject=quote,evidence=[quote])
    package=T._package(item,quote,dict(method='source_quote',quote=quote),[item])
    assert '对象' in T.error(item['title'],package['title_rewrite'],quote)


def test_actual_34_subject_catalog_keeps_complication_term_and_positive_answer():
    case=json.loads((Path(__file__).resolve().parents[1]/'linyuan/simulations/benchmark-20260921/content-stage-corpus.json').read_text())[2]
    units=[c['text'] for c in case['cues']]
    ids=T.guest_evidence_ids(units,['unknown']*7+['guest']*5)
    subjects={w:[i for i in locations if i in ids] for w,locations in T.subject_catalog(units).items()}
    assert subjects['并发症']==[10]
    source=''.join(units)
    assert T.product_contrast_error('林园：这类药物空间在百倍到五百倍之间','药物空间百倍到五百倍',source)
    assert T.product_contrast_error('林园：药物空间在百倍到五百倍，但重点在并发症治疗','药物空间大，但看并发症',source)
    assert T.product_contrast_error('林园：看好的不是三大病药物，而是并发症产品','看好并发症相关产品',source) is None
    assert T.product_contrast_error('林园：我们看好的是并发症相关产品','看好的是并发症相关产品',source) is None
    assert T.product_contrast_error('林园：看好药物市场的需求空间','看好药物市场需求','我们看好药物市场需求，并发症也值得研究。') is None


def test_actual_source46_ends_before_explicit_recap_without_changing_cues(monkeypatch):
    record=json.loads((Path(__file__).parent/'fixtures/linyuan_source46_summary_boundary.json').read_text())
    cues=record['cues'];before=deepcopy(cues)
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    assert '总结章节' in S.boundary_error(cues,dict(start=0,end=len(cues)-1))
    found=S.select(cues,limit=20,whole_source=True)
    assert any(cues[x['end']]['end']==record['previous_answer_end'] for x in found)
    assert all(cues[x['end']]['end']<=record['previous_answer_end'] for x in found)
    assert cues==before


def test_guest_can_have_a_complete_independent_recap(monkeypatch):
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    cues=[dict(start=0,end=15,text='总结下几个关键词：我先选择能够看明白的公司。'),
          dict(start=15,end=36,text='我更看重持续的需求，这是我选择企业的办法。')]
    assert S.boundary_error(cues,dict(start=0,end=1)) is None
    assert S.select(cues,whole_source=True)


def test_incremental_investment_is_not_no_cost_profit():
    source=['它不需要再去投资，利润扩大不需要再去花钱来产生利润。']
    assert T.incremental_cost_error('林园：我投的是不花钱也能赚的生意','不花钱也能赚',source)
    assert T.incremental_cost_error('林园：这种企业利润扩大不用追加投入','利润扩大不用追加投入',source) is None


def test_missing_forecast_event_rereads_full_dialogue_without_forcing_guest_roles():
    units=['您预计什么时候可以到达这个点位？',
           '进入牛市的时间我不好预测。',
           '我个人判断十二个月可能性很大，但仍然需要时间。']
    reading_calls=[];drafts=[]
    def model(prompt,schema):
        props=schema['properties']
        if 'c_guest_spans' in props:
            reading_calls.append(prompt)
            if len(reading_calls)==2:
                assert '重新通读完整问答' in prompt
                assert '但得耐心等' not in prompt
            return json.dumps(dict(a_guest_answer='个人认为十二个月有可能，但具体时间不好预测。',
                b_question_premise='主持人询问到达点位的时间。',
                c_guest_spans=[dict(a_start=2 if len(reading_calls)==1 else 1,b_end=2)]),ensure_ascii=False)
        if 'c_candidates' in props:
            drafts.append(prompt)
            if len(drafts)==1:
                title='林园：十二个月可能性大，但得耐心等';cover='十二个月可能性很大';ids=[2]
            else:
                title='林园：牛市启动的时间我不好预测';cover='牛市启动时间不好预测';ids=[1]
            return json.dumps(dict(b_focus=dict(a_claim='牛市的具体启动时间不好预测，只是个人判断。',b_evidence_ids=ids),
                c_candidates=[dict(title=title,cover_title=cover)]*3),ensure_ascii=False)
        return json.dumps(dict(reviews=[dict(a_analysis=dict(a_guest_answer='嘉宾明确表示牛市的具体启动时间不好预测。',
            b_question_premise='主持人询问到达点位的时间。',c_reason='标题和封面均保留原文具体事件及不好预测的限定。'),
            b_verdict=dict(index=0,appeal=4,**{k:True for k in T.CHECKS}))]),ensure_ascii=False)
    result=T.generate(''.join(units),source_cues=units,structured_model=model)
    assert len(reading_calls)==2 and len(drafts)==2
    assert result['title']=='林园：牛市启动的时间我不好预测'
    assert T.error(result['title'],result['title_rewrite'],''.join(units)) is None


def test_short_source_opening_budget_uses_time_not_asr_sentence_count(monkeypatch):
    records=json.loads((Path(__file__).parent/'fixtures/linyuan_short_opening_budget_20260921.json').read_text())
    monkeypatch.setattr(S.editorial,'CONTENT_POLICY','reference_v1')
    monkeypatch.setattr(S.editorial,'MIN_SECONDS',20)
    for row in records:
        cues=row['cues'];original=deepcopy(cues);units=S.sentence_units(cues)
        picks=S.select(cues,whole_source=True)
        assert len(picks)==1 and picks[0]['start']==units[3]['start']
        assert picks[0]['end']==len(cues)-1 and cues==original
        # The same later sentence must not rescue a source whose missing
        # context/preamble lasts longer than the unchanged 20-second budget.
        late=deepcopy(cues)
        for cue in late[units[3]['start']:]:cue['start']+=25;cue['end']+=25
        assert S.select(late,whole_source=True)==[]
