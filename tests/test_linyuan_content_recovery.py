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
        assert '疗效' in T.error(title,package['title_rewrite'],source)
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
