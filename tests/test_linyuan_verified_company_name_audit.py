"""A historical name reference informs review; it never suppresses an issue."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import transcript_audit as A


def test_primary_reference_is_present_only_for_its_exact_source_word(tmp_path):
    def call(prompt,schema):
        assert '郑州百文股份有限公司（集团）' in prompt and '不是原音核验' in prompt
        return json.dumps(dict(verdict='clear',issues=[],explanation='仅文字可理解性；未核验原音'))
    proof=A.review('我那时候持有长虹，后来提到郑州百文。','林园','synthetic-model',call,tmp_path/'one.json')
    assert proof['passed'] and proof['audio_verified'] is False and proof['entity_reference_sha256']
    def unrelated(prompt,schema):
        assert '郑州百文股份有限公司' not in prompt
        return json.dumps(dict(verdict='clear',issues=[],explanation='仅文字可理解性'))
    proof=A.review('我还是持有这家公司。','林园','synthetic-model',unrelated,tmp_path/'two.json')
    assert 'entity_reference_sha256' not in proof


def test_known_name_does_not_auto_waive_negative_or_other_garbled_evidence(tmp_path):
    text='我当年提到郑州百文和TCTC二。'
    def call(prompt,schema):
        return json.dumps(dict(verdict='needs_audio',issues=[dict(sentence_id=0,
            suspect_quote='TCTC二',kind='unintelligible_term',reason='公司缩写与汉字发生明显粘连，须复核')],
            explanation='存在具体文字识别疑点'))
    proof=A.review(text,'林园','synthetic-model',call,tmp_path/'blocked.json')
    assert not proof['passed'] and proof['issues'][0]['suspect_quote']=='TCTC二'
    assert proof['audio_verified'] is False


def test_reference_never_overrides_a_reported_source_bound_name_problem(tmp_path):
    def call(prompt,schema):
        return json.dumps(dict(verdict='needs_audio',issues=[dict(sentence_id=0,
            suspect_quote='郑州百文',kind='unintelligible_term',reason='前后句主体互相冲突，单凭名称存在不能核实本处原音')],
            explanation='保留实际上下文疑点，未核验原音'))
    proof=A.review('他说的公司是郑州百文。','林园','synthetic-model',call,tmp_path/'name.json')
    assert not proof['passed'] and proof['issues'][0]['suspect_quote']=='郑州百文'
