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
        assert p.pick_argument_context(cues(),[dict(start=0,end=0)],'林园','',tmp_path,'')==[]


def test_candidate_id_uses_program_duration_and_never_model_timestamps(tmp_path):
    answer={'picks':[dict(candidate_id=0,accepted=True,score=8,reason='完整上下文',
                          duration_sec=9999,start=307.4,end=343.0)]}
    with patch.object(p,'llm',return_value=json.dumps(answer)):
        picked=p.pick_argument_context(cues(),[dict(start=0,end=0)],'林园','',tmp_path,'')
    assert p.editorial.range_seconds(cues(),picked[0])==120


def test_seconds_as_indices_are_retryable_not_bad_material(tmp_path):
    reply=json.dumps(dict(start=307.4,end=343.0,score=8))
    with patch.object(p,'llm',return_value=reply):
        with pytest.raises(p.LocalTextUnavailable):
            p.pick_highlights(cues(),'林园','',tmp_path)
    assert not (tmp_path/'highlights.json').exists()
