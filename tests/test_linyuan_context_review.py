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
    with pytest.raises(ValueError):r.parse(json.dumps(DATA['old_response']),DATA['choices'])
    fields=r.schema(DATA['choices'])['properties']['verdicts']
    assert set(fields['required'])=={str(i) for i in range(39)}


@pytest.mark.parametrize('answer',[
    '{"verdicts":{"0":"accept_7","1":"reject_ending"}}',
    '{"verdicts":[]}',
    '{"verdicts":{"0":"accept_7","0":"reject_ending"}}',
])
def test_missing_or_duplicate_coverage_is_not_source_rejection(answer):
    with pytest.raises(ValueError):r.parse(answer,DATA['choices'])


def test_complete_rejection_preserves_every_reason():
    verdicts={str(i):'reject_mixed_topics' for i in range(39)}
    assert r.parse(json.dumps({'verdicts':verdicts}),DATA['choices'])==verdicts
    verdicts['38']='accept_0'
    with pytest.raises(ValueError):r.parse(json.dumps({'verdicts':verdicts}),DATA['choices'])


def test_real_715_candidates_can_select_a_later_id_without_timing_invention(monkeypatch,tmp_path):
    monkeypatch.setattr(p,'argument_context_candidates',lambda *a:DATA['choices'])
    verdicts={str(i):'reject_mixed_topics' for i in range(39)}
    verdicts['38']='accept_8'
    monkeypatch.setattr(p,'llm',lambda *a,**k:json.dumps({'verdicts':verdicts}))
    result=p.pick_argument_context(DATA['cues'],[dict(start=0,end=2)],'林园','',tmp_path,'')
    assert [(x['start'],x['end']) for x in result]==[(DATA['choices'][38]['start'],DATA['choices'][38]['end'])]
    assert result[0]['score']==8
    proof=json.loads((tmp_path/'context_review.json').read_text())
    assert proof['complete'] and proof['reviewed_count']==proof['candidate_count']==39


def test_malformed_later_batch_never_writes_complete_proof(monkeypatch,tmp_path):
    monkeypatch.setattr(p,'argument_context_candidates',lambda *a:DATA['choices'])
    monkeypatch.setattr(r,'BATCH_SIZE',20)
    responses=iter([json.dumps({'verdicts':{str(i):'reject_mixed_topics' for i in range(20)}}),'{"verdicts":{}}'])
    monkeypatch.setattr(p,'llm',lambda *a,**k:next(responses))
    with pytest.raises(p.SelectionIncomplete):
        p.pick_argument_context(DATA['cues'],[dict(start=0,end=2)],'林园','',tmp_path,'')
    assert not (tmp_path/'context_review.json').exists()
