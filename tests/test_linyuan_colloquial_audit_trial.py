import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'linyuan'))
import transcript_audit as audit


def test_experimental_profile_never_reuses_production_or_returns_its_pass(tmp_path):
    path=tmp_path/'audit.json';calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        return json.dumps({'issues':[],'explanation':'仅文字合理性检查'})
    text='普通口语。'
    audit.review(text,'林园','cpu',call,path)
    audit.review(text,'林园','cpu',call,path,prompt_profile='colloquial-v1')
    audit.review(text,'林园','cpu',call,path)
    assert len(calls)==3 and 'prompt_profile' not in json.loads(path.read_text())
    assert '词语白名单' in calls[1] and '额外核对原则' not in calls[0]
    with pytest.raises(ValueError):audit.review(text,'林园','cpu',call,path,prompt_profile='waive-errors')


def test_trial_retains_bound_negative_issue_and_never_normalizes_text(tmp_path):
    text='我们凌源投资是这样做的。'
    reply={'issues':[dict(sentence_id=0,suspect_quote='凌源',kind='unintelligible_term',reason='人名错写')],
           'explanation':'需核对原音'}
    proof=audit.review(text,'林园','cpu',lambda *a:json.dumps(reply),tmp_path/'audit.json',
                       prompt_profile='colloquial-v1')
    assert not proof['passed'] and not proof['audio_verified']
    assert proof['issues'][0]['quote']==text and proof['transcript_sha256']==audit.text_digest(text)


def test_trial_labels_are_never_model_input_and_unavailable_is_not_pass(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('colloquial_trial',ROOT/'scripts/linyuan_colloquial_audit_trial.py')
    trial=importlib.util.module_from_spec(spec);spec.loader.exec_module(trial)
    monkeypatch.setattr(trial,'CASES',(('case','真实原句。',False,'expected label secret'),))
    calls=[]
    def unavailable(prompt,schema):
        calls.append(prompt)
        assert 'expected label secret' not in prompt and 'expected_pass' not in prompt
        raise TimeoutError('temporary')
    result=trial.run(unavailable,'cpu',tmp_path)
    assert len(calls)==2 and all(r['observed_pass'] is None and not r['matched'] for r in result['results'])
    assert not result['publication_allowed'] and result['model_evaluation_not_mother_yield']
