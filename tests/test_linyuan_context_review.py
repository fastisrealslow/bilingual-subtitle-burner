import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import context_review as r
import produce_cn as p

DATA=json.loads((Path(__file__).parent/'fixtures/linyuan_715_selection.json').read_text())


def test_actual_715_contradictory_partial_response_cannot_be_a_verdict():
    assert len(DATA['choices'])==39
    assert all(x['accepted'] and x['score']==0 for x in DATA['old_response']['picks'])
    with pytest.raises(ValueError):r.parse(json.dumps(DATA['old_response']),DATA['choices'],DATA['cues'])
    fields=r.schema(DATA['choices'],len(DATA['cues']))['properties']['verdicts']
    assert set(fields['required'])=={str(i) for i in range(39)}


@pytest.mark.parametrize('answer',[
    '{"verdicts":{"0":"accept_7","1":"reject_ending"}}',
    '{"verdicts":[]}',
    '{"verdicts":{"0":"accept_7","0":"reject_ending"}}',
])
def test_missing_or_duplicate_coverage_is_not_source_rejection(answer):
    with pytest.raises(ValueError):r.parse(answer,DATA['choices'],DATA['cues'])


def test_complete_rejection_preserves_every_reason():
    verdicts={str(i):'reject_mixed_topics' for i in range(39)}
    assert r.parse(json.dumps({'topics':[dict(start=0,topic='模拟完整主题')],'verdicts':verdicts}),DATA['choices'],DATA['cues'])[0]==verdicts
    verdicts['38']='accept_0'
    with pytest.raises(ValueError):r.parse(json.dumps({'topics':[dict(start=0,topic='模拟完整主题')],'verdicts':verdicts}),DATA['choices'],DATA['cues'])[0]


def test_real_715_candidates_can_select_a_later_id_without_timing_invention(monkeypatch,tmp_path):
    monkeypatch.setattr(r,'reviewed_topics',lambda cues:None)
    monkeypatch.setattr(p,'argument_context_candidates',lambda *a:DATA['choices'])
    verdicts={str(i):'reject_mixed_topics' for i in range(39)}
    verdicts['38']='accept_8'
    monkeypatch.setattr(p,'llm',lambda *a,**k:json.dumps({'topics':[dict(start=0,topic='模拟完整主题')],'verdicts':verdicts}))
    result=p.pick_argument_context(DATA['cues'],[dict(start=0,end=2)],'林园','',tmp_path,'')
    assert [(x['start'],x['end']) for x in result]==[(DATA['choices'][38]['start'],DATA['choices'][38]['end'])]
    assert result[0]['score']==8
    proof=json.loads((tmp_path/'context_review.json').read_text())
    assert proof['complete'] and proof['reviewed_count']==proof['candidate_count']==39


def test_malformed_later_batch_never_writes_complete_proof(monkeypatch,tmp_path):
    monkeypatch.setattr(r,'reviewed_topics',lambda cues:None)
    monkeypatch.setattr(p,'argument_context_candidates',lambda *a:DATA['choices'])
    monkeypatch.setattr(r,'BATCH_SIZE',20)
    responses=iter([json.dumps({'topics':[dict(start=0,topic='模拟完整主题')],'verdicts':{str(i):'reject_mixed_topics' for i in range(20)}}),'{"verdicts":{}}'])
    monkeypatch.setattr(p,'llm',lambda *a,**k:next(responses))
    with pytest.raises(p.SelectionIncomplete):
        p.pick_argument_context(DATA['cues'],[dict(start=0,end=2)],'林园','',tmp_path,'')
    assert not (tmp_path/'context_review.json').exists()


def test_actual_715_topic_change_blocks_all_39_ranges_even_with_high_scores():
    topics=[dict(start=0,end=26,topic='资本市场投资回报'),
            dict(start=27,end=61,topic='无风险套利与恒大债券')]
    original={str(i):'accept_10' for i in range(39)}
    effective,returned,raw=r.parse(json.dumps(dict(topics=[{k:v for k,v in t.items() if k!='end'} for t in topics],verdicts=original)),DATA['choices'],DATA['cues'])
    assert set(effective.values())=={'reject_mixed_topics'}
    assert raw==original and returned==topics
    # Neither natural topic is 120 seconds; do not merge them to meet duration.
    assert all(DATA['cues'][t['end']]['end']-DATA['cues'][t['start']]['start']<120 for t in topics)


@pytest.mark.parametrize('topics',[
    [dict(start=1,topic='漏掉开头')],
    [dict(start=0,topic='第一段'),dict(start=0,topic='重复')],
    [dict(start=0,topic='第一段'),dict(start=26,topic='切断原句')],
])
def test_topic_starts_must_cover_opening_and_use_sentence_boundaries(topics):
    with pytest.raises(ValueError):
        r.parse(json.dumps(dict(topics=topics,verdicts={str(i):'accept_7' for i in range(39)})),DATA['choices'],DATA['cues'])


def test_topic_ends_are_derived_so_tail_cannot_be_omitted():
    _,topics,_=r.parse(json.dumps(dict(topics=[dict(start=0,topic='第一话题'),dict(start=27,topic='第二话题')],verdicts={str(i):'accept_7' for i in range(39)})),DATA['choices'],DATA['cues'])
    assert [(t['start'],t['end']) for t in topics]==[(0,26),(27,61)]


def test_reviewed_715_blocks_mixed_ranges_without_model_call(monkeypatch,tmp_path):
    monkeypatch.setattr(p,'argument_context_candidates',lambda *a:DATA['choices'])
    def no_model(*a,**kw):raise AssertionError('Known mixed-topic candidates must not call the model')
    monkeypatch.setattr(p,'llm',no_model)
    assert p.pick_argument_context(DATA['cues'],[dict(start=0,end=2)],'林园','',tmp_path,'')==[]
    proof=json.loads((tmp_path/'context_review.json').read_text())
    assert proof['complete'] and proof['reviewed_count']==39
    assert set(proof['verdicts'].values())=={'reject_mixed_topics'}


def test_reviewed_boundaries_never_match_changed_transcript_or_timing():
    import copy
    assert r.reviewed_topics(DATA['cues'])
    for field,value in [('text','changed'),('start',0.33)]:
        cues=copy.deepcopy(DATA['cues']);cues[0][field]=value
        assert r.reviewed_topics(cues) is None
