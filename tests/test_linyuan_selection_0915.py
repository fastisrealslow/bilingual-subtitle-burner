"""Replay actual source boundaries, model failures and attribution mistakes."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linyuan'))
import produce_cn as p
from source_selection import select, boundary_error, OUTRO


def fixture(number):
    return json.loads((Path(__file__).parent / f'fixtures/linyuan_{number}_source_selection.json').read_text())


def test_real_891_opening_does_not_erase_later_questions():
    cues=fixture(891)['sections']['opening']
    assert not OUTRO.search('进入我们今天的访谈对话环节，有请林总和我们的投资人打声招呼吧。')
    picks=select(cues)
    assert [(x['start'],x['end']) for x in picks]==[(27,65)]
    assert p.editorial.range_seconds(cues,picks[0])==pytest.approx(142.64)


def test_real_891_multisentence_host_summary_is_removed():
    cues=fixture(891)['sections']['host_summary']
    pick=select(cues)[0]
    assert (pick['start'],pick['end'])==(0,35)
    assert p.editorial.range_seconds(cues,pick)==pytest.approx(122.08)
    assert '好的，林总也提到了' not in ''.join(c['text'] for c in cues[:pick['end']+1])


def test_real_891_new_investor_question_cannot_extend_short_leverage_answer():
    cues=fixture(891)['sections']['investor_question']
    # Old regex missed “也有投资者问到…林总说”, joining two independent questions.
    assert select(cues)==[]


def test_real_891_medical_answer_survives_without_host_summary():
    cues=fixture(891)['sections']['medical_answer']
    pick=select(cues)[0]
    assert (pick['start'],pick['end'])==(0,48)
    assert p.editorial.range_seconds(cues,pick)==pytest.approx(161.04)


def test_real_891_outro_cannot_make_a_short_answer_publishable():
    cues=fixture(891)['sections']['outro']
    assert select(cues,whole_source=True)==[]
    assert boundary_error(cues,dict(start=0,end=len(cues)-1))


def test_real_902_attribution_fails_before_cached_face_approval(tmp_path):
    data=fixture(902)
    transcript=''.join(c['text'] for c in data['cues'])
    assert '我们借这段话' in transcript and '我对林元这段话的理解' in transcript
    with patch.object(p,'_file_sha256',return_value=data['source_sha256']), \
            patch.object(p,'verified_source_evidence') as cache:
        report=p.run_source_quality_gate(Path('source.mp4'),tmp_path,'林园','')
        assert report['passed'] is False and report['retryable'] is False
        assert '第三方' in report['reason']
        cache.assert_not_called()
        with pytest.raises(p.VisualQualityError,match='第三方'):
            p.load_source_quality_report(Path('source.mp4'),tmp_path/'source_quality.json')
    assert '第三方' in p.editorial.metadata_error(dict(speaker='林园',source_sha256=data['source_sha256']))


def test_production_no_candidate_never_reenters_disabled_model_review(tmp_path,monkeypatch):
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    cues=[dict(start=i*40,end=(i+1)*40,text='企业需要持续增长的需求。') for i in range(10)]
    # A mechanical chunk end is not a source ending. Repeating a model request
    # cannot create the missing source boundary; this is not a service outage.
    with patch.object(p,'llm',side_effect=AssertionError('disabled review called')), \
            patch.object(p,'pick_argument_context',side_effect=AssertionError('topic map called')):
        assert p.pick_highlights(cues,'林园','',tmp_path)==[]
        assert p.pick_highlights(cues,'林园','',tmp_path)==[]


def test_stale_positive_cache_does_not_preserve_bad_outro(tmp_path,monkeypatch):
    monkeypatch.setenv('SOURCE_EDITORIAL_FIRST','true')
    cues=fixture(891)['sections']['outro']
    (tmp_path/'highlights.json').write_text(json.dumps(dict(
        identity=dict(editorial=p.editorial.plan_identity(cues,p.TARGET_SEC),selector_version=15),
        picks=[dict(start=0,end=len(cues)-1,score=9)])))
    with patch.object(p,'llm',side_effect=AssertionError('model called')):
        assert p.pick_highlights(cues,'林园','',tmp_path)==[]


def test_short_original_speech_keeps_all_conditions_and_only_natural_source_end():
    cues=[dict(start=0,end=40,text='企业的长期增长取决于持续的需求。'),
          dict(start=40,end=95,text='如果需求下降，即使估值低也不能只看价格。'),
          dict(start=95,end=145,text='所以投资时应同时关注企业需求和投资风险。')]
    before=json.dumps(cues,ensure_ascii=False)
    assert select(cues)==[]
    assert [(p['start'],p['end']) for p in select(cues,whole_source=True)]==[(0,2)]
    assert json.dumps(cues,ensure_ascii=False)==before
    cues[-1]['text']='所以投资时应同时关注'
    assert select(cues,whole_source=True)==[]


def test_short_speech_cannot_use_next_question_for_duration():
    cues=[dict(start=0,end=110,text='企业的长期增长取决于持续的需求。'),
          dict(start=110,end=145,text='您对科技股怎么看？')]
    assert select(cues,whole_source=True)==[]
