"""Experiments retain source binding and cannot mint production approvals."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import title_model_lab as lab


def test_cannot_skip_qualifier_or_invent_a_hook_quote():
    units=['我不是全部买，','只买一小部分，','其他的先观察。']
    item=dict(angle='个人选择',title='林园：我只买一小部分',hook_quote='只买一小部分，',evidence_ids=[1])
    assert lab.binding_errors(item,units)==[]
    assert 'evidence_must_be_continuous' in lab.binding_errors({**item,'evidence_ids':[0,2]},units)
    assert 'quote_not_exactly_bound' in lab.binding_errors({**item,'hook_quote':'我全部买'},units)
    assert lab.binding_errors({**item,'evidence_ids':[True]},units)==['invalid_evidence_ids']


def test_thinking_and_direct_are_distinct_experiments_on_same_new_model():
    a=lab.PROFILES['qwen35-9b-direct'];b=lab.PROFILES['qwen35-9b-thinking']
    assert a['model']==b['model']=='qwen3.5:9b'
    assert a['think'] is False and b['think'] is True
    assert len(set(lab.ANGLES))==6
    assert lab.digest(['不是','建议'])!=lab.digest(['不应','建议'])
