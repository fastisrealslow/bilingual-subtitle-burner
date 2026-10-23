"""Prior timeouts affect order, never semantic approval or source context."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'linyuan'))
import publishing_experiment as E
from story_review_progress import continuation_order


def fixture():
    cues=[dict(start=i*60,end=i*60+50,
        text=f'我当年买了这家公司。我后来去看过它的产品。这是第{i}个经历。') for i in range(6)]
    picks=[dict(start=i,end=i,editorial_rank=i) for i in range(6)]
    prior=dict(revision=E.SELECTION_REVISION,transcript_sha256=E.digest(cues),
        candidate_count=6,candidates=deepcopy(picks),retryable=True,selection_completed=False,
        unresolved=[dict(index=i,error='selection_budget_exhausted') for i in (4,5)],
        selected=[{**picks[0],'story_selection':{'invented':'not approval'}}])
    return cues,picks,prior


def order(cues,picks,prior):
    return continuation_order(picks,prior,transcript_sha256=E.digest(cues),revision=E.SELECTION_REVISION)


def test_unfinished_first_preserves_every_exact_candidate_and_source():
    cues,picks,prior=fixture();snapshot=deepcopy((cues,picks,prior))
    indices,proof=order(cues,picks,prior)
    assert indices==[4,5,0,1,2,3] and proof['applied']
    assert not proof['prior_approvals_reused'] and sorted(indices)==list(range(6))
    assert (cues,picks,prior)==snapshot


def test_reordered_picture_ranking_maps_by_exact_cue_spans_not_old_indices():
    cues,picks,prior=fixture();picks=picks[::-1]
    assert order(cues,picks,prior)[0]==[0,1,2,3,4,5]
    assert order(cues,picks,prior)[1]['pending_first']==[0,1]


@pytest.mark.parametrize('change',[
    lambda p:p.update(revision=0),lambda p:p.update(transcript_sha256='a'*64),
    lambda p:p.update(candidate_count=True),lambda p:p.update(candidate_count=5),
    lambda p:p.update(retryable=False),lambda p:p.update(selection_completed=True),
    lambda p:p.update(candidates=p['candidates'][:-1]),
    lambda p:p['candidates'][0].update(start=5),
    lambda p:p['candidates'][0].update(start=True),
    lambda p:p['candidates'].__setitem__(0,deepcopy(p['candidates'][1])),
    lambda p:p.update(unresolved=[dict(index=True,error='selection_budget_exhausted')]),
    lambda p:p.update(unresolved=[dict(index=99,error='selection_budget_exhausted')]),
    lambda p:p.update(unresolved=None),lambda p:p.pop('candidates'),
    lambda p:p.update(unresolved=[dict(index=4,error='invalid semantic evidence')]),
])
def test_stale_ambiguous_or_invalid_hint_is_a_normal_cold_start(change):
    cues,picks,prior=fixture();change(prior)
    indices,proof=order(cues,picks,prior)
    assert indices==list(range(6)) and not proof['applied']


@pytest.mark.parametrize('value',[None,[],True,{},'bad'])
def test_missing_progress_does_not_fail_new_source(value):
    cues,picks,_=fixture();assert order(cues,picks,value)[0]==list(range(6))


def test_actual_calls_recheck_old_positives_and_remap_indices(tmp_path):
    cues,picks,prior=fixture();(tmp_path/'story_review_progress.json').write_text(json.dumps(prior))
    calls=[]
    def call(prompt,schema):
        calls.append(prompt)
        # All previous positives can now be rejected. No cache approval leaks.
        return {k:dict(kind='skip',subject_sentence=0,event_sentence=0,detail_sentence=1)
                for k in schema['properties']}
    result=E.choose_stories(cues,picks,call,tmp_path/'selection.json')
    assert result['review_order']==[4,5,0,1,2,3]
    assert result['calls'][0]['indices']==[4,5]
    assert result['selection_completed'] and not result['selected']
    assert sorted(r['index'] for r in result['rejected'])==list(range(6))
    assert len(calls)==3


def test_new_budget_remains_bounded_without_silently_discarding_rest(tmp_path,monkeypatch):
    cues,picks,prior=fixture();(tmp_path/'story_review_progress.json').write_text(json.dumps(prior))
    clock=[0];monkeypatch.setattr(E.time,'monotonic',lambda:clock[0])
    def call(prompt,schema):
        clock[0]=2
        return {k:dict(kind='skip',subject_sentence=0,event_sentence=0,detail_sentence=1)
                for k in schema['properties']}
    result=E.choose_stories(cues,picks,call,tmp_path/'selection.json',budget_sec=1)
    assert [r['index'] for r in result['rejected']]==[4,5]
    assert [r['index'] for r in result['unresolved']]==[0,1,2,3]
    assert result['retryable'] and not result['selection_completed']


def test_malformed_progress_file_does_not_block_fresh_review(tmp_path):
    cues,picks,_=fixture();(tmp_path/'story_review_progress.json').write_text('{broken')
    call=lambda p,s:{k:dict(kind='skip',subject_sentence=0,event_sentence=0,detail_sentence=1)
                       for k in s['properties']}
    result=E.choose_stories(cues,picks,call,tmp_path/'selection.json')
    assert result['review_order']==list(range(6)) and result['selection_completed']


@pytest.mark.parametrize('titles_only',[False,True])
def test_restore_only_copies_scheduling_hint_not_current_semantic_proof(tmp_path,monkeypatch,titles_only):
    import restore_production_evidence as R
    import mother_asr_cache
    evidence=tmp_path/'old';work=tmp_path/'new';evidence.mkdir();work.mkdir()
    source=dict(passed=True,source_sha256='a'*64)
    for directory in (evidence,work):
        (directory/'source_quality.json').write_text(json.dumps(source))
    _,_,prior=fixture();(evidence/'story_selection.json').write_text(json.dumps(prior))
    monkeypatch.setattr(mother_asr_cache,'transfer',lambda *a:True)
    R.restore(evidence,work,titles_only=titles_only)
    assert (work/'story_review_progress.json').exists() is not titles_only
    assert not (work/'story_selection.json').exists()


def test_wrong_raw_source_cannot_import_progress(tmp_path):
    import restore_production_evidence as R
    evidence=tmp_path/'old';work=tmp_path/'new';evidence.mkdir();work.mkdir()
    for directory,sha in ((evidence,'a'*64),(work,'b'*64)):
        (directory/'source_quality.json').write_text(json.dumps(dict(passed=True,source_sha256=sha)))
    (evidence/'story_selection.json').write_text('{}')
    with pytest.raises(ValueError,match='哈希不一致'):R.restore(evidence,work)
    assert not (work/'story_review_progress.json').exists()
