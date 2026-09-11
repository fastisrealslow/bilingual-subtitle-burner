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
    assert r.parse(json.dumps({'topics':[dict(start=0,end=len(DATA['cues'])-1,topic='模拟完整主题')],'verdicts':verdicts}),DATA['choices'],DATA['cues'])[0]==verdicts
    verdicts['38']='accept_0'
    with pytest.raises(ValueError):r.parse(json.dumps({'topics':[dict(start=0,end=len(DATA['cues'])-1,topic='模拟完整主题')],'verdicts':verdicts}),DATA['choices'],DATA['cues'])[0]


def test_real_715_candidates_can_select_a_later_id_without_timing_invention(monkeypatch,tmp_path):
    monkeypatch.setattr(p,'argument_context_candidates',lambda *a:DATA['choices'])
    verdicts={str(i):'reject_mixed_topics' for i in range(39)}
    verdicts['38']='accept_8'
    monkeypatch.setattr(p,'llm',lambda *a,**k:json.dumps({'topics':[dict(start=0,end=len(DATA['cues'])-1,topic='模拟完整主题')],'verdicts':verdicts}))
    result=p.pick_argument_context(DATA['cues'],[dict(start=0,end=2)],'林园','',tmp_path,'')
    assert [(x['start'],x['end']) for x in result]==[(DATA['choices'][38]['start'],DATA['choices'][38]['end'])]
    assert result[0]['score']==8
    proof=json.loads((tmp_path/'context_review.json').read_text())
    assert proof['complete'] and proof['reviewed_count']==proof['candidate_count']==39


def test_malformed_later_batch_never_writes_complete_proof(monkeypatch,tmp_path):
    monkeypatch.setattr(p,'argument_context_candidates',lambda *a:DATA['choices'])
    monkeypatch.setattr(r,'BATCH_SIZE',20)
    responses=iter([json.dumps({'topics':[dict(start=0,end=len(DATA['cues'])-1,topic='模拟完整主题')],'verdicts':{str(i):'reject_mixed_topics' for i in range(20)}}),'{"verdicts":{}}'])
    monkeypatch.setattr(p,'llm',lambda *a,**k:next(responses))
    with pytest.raises(p.SelectionIncomplete):
        p.pick_argument_context(DATA['cues'],[dict(start=0,end=2)],'林园','',tmp_path,'')
    assert not (tmp_path/'context_review.json').exists()


def test_actual_715_topic_change_blocks_all_39_ranges_even_with_high_scores():
    topics=[dict(start=0,end=26,topic='资本市场投资回报'),
            dict(start=27,end=61,topic='无风险套利与恒大债券')]
    original={str(i):'accept_10' for i in range(39)}
    effective,returned,raw=r.parse(json.dumps(dict(topics=topics,verdicts=original)),DATA['choices'],DATA['cues'])
    assert set(effective.values())=={'reject_mixed_topics'}
    assert raw==original and returned==topics
    # Neither natural topic is 120 seconds; do not merge them to meet duration.
    assert all(DATA['cues'][t['end']]['end']-DATA['cues'][t['start']]['start']<120 for t in topics)


@pytest.mark.parametrize('topics',[
    [dict(start=0,end=26,topic='漏掉后半段')],
    [dict(start=0,end=26,topic='第一段'),dict(start=26,end=61,topic='重叠')],
    [dict(start=0,end=25,topic='切断原句'),dict(start=26,end=61,topic='第二段')],
])
def test_topic_coverage_must_be_complete_and_use_sentence_boundaries(topics):
    with pytest.raises(ValueError):
        r.parse(json.dumps(dict(topics=topics,verdicts={str(i):'accept_7' for i in range(39)})),DATA['choices'],DATA['cues'])
