import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
sys.path.insert(0,str(ROOT/'scripts'))
import transcript_audit as A
from check_linyuan_transcript_style_cpu import control_evidence_passed


def test_actual_age_objection_does_not_change_source_or_approve_other_errors():
    f=json.loads((ROOT/'tests/fixtures/linyuan_actual_age_fact_check_audit.json').read_text())
    text=''.join(c['text'] for c in f['cues'])
    assert A.text_digest(text)==f['actual_old_audit']['transcript_sha256']
    assert len(f['actual_old_audit']['issues'])==1
    assert f['actual_old_audit']['issues'][0]['suspect_quote']=='八十七岁'
    assert '无直接冲突' in f['actual_old_audit']['issues'][0]['reason']
    assert '未见专名错写、金额截断、单位不明或实际矛盾' in f['actual_old_audit']['explanation']
    assert not control_evidence_passed('actual_life_medical_age',f['actual_old_audit'],None)
    # The control can pass with a real different error STILL blocking production.
    proof=dict(passed=False,audio_verified=False,issues=[dict(suspect_quote='TCATCA')])
    assert control_evidence_passed('actual_life_medical_age',proof,None)
    assert proof['passed'] is False
