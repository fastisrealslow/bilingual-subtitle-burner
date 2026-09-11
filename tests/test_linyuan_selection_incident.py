"""Regression coverage for production failures #612-615."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p


def cues():
    return [dict(start=i*30, end=(i+1)*30, text='同一观点的完整解释。') for i in range(10)]


def test_incident_object_and_envelope_normalize_without_trusting_duration():
    row=dict(candidate_id=9, duration_sec=213.36, score=9, reason='医药股投资策略')
    assert p.parse_llm_json_array(json.dumps(row)) == [row]
    assert p.parse_llm_json_array(json.dumps({'picks':[row]})) == [row]
    assert p.parse_llm_json_array('{"picks":[]}') == []
    # Actual #612 output used seconds as indices and claimed 120s for 35.6s.
    short=dict(start=307.40,end=343.00,score=7,reason='时长120秒')
    parsed=p.parse_llm_json_array(json.dumps(short))[0]
    with pytest.raises(ValueError,match='字幕序号'):
        p.editorial.range_seconds(cues(),parsed)


def test_bad_first_pick_does_not_discard_good_second_pick(tmp_path):
    bad=dict(start=0,end=0,score=8,reason='短句')
    good=dict(start=1,end=5,score=8,reason='完整论述')
    with patch.object(p,'llm',return_value=json.dumps({'picks':[bad,good]})) as ask:
        assert p.pick_highlights(cues(),'林园','',tmp_path) == [good]
    assert ask.call_count == 1
    schema=ask.call_args.kwargs['response_schema']
    assert schema['properties']['picks']['items']['properties']['start']['type']=='integer'


@pytest.mark.parametrize('answer', ['{"candidate_id":0,"reason":"unterminated',
    '{"candidate_id":99999,"accepted":true,"score":8}',
    '{"candidate_id":0,"score":8}', '[null]'])
def test_fallback_runtime_errors_never_cache_source_rejection(tmp_path,answer):
    replies=['[{"start":0,"end":0,"score":8}]']*2+[answer]
    with patch.object(p,'llm',side_effect=replies):
        with pytest.raises(p.LocalTextUnavailable):
            p.pick_highlights(cues(),'林园','',tmp_path)
    assert not (tmp_path/'highlights.json').exists()


def test_explicit_rejection_cannot_be_overridden_by_high_score(tmp_path):
    answer={'picks':[dict(candidate_id=0,accepted=False,score=9,reason='结尾残句')]}
    with patch.object(p,'llm',return_value=json.dumps(answer)):
        with pytest.raises(p.SelectionIncomplete,match='不判整源'):
            p.pick_argument_context(cues(),[dict(start=0,end=0)],'林园','',tmp_path,'')


def test_actual_690_partial_rejections_never_cache_a_terminal_verdict(tmp_path):
    # The CPU model in #690 rejected only IDs 0 and 1, not the whole table.
    partial=json.dumps({'picks':[dict(candidate_id=i,accepted=False,score=0,
        reason='开头缺上下文') for i in (0,1)]})
    short=json.dumps({'picks':[dict(start=0,end=0,score=8)]})
    with patch.object(p,'llm',side_effect=[short,short,partial]):
        with pytest.raises(p.SelectionIncomplete):
            p.pick_highlights(cues(),'林园','',tmp_path)
    assert not (tmp_path/'highlights.json').exists()
    assert len(json.loads((tmp_path/'context_candidates.json').read_text()))>2


def test_actual_690_context_keeps_full_sentences_and_127_second_answer():
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_690_selection.json').read_text())
    rows=data['cues']
    # A short model seed must not force a 120-second cut or drop the conclusion.
    choices=p.argument_context_candidates(rows,[dict(start=117,end=139)])
    expected=dict(start=117,end=161)
    assert any(all(x[k]==v for k,v in expected.items()) for x in choices)
    from source_selection import sentence_units,boundary_error
    units=sentence_units(rows)
    starts={u['start'] for u in units};ends={u['end'] for u in units}
    assert choices
    for x in choices:
        assert x['start'] in starts and x['end'] in ends
        assert 120<=p.editorial.range_seconds(rows,x)<=330
        assert boundary_error(rows,x) is None


def test_690_text_edit_is_source_bound_and_has_no_fabricated_media_approval():
    from curated_editorial import source_ranges
    data=json.loads((Path(__file__).parent/'fixtures/linyuan_690_selection.json').read_text())
    ranges=source_ranges(data['cues'],data['source_sha256'])
    assert [(a,b) for a,b,_ in ranges]==[(117,161)]
    pick=ranges[0][2][0]
    assert pick.get('editorial_review') is None
    assert p.editorial.range_seconds(data['cues'][117:162],pick)==pytest.approx(127.2)


def test_candidate_id_uses_program_duration_and_never_model_timestamps(tmp_path):
    choices=p.argument_context_candidates(cues(),[dict(start=0,end=0)])
    answer={'topics':[dict(start=0,topic='完整解释')],'verdicts':{str(c['candidate_id']):'reject_low_value' for c in choices}}
    answer['verdicts']['0']='accept_8'
    with patch.object(p,'llm',return_value=json.dumps(answer)):
        picked=p.pick_argument_context(cues(),[dict(start=0,end=0)],'林园','',tmp_path,'')
    assert p.editorial.range_seconds(cues(),picked[0])==120


def test_seconds_as_indices_are_retryable_not_bad_material(tmp_path):
    reply=json.dumps(dict(start=307.4,end=343.0,score=8))
    with patch.object(p,'llm',return_value=reply):
        with pytest.raises(p.LocalTextUnavailable):
            p.pick_highlights(cues(),'林园','',tmp_path)
    assert not (tmp_path/'highlights.json').exists()
