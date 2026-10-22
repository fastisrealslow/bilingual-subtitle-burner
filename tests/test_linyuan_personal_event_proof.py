from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E


@pytest.mark.parametrize('text',[
    '我自己觉得是没问题。','我觉得没有问题。','嗯，我们个人认为应该没什么问题吧。',
    '所以我觉得可以。','我个人估计不会有问题啊。',
])
def test_bare_assessment_cannot_supply_actual_personal_event(text):
    assert 'self-assessment' in E.personal_event_error(text)


@pytest.mark.parametrize('text',[
    '我当时买了这家公司的股票。','因为我那时候持有长虹。',
    '我只投我看好的，我在自己这一亩三分地里耕耘。',
    '我后来才觉得当时买错了。','我吃药啊，因为我有糖尿病。',
    '我觉得可以投茅台。',
])
def test_actual_action_choice_or_specific_answer_remains_available(text):
    # Semantic/source/asr/identity/rights gates still have to judge the full
    # passage. This only proves the narrow bare-assessment rule did not fire.
    assert E.personal_event_error(text) is None


def test_delivery_proof_uses_same_empty_event_rejection_before_media_claims():
    meta=dict(editorial_profile=E.TREATMENT,cover_proof={},experiment_proof=dict(
        version=E.VERSION,profile=E.TREATMENT,source_duration_sec=1800,
        selection=dict(version=E.VERSION,kind='personal_story',subject_quote='如果重新来过呢？',
            event_quote='我自己觉得是没问题。',detail_quote='我现在回不到那个年代了。',transcript_sha256='a'*64)))
    assert 'self-assessment' in E.proof_error(meta)
