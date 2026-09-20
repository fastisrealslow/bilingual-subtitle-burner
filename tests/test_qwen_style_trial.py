import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('trial', Path(__file__).resolve().parents[1] / 'scripts/qwen_style_trial.py')
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def test_style_score_cannot_override_false_attribution_or_missing_qualifier():
    drafts = [dict(title='林园：医药股也有差公司，经营不好就别买', cover='医药股也要看经营好坏')] * 3
    reviews = [dict(index=i, a_reason='原文确实讨论医药公司经营情况，必须正确归属并保留条件',
                    **{k:True for k in trial.FACT_CHECKS}, natural=True, style_fit=5) for i in range(3)]
    reviews[0]['speaker_correct'] = False
    reviews[1]['qualifiers_preserved'] = False
    reviews[2]['style_fit'] = 3
    assert trial.select(drafts, reviews, '医药股有经营不好的，你不要买') == 2
    assert trial.select(drafts, reviews[:2], '医药股有经营不好的，你不要买') is None


def test_duplicate_verdict_cannot_approve_title():
    draft = dict(title='林园：医药股也有差公司，经营不好就别买', cover='医药股也要看经营好坏')
    review = dict(index=0, a_reason='原文的明确判断与标题一致，没有新增事实',
                  **{k:True for k in trial.FACT_CHECKS}, natural=True, style_fit=5)
    assert trial.select([draft], [review, review], '医药股有经营不好的') is None


def test_ten_cases_match_user_comparison_and_no_manual_answers_are_inputs():
    assert trial.CASE_INDICES == (0,1,2,3,4,5,6,7,10,11)
    source = Path(trial.__file__).read_text()
    assert 'reviewed_title=' not in source
    assert '127.0.0.1:11434/api/chat' in source


def test_style_preview_does_not_approve_publication():
    drafts = [dict(title='林园：医药股也有差公司，经营不好就别买', cover='医药股也要看经营好坏')]
    reviews = [dict(index=0, a_reason='这句话像本人口吻，但事实没有通过检查',
                    **{k:False for k in trial.FACT_CHECKS}, natural=True, style_fit=5,
                    style_scores={k:2 for k in trial.STYLE_DIMENSIONS})]
    assert trial.style_choice(drafts, reviews) == 0
    assert trial.select(drafts, reviews, '原文') is None
