"""Real keynote proposals omitted the actual meeting background."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import keynote_proposals as K


def actual():return json.loads((Path(__file__).parent/'fixtures/linyuan_actual_keynote_memory_context.json').read_text())


def test_actual_memory_clause_is_only_an_original_timed_alternative(monkeypatch):
    monkeypatch.setattr(K.editorial,'CONTENT_POLICY','reference_v1');monkeypatch.setattr(K.editorial,'MIN_SECONDS',20)
    f=actual();cues=f['cues'];before=deepcopy(cues);rows=K.windows(cues)[0]
    assert any(r.get('memory_context') and r['start']==f['memory_start'] for r in rows)
    assert any(r['start']<f['memory_start']<=r['end'] and '如果今天' in r['text'] for r in rows)
    prompt,schema=K.request(rows);props=schema['properties']['picks']['items']['properties']
    assert f['memory_start'] in props['start']['enum']
    assert f['rejected_parent']['start'] not in props['start']['enum']
    picks,rejected=K.validate(cues,rows,dict(picks=[dict(start=f['memory_start'],end=f['story_end'])]))
    assert not rejected and len(picks)==1 and cues==before
    p=picks[0];text=''.join(c['text'] for c in cues[p['start']:p['end']+1])
    assert text.startswith('我记得') and '线上的会议' in text and '媒体就报道' in text
    assert text.endswith('我说：那是提起风险。')
    assert '真正的投资' not in text and '没有删除条件' in prompt
    assert not any(k in p for k in ['passed','story_selection','title'])


@pytest.mark.parametrize('lead',['假设前两年，','假如前两年，','如果我当年前两年，','我编一个故事前两年，'])
def test_a_governing_fiction_or_past_hypothesis_cannot_be_cut_away(lead):
    cues=[dict(start=0,end=10,text=lead),dict(start=10,end=30,text='我记得两年前参加了一场会议。'),
        dict(start=30,end=55,text='我说投资要注意风险，后来媒体作了报道。')]
    assert not any(r.get('memory_context') for rows in K.windows(cues) for r in rows)


def test_no_midword_timestamp_or_generic_self_assessment_can_be_added():
    cues=[dict(start=0,end=10,text='前两年，我记得'),dict(start=10,end=30,text='自己当时想参加会议。')]
    assert not any(r.get('memory_context') for rows in K.windows(cues) for r in rows)
    assert not K.opening_allowed('那时候问我，我说要注意投资风险。')
    assert K.opening_allowed('我当年买了这家公司，后来一直持有。')
