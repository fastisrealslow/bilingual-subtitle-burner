import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import qwen_style_v3 as trial


def test_direct_comparison_has_no_implicit_first_candidate_fallback():
    result = dict(ranking=[2, 0, 1], reasons=[dict(index=i, reason='具体比较理由充分') for i in range(3)])
    assert trial.choose(result) == 2
    for ranking in ([0, 0, 1], [2, 1], [True, 0, 2]):
        with pytest.raises(ValueError):
            trial.choose(dict(result, ranking=ranking))
    with pytest.raises(ValueError):
        trial.choose(dict(result, reasons=result['reasons'][:2]))


def test_false_or_fabricated_evidence_cannot_approve_output():
    draft = dict(title='林园：经营不好的公司，我不会买', cover='经营不好的公司不能买')
    audit = dict(reason='原文明确谈公司经营', evidence=['经营不好的公司不能买'], issues=[],
                 **{k: True for k in trial.CHECKS})
    cues = ['经营不好的公司', '不能买']
    assert trial.audit_ok(audit, draft, cues)
    assert not trial.audit_ok(dict(audit, speaker_correct=False), draft, cues)
    assert not trial.audit_ok(dict(audit, evidence=['经营不好必然亏损']), draft, cues)
    assert trial.validation_issues(dict(audit, evidence=['经营不好必然亏损']), draft, cues)
    assert trial.validation_issues(audit, draft, cues) == []
    assert not trial.audit_ok(dict(audit, issues=['还存在一个问题']), draft, cues)
    assert not trial.audit_ok(audit, dict(draft, cover='林园：经营不好不能买'), cues)


def test_second_audit_is_required_for_repaired_title():
    draft = dict(title='林园：经营不好的公司，我不会买', cover='经营不好的公司不能买')
    failed = dict(evidence=['经营不好的公司不能买'], issues=[], **{k: True for k in trial.CHECKS})
    failed['source_supported'] = False
    assert not trial.audit_ok(failed, draft, ['经营不好的公司不能买'])


def test_summary_keeps_missing_and_rejected_trials_in_denominator(tmp_path):
    import json
    from summarize_qwen_style_v3 import summarize
    for repeat,status in [(1,'accepted_initial'),(2,'rejected')]:
        (tmp_path/f'case-0-repeat-{repeat}.json').write_text(json.dumps(dict(
            case_number=1, repeat=repeat, status=status,
            final_candidate={'title':'示例'} if repeat==1 else None)))
    result = summarize(tmp_path)
    assert result['expected'] == 30
    assert len(result['rows']) == 30
    assert result['machine_accepted'] == 1
    assert result['all_three_accepted_cases'] == 0
    assert result['rows'][2]['status'] == 'missing'
